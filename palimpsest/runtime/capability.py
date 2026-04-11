"""Capability model implementation per ADR-0016.

Capability is a runtime service management unit with setup/finalize lifecycle.
Each capability:
- Has a name for role declaration
- setup(ctx) returns events, runtime emits them
- finalize(ctx) returns FinalizeResult(events, success), runtime decides job state
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable, Any

from loguru import logger

from yoitsu_contracts import FinalizeResult, EventData, AnalyzerVersion, TargetSource


@runtime_checkable
class Capability(Protocol):
    """Capability protocol per ADR-0016.
    
    Each capability manages a runtime service with setup/finalize lifecycle.
    """
    name: str
    
    def setup(self, ctx: JobContext) -> list[EventData]:
        """Setup the capability. Returns event data for runtime to emit.
        
        Setup failure = job failure (preparation failure path).
        """
        ...
    
    def finalize(self, ctx: JobContext) -> FinalizeResult:
        """Finalize the capability. Returns (events, success).
        
        Must NOT raise exceptions. All retry logic is inside finalize.
        Runtime uses success flag to determine job terminal state.
        """
        ...


@dataclass
class JobContext:
    """Context passed to capability setup/finalize.
    
    Provides access to:
    - Job configuration
    - Workspaces (bundle and target)
    - Resources dict for capability-shared state
    - analyzer_version for observation emission (ADR-0017)
    - role_type for capability behavior differentiation (ADR-0016)
    """
    job_id: str
    task_id: str
    bundle: str
    role: str
    goal: str
    bundle_workspace: str = ""
    target_workspace: str = ""
    resources: dict[str, Any] = field(default_factory=dict)
    analyzer_version: AnalyzerVersion | None = None  # ADR-0017
    target_source: TargetSource | None = None  # For artifact URI construction
    role_type: str = "worker"  # For hallucination gate behavior (worker vs planner/evaluator)


# === Built-in Capabilities ===

class GitWorkspaceCapability:
    """Git workspace management capability.
    
    Handles:
    - Clone target repo (setup)
    - Commit + push (finalize)
    - Hallucination gate (no changes = success=False for worker)
    """
    name = "git_workspace"
    
    MAX_RETRIES = 3
    
    def setup(self, ctx: JobContext) -> list[EventData]:
        """Setup is handled by Trenni (workspace materialization).
        
        This capability's setup is a no-op for now.
        Future: could handle input artifact materialization.
        """
        return [EventData(type="git_workspace.ready", data={
            "workspace": ctx.target_workspace
        })]
    
    def finalize(self, ctx: JobContext) -> FinalizeResult:
        """Commit and push changes to target repo.
        
        Implements ADR-0015 push strategy:
        - Hallucination gate: no changes = success=False (worker role)
        - Sync push with retry
        - Artifact URI points to remote repo, not ephemeral workspace
        """
        import subprocess
        
        events = []
        success = True
        
        if not ctx.target_workspace:
            # Repoless task: skip
            events.append(EventData(type="publication.skipped", data={
                "reason": "no_target_workspace"
            }))
            return FinalizeResult(events=events, success=True)
        
        workspace = Path(ctx.target_workspace)
        
        # Hallucination gate
        subprocess.run(["git", "add", "-A"], cwd=workspace, check=False)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=workspace,
            capture_output=True
        )
        
        if result.returncode == 0:
            # No changes
            # Worker role: hallucination = success=False
            # Planner/Evaluator: expected, success=True
            is_worker = ctx.role_type == "worker"
            events.append(EventData(type="publication.skipped", data={
                "reason": "no_changes",
                "workspace": str(workspace),
                "role_type": ctx.role_type,
            }))
            # Worker without changes = hallucination = failure
            # Non-worker (planner/evaluator) without changes = expected = success
            return FinalizeResult(events=events, success=not is_worker)
        
        # Commit
        sha_before = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=workspace
        ).decode().strip()
        
        try:
            subprocess.run(
                ["git", "commit", "-m", f"job: {ctx.job_id}"],
                cwd=workspace,
                check=True,
                capture_output=True
            )
        except subprocess.CalledProcessError as e:
            events.append(EventData(type="finalize.failed", data={
                "capability": self.name,
                "stage": "commit",
                "error": e.stderr.decode() if e.stderr else str(e),
                "artifact_persisted": False
            }))
            return FinalizeResult(events=events, success=False)
        
        sha_after = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=workspace
        ).decode().strip()
        
        # Push with retry
        for attempt in range(self.MAX_RETRIES):
            try:
                subprocess.run(
                    ["git", "push"],
                    cwd=workspace,
                    check=True,
                    capture_output=True
                )
                # Success: artifact URI points to remote repo, not ephemeral workspace
                repo_uri = ctx.target_source.repo_uri if ctx.target_source else ""
                artifact_ref = f"{repo_uri}@{sha_after}" if repo_uri else f"git_commit:{sha_after}"
                events.append(EventData(type="artifact.published", data={
                    "ref": artifact_ref,
                    "relation": "workspace_output",
                    "workspace": str(workspace)
                }))
                return FinalizeResult(events=events, success=True)
            except subprocess.CalledProcessError as e:
                if attempt < self.MAX_RETRIES - 1:
                    logger.warning(f"Push failed (attempt {attempt + 1}), retrying...")
                    continue
                # Final failure
                events.append(EventData(type="finalize.failed", data={
                    "capability": self.name,
                    "stage": "push",
                    "error": e.stderr.decode() if e.stderr else str(e),
                    "local_commit_sha": sha_after,
                    "artifact_persisted": False,
                    "retry_possible": True
                }))
                success = False
        
        return FinalizeResult(events=events, success=success)


class CleanupCapability:
    """Cleanup capability for workspace/resource cleanup.
    
    Handles cleanup of resources. Failure does not affect job state
    (cleanup is not critical for artifact persistence).
    """
    name = "cleanup"
    
    def setup(self, ctx: JobContext) -> list[EventData]:
        """No setup needed."""
        return []
    
    def finalize(self, ctx: JobContext) -> FinalizeResult:
        """Cleanup workspace and resources.
        
        Cleanup failure does not affect success (artifact already persisted).
        """
        import shutil
        
        events = []
        
        # Cleanup target workspace
        if ctx.target_workspace:
            try:
                shutil.rmtree(ctx.target_workspace)
                events.append(EventData(type="cleanup.completed", data={
                    "workspace": ctx.target_workspace
                }))
            except Exception as e:
                # Cleanup failure is non-critical
                events.append(EventData(type="cleanup.failed", data={
                    "workspace": ctx.target_workspace,
                    "error": str(e)
                }))
        
        # Cleanup resources
        for name, resource in ctx.resources.items():
            if hasattr(resource, "close"):
                try:
                    resource.close()
                    events.append(EventData(type="resource.closed", data={
                        "name": name
                    }))
                except Exception as e:
                    events.append(EventData(type="cleanup.failed", data={
                        "resource": name,
                        "error": str(e)
                    }))
        
        # Cleanup always returns success=True (non-critical)
        return FinalizeResult(events=events, success=True)


# === Capability Registry ===

BUILTIN_CAPABILITIES: dict[str, Capability] = {
    "git_workspace": GitWorkspaceCapability(),
    "cleanup": CleanupCapability(),
}


def get_capability(name: str) -> Capability | None:
    """Get a capability by name from registry."""
    return BUILTIN_CAPABILITIES.get(name)
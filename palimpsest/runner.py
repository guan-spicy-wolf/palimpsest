"""Four-stage pipeline orchestrator.

The runner is part of the Runtime (skeleton) — it is immutable and not
subject to Agent self-evolution.  It orchestrates:

  1. Workspace setup (clone repo, create job branch)
  2. Context building (from the resolved JobSpec)
  3. Interaction loop (LLM calls + tool execution)
  4. Publication (git commit + push + completion event)

Stage-level events (transitions, job-started, cleanup issues) are emitted
by the stage functions themselves.  The runner only emits job-lifecycle
events (completed / failed) and orchestration-level events.
"""

from __future__ import annotations

import json
import signal
import traceback
from pathlib import Path

import git
from loguru import logger

from palimpsest.config import JobConfig
from palimpsest.emitter import EventEmitter
from palimpsest.events import (
    JobCompletedData,
    JobFailedData,
    RuntimeIssueData,
)
from palimpsest.runtime import (
    EventGateway,
    UnifiedLLMGateway,
    RoleManager,
    UnifiedToolGateway,
)
from palimpsest.runtime.roles import JobSpec
from palimpsest.stages import (
    build_context,
    finalize_workspace_after_job,
    find_publication_issues,
    publish_results,
    run_interaction_loop,
    setup_workspace,
)

# The evolvable repo is always located at <project_root>/evo.
# This is a structural constant, not a per-job configuration.
_EVO_DIR = "evo"


class ControlledJobFailure(Exception):
    """Runtime-detected job failure that should not produce a traceback."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


def run_job(config: JobConfig) -> None:
    """Resolve the role into a JobSpec and execute the four-stage pipeline."""
    evo_path = Path.cwd() / _EVO_DIR

    resolver = RoleManager(evo_path)
    spec = resolver.resolve(config.role)

    logger.info(
        f"Resolved role '{config.role}' -> JobSpec "
        f"(source_role={spec.source_role!r}, tools={spec.tools})"
    )

    _run_job_from_spec(config, spec, evo_path)


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------

def _run_job_from_spec(
    config: JobConfig, spec: JobSpec, evo_path: Path
) -> None:
    job_id = config.job_id
    if not job_id:
        raise ValueError("Job ID must be specified in the configuration.")

    emitter = EventEmitter(config.eventstore)
    task_id = config.task_id or job_id
    gateway = EventGateway(emitter, job_id, task_id)

    evo_sha = _read_evo_sha(evo_path)
    logger.info(f"Starting job {job_id} (evo={evo_sha[:8] if evo_sha else '?'})")

    _install_timeout(config.timeout)

    workspace: str | None = None
    try:
        # Stage 1: Workspace (emits stage-transition + job-started internally)
        workspace = setup_workspace(
            job_id,
            config.workspace,
            config.publication.branch_prefix,
            gateway=gateway,
            evo_sha=evo_sha,
        )

        # Capture base SHA before any agent modifications (used by guardrails).
        try:
            base_sha = git.Repo(workspace).head.commit.hexsha
        except Exception:
            base_sha = ""

        # Stage 2: Context (emits stage-transition internally)
        context = build_context(
            job_id, workspace, config.task, spec, config, gateway, evo_root=evo_path,
        )

        # Stage 3+4: Interaction and publication
        tools = _setup_tools(config, spec, evo_path, gateway)
        llm = _setup_llm(config, gateway)
        result, git_ref, artifact_info = _stage_interaction_and_publication(
            job_id, context, workspace, config, spec, gateway, tools, llm,
            base_sha=base_sha,
        )

        # Emit structured completion event
        gateway.emit(
            JobCompletedData(
                git_ref=git_ref,
                summary=result.get("summary", ""),
                status=str(result.get("status", "complete") or "complete"),
                code=str(result.get("code", "") or ""),
                artifact_path=artifact_info.get("artifact_path"),
                publication_mode=artifact_info.get("publication_mode", "branch_only"),
                trust_level=artifact_info.get("trust_level", "automated"),
                requires_review=artifact_info.get("requires_review", False),
                files_changed=artifact_info.get("files_changed", []),
            )
        )
        logger.info(f"Job {job_id} completed")

    except ControlledJobFailure as exc:
        error_msg = str(exc)
        logger.error(f"Job {job_id} failed: {error_msg}")
        gateway.emit(
            JobFailedData(error=error_msg, code=exc.code)
        )
        raise

    except _JobTimeout:
        logger.error(f"Job {job_id} timed out ({config.timeout}s)")
        gateway.emit(
            JobFailedData(
                error=f"Job timed out after {config.timeout}s",
                code="timeout",
            )
        )
        raise ControlledJobFailure(
            f"Job timed out after {config.timeout}s", code="timeout"
        )

    except Exception as exc:
        error_msg = str(exc)
        tb_str = traceback.format_exc()
        logger.exception(f"Job {job_id} failed")
        gateway.emit(
            JobFailedData(error=error_msg, traceback=tb_str)
        )
        raise

    finally:
        if workspace:
            finalize_workspace_after_job(workspace, gateway=gateway)
        gateway.close()


# ---------------------------------------------------------------------------
# Internal helpers (orchestration logic, not independent stages)
# ---------------------------------------------------------------------------

def _setup_tools(
    config: JobConfig,
    spec: JobSpec,
    evo_path: Path,
    gateway: EventGateway,
) -> UnifiedToolGateway:
    """Create the unified tool gateway from builtin + evo providers."""
    return UnifiedToolGateway(config.tools, evo_path, spec.tools, gateway)


def _setup_llm(config: JobConfig, gateway: EventGateway) -> UnifiedLLMGateway:
    """Create the LLM gateway with configuration."""
    return UnifiedLLMGateway(config.llm, gateway)


def _determine_trust_level(mode: str) -> str:
    """Determine the trust level based on publication mode."""
    if mode == "approval_required":
        return "human_required"
    elif mode == "pr_draft":
        return "review_suggested"
    elif mode == "branch_only":
        return "automated"
    return "unknown"


def _stage_interaction_and_publication(
    job_id: str,
    context: dict,
    workspace: str,
    config: JobConfig,
    spec: JobSpec,
    gateway: EventGateway,
    tools: UnifiedToolGateway,
    llm: UnifiedLLMGateway,
    *,
    base_sha: str = "",
) -> tuple[dict, str | None, dict]:
    """Stage 3+4: interaction loop with publication recovery. Returns (result, git_ref, artifact_info)."""
    from palimpsest.events import StageTransitionData
    gateway.emit(StageTransitionData(from_stage="context", to_stage="interaction"))

    interaction_messages: list[dict] | None = None
    publication_recovery_attempts = 0
    pending_user_prompt: str | None = None
    max_recovery_attempts = max(0, config.publication.max_recovery_attempts)

    while True:
        result = run_interaction_loop(
            job_id,
            context,
            workspace,
            llm,
            tools,
            messages=interaction_messages,
            user_prompt=pending_user_prompt,
        )
        interaction_messages = result["messages"]
        pending_user_prompt = None

        should_publish = bool(config.workspace.repo) and config.publication.strategy != "skip"
        if not should_publish:
            # Still create artifact for non-published results
            artifact_info = _create_artifact_info(None, result, config.publication)
            return result, None, artifact_info

        # Publication guardrails
        gateway.emit(StageTransitionData(from_stage="interaction", to_stage="publication"))
        issues = find_publication_issues(git.Repo(workspace), base_sha=base_sha)
        if issues:
            can_retry = publication_recovery_attempts < max_recovery_attempts
            gateway.emit(
                RuntimeIssueData(
                    stage="publication",
                    fatal=not can_retry,
                    code="publication_guardrail",
                    violations=issues,
                )
            )
            if not can_retry:
                raise ControlledJobFailure(
                    "Publication guardrails triggered:\n- " + "\n- ".join(issues),
                    code="publication_guardrail",
                )

            publication_recovery_attempts += 1
            gateway.emit(StageTransitionData(from_stage="publication", to_stage="interaction"))
            pending_user_prompt = (
                "Publication was blocked by runtime guardrails.\n"
                "Issues:\n- " + "\n- ".join(issues) + "\n"
                "Please fix the workspace state, continue using tools if needed, "
                "and stop calling tools when the job is actually done."
            )
            continue

        git_ref = publish_results(
            job_id,
            result,
            workspace,
            config.publication,
            git_token_env=config.workspace.git_token_env,
        )
        
        # Read artifact info from the completion artifact
        artifact_info = _read_artifact_info(workspace, git_ref, result, config.publication)
        return result, git_ref, artifact_info


def _create_artifact_info(
    artifact_path: str | None,
    result: dict,
    publication_config,
) -> dict:
    """Create artifact info dict for the completion event."""
    mode = getattr(publication_config, "mode", "branch_only")
    return {
        "artifact_path": artifact_path,
        "publication_mode": mode,
        "trust_level": _determine_trust_level(mode),
        "requires_review": mode in ("pr_draft", "approval_required"),
        "files_changed": [],
    }


def _read_artifact_info(
    workspace: str,
    git_ref: str | None,
    result: dict,
    publication_config,
) -> dict:
    """Read artifact info from the completion artifact file."""
    mode = getattr(publication_config, "mode", "branch_only")
    artifact_path = Path(workspace) / ".palimpsest" / "completion.json"
    
    artifact_info = _create_artifact_info(
        str(artifact_path) if artifact_path.exists() else None,
        result,
        publication_config,
    )
    
    if artifact_path.exists():
        try:
            artifact_data = json.loads(artifact_path.read_text())
            artifact_info["files_changed"] = artifact_data.get("artifacts", {}).get("files_changed", [])
        except Exception as e:
            logger.debug(f"Could not read completion artifact: {e}")
    
    return artifact_info


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------

class _JobTimeout(Exception):
    """Raised by the SIGALRM handler when the job wall-clock timeout expires."""


def _timeout_handler(signum, frame):
    raise _JobTimeout()


def _install_timeout(seconds: int) -> None:
    """Arm a SIGALRM-based wall-clock timeout. 0 means no limit."""
    if seconds <= 0:
        return
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(seconds)


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def _read_evo_sha(evo_path: Path) -> str:
    """Return the HEAD SHA of the evolvable repo, or empty string."""
    try:
        return git.Repo(evo_path).head.commit.hexsha
    except Exception:
        logger.debug("Could not read evolvable repo HEAD")
        return ""

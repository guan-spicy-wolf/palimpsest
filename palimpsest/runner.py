"""Four-stage pipeline orchestrator.

The runner is part of the Runtime (skeleton) — it is immutable and not
subject to Agent self-evolution.  It orchestrates:

  1. Preparation  (workspace setup, artifact materialization, resource init)
  2. Context      (system prompt, goal, tools, event-derived context)
  3. Interaction  (LLM calls + tool execution loop)
  4. Publication   (artifact store + optional git push + completion event)

Architecture note: Why are the stages fixed and not event-driven?
During the runtime redesign exploration (2026-04), an alternative was
considered where stages could be selected dynamically via an event-driven
kernel. This was rejected because the four stages form a causal dependency
chain: context cannot be built without a workspace, interaction cannot
start without context, publication cannot happen without interaction
output. An event-driven selector that always walks 1→2→3→4 adds overhead
without flexibility. Variation between task types belongs in stage
*implementations* (different preparation_fn, different publication_fn),
not in stage *topology*.

Stage-level events (transitions, job-started, cleanup issues) are emitted
by the stage functions themselves.  The runner only emits job-lifecycle
events (completed / failed) and orchestration-level events.
"""

from __future__ import annotations

import inspect
import io
import signal
import subprocess
import sys
import tarfile
import tempfile
import traceback
from contextlib import contextmanager
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
    RuntimeContext,
    UnifiedLLMGateway,
    RoleManager,
    UnifiedToolGateway,
)
from palimpsest.runtime.capability import JobContext, get_capability, BUILTIN_CAPABILITIES
from palimpsest.runtime.roles import JobSpec
from palimpsest.stages import (
    build_context,
    finalize_workspace_after_job,
    PublicationGuardrailViolation,
    run_interaction_loop,
    run_preparation,  # ADR-0009: canonical name
    setup_workspace,  # Backward compatibility alias
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
    """Resolve the role into a JobSpec and execute the four-stage pipeline.
    
    Per ADR-0015: bundle workspace is for code loading, target workspace for execution.
    Trenni clones repos, Palimpsest just uses the paths.
    """
    # ADR-0015: Use bundle_source/target_source from config (Trenni prepared)
    bundle_workspace = ""
    target_workspace = ""
    
    if config.bundle_source:
        bundle_workspace = config.bundle_source.workspace
    if config.target_source:
        target_workspace = config.target_source.workspace
    
    # Backward compat: if no bundle_source, use evo_sha to find evo
    evo_path = Path(bundle_workspace) if bundle_workspace else Path(_EVO_DIR)
    
    resolver = RoleManager(evo_path, bundle=config.bundle)
    spec = resolver.resolve(config.role, **dict(config.role_params or {}))

    logger.info(
        f"Resolved role '{config.role}' -> JobSpec "
        f"(source_role={spec.source_role!r}, tools={spec.tools})"
    )

    # Get role metadata for needs
    role_meta = resolver.get_definition(config.role)
    needs = role_meta.needs if role_meta else []
    
    _run_job_from_spec(
        config, spec, evo_path, 
        bundle_workspace=bundle_workspace,
        target_workspace=target_workspace,
        needs=needs,
    )


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------

def _run_job_from_spec(
    config: JobConfig,
    spec: JobSpec,
    evo_path: Path,
    *,
    bundle_workspace: str = "",
    target_workspace: str = "",
    needs: list[str] = [],
) -> None:
    """Execute the four-stage pipeline with capability model (ADR-0016).
    
    Per ADR-0016: capability provides setup/finalize lifecycle.
    - setup() returns events, runtime emits
    - finalize() returns FinalizeResult(events, success)
    - success=False → job.failed
    
    Backward compat: if needs=[], use old preparation/publication fn.
    """
    job_id = config.job_id
    if not job_id:
        raise ValueError("Job ID must be specified in the configuration.")

    emitter = EventEmitter(config.eventstore)
    task_id = config.task_id or job_id
    gateway = EventGateway(emitter, job_id, task_id)

    logger.info(f"Starting job {job_id} (bundle={config.bundle}, needs={needs})")

    # ADR-0015: Make bundle workspace importable for role/tool/context modules
    if bundle_workspace and bundle_workspace not in sys.path:
        sys.path.insert(0, bundle_workspace)

    _install_timeout(config.timeout)

    # Create RuntimeContext at job start (ADR-0011 D6)
    runtime_context = RuntimeContext(
        job_id=job_id,
        task_id=task_id,
        bundle=config.bundle,
        role=config.role,
    )

    # Create JobContext for capabilities (ADR-0016)
    cap_ctx = JobContext(
        job_id=job_id,
        task_id=task_id,
        bundle=config.bundle,
        role=config.role,
        goal=config.goal,
        bundle_workspace=bundle_workspace,
        target_workspace=target_workspace,
        resources={},  # Populated by capabilities
    )

    workspace: str | None = None
    llm = _setup_llm(config, gateway)
    cost_tracking_degraded = llm.cost_tracking_degraded()
    all_success = True  # Track capability finalize success
    
    try:
        role_params = dict(config.role_params or {})
        
        # ADR-0016: Stage 1 - Capability setup
        if needs:
            for cap_name in needs:
                cap = get_capability(cap_name)
                if cap:
                    try:
                        events = cap.setup(cap_ctx)
                        for evt in events:
                            gateway.emit(evt)
                    except Exception as e:
                        logger.error(f"Capability {cap_name} setup failed: {e}")
                        gateway.emit(
                            RuntimeIssueData(
                                stage="setup",
                                fatal=True,
                                code=f"{cap_name}_setup_failed",
                                error=str(e),
                            )
                        )
                        raise ControlledJobFailure(
                            f"Capability {cap_name} setup failed: {e}",
                            code="capability_setup",
                        )
            workspace = target_workspace  # Use Trenni-prepared workspace
        else:
            # Backward compat: use old preparation_fn
            branch_prefix = str(
                role_params.get("branch_prefix")
                or getattr(spec.publication_fn, "__publication_branch_prefix__", config.publication.branch_prefix)
            )
            prep_params = {
                "goal": config.goal,
                "repo": config.workspace.repo,
                "init_branch": config.workspace.init_branch,
                **role_params,
            }
            prep_sig = inspect.signature(spec.preparation_fn)
            if "runtime_context" in prep_sig.parameters:
                prep_params["runtime_context"] = runtime_context
            if "evo_root" in prep_sig.parameters:
                prep_params["evo_root"] = str(evo_path)
            workspace_cfg = spec.preparation_fn(**prep_params)
            workspace = setup_workspace(
                job_id,
                workspace_cfg,
                branch_prefix,
                task_id=config.task_id or job_id,
                goal=config.goal,
                gateway=gateway,
                cost_tracking_degraded=cost_tracking_degraded,
            )
            cap_ctx.target_workspace = workspace

        # ADR-0011: set workspace_path after workspace setup
        runtime_context.workspace_path = workspace

        # Capture base SHA before agent modifications
        try:
            base_sha = git.Repo(workspace).head.commit.hexsha
        except Exception:
            base_sha = ""

        # Stage 2: Context (for LLM prompt, not a capability)
        context_spec = spec.context_fn(
            workspace=workspace,
            job_id=job_id,
            goal=config.goal,
            job_config=config,
            evo_root=str(evo_path),
            **role_params,
        )
        context = build_context(
            job_id, workspace, config.goal, context_spec, config, gateway, evo_root=evo_path,
        )

        # Stage 3: Interaction
        tools = _setup_tools(config, spec, evo_path, "", gateway, config.bundle)
        
        if needs:
            # ADR-0016: capability path - no publication_fn, interaction only
            result = run_interaction_loop(
                job_id, context, workspace, llm, tools,
                runtime_context=runtime_context,
            )
            git_ref = ""
        else:
            # Backward compat: use old _stage_interaction_and_publication
            result, git_ref = _stage_interaction_and_publication(
                job_id, context, workspace, config, spec, gateway, tools, llm,
                base_sha=base_sha,
                role_params=role_params,
                runtime_context=runtime_context,
            )
        
        # ADR-0016: Stage 4 - Capability finalize
        if needs:
            for cap_name in needs:
                cap = get_capability(cap_name)
                if cap:
                    try:
                        finalize_result = cap.finalize(cap_ctx)
                        for evt in finalize_result.events:
                            gateway.emit(evt)
                        if not finalize_result.success:
                            all_success = False
                            logger.warning(f"Capability {cap_name} finalize returned success=False")
                    except Exception as e:
                        logger.error(f"Capability {cap_name} finalize failed: {e}")
                        gateway.emit(
                            RuntimeIssueData(
                                stage="finalize",
                                fatal=True,
                                code=f"{cap_name}_finalize_failed",
                                error=str(e),
                            )
                        )
                        all_success = False
            # git_ref from capability finalize (e.g., GitWorkspaceCapability)
        else:
            # Backward compat: no needs, old behavior
            # git_ref is data field, not success indicator
            # _stage_interaction_and_publication handles guardrail retry
            all_success = True  # Old path: always complete (exceptions handled above)

        # Emit completion
        if all_success:
            gateway.emit(
                JobCompletedData(
                    git_ref=git_ref,
                    summary=result.get("summary", ""),
                    status=str(result.get("status", "complete") or "complete"),
                    code=str(result.get("code", "") or ""),
                    budget_dim=str(result.get("budget_dim", "") or ""),
                    cost_tracking_degraded=cost_tracking_degraded,
                    cost=llm.total_cost,
                    artifact_bindings=result.get("artifact_bindings", []),
                    tool_call_history=result.get("tool_call_history", []),
                )
            )
            logger.info(f"Job {job_id} completed")
        else:
            gateway.emit(
                JobFailedData(
                    error="Capability finalize returned success=False",
                    code="finalize_failed",
                )
            )
            logger.warning(f"Job {job_id} failed (finalize success=False)")

    except ControlledJobFailure as exc:
        error_msg = str(exc)
        logger.error(f"Job {job_id} failed: {error_msg}")
        gateway.emit(JobFailedData(error=error_msg, code=exc.code))
        raise

    except _JobTimeout:
        logger.error(f"Job {job_id} timed out ({config.timeout}s)")
        gateway.emit(
            JobFailedData(
                error=f"Job timed out after {config.timeout}s",
                code="timeout",
            )
        )
        raise ControlledJobFailure(f"Job timed out after {config.timeout}s", code="timeout")

    except Exception as exc:
        error_msg = str(exc)
        tb_str = traceback.format_exc()
        logger.exception(f"Job {job_id} failed")
        gateway.emit(JobFailedData(error=error_msg, traceback=tb_str))
        raise

    finally:
        if 'runtime_context' in locals():
            runtime_context.cleanup()
        if workspace and not needs:  # Old cleanup for backward compat path
            finalize_workspace_after_job(workspace, gateway=gateway)
        gateway.close()


# ---------------------------------------------------------------------------
# Internal helpers (orchestration logic, not independent stages)
# ---------------------------------------------------------------------------

def _setup_tools(
    config: JobConfig,
    spec: JobSpec,
    evo_path: Path,
    evo_sha: str,
    gateway: EventGateway,
    bundle: str,
) -> UnifiedToolGateway:
    """Create the unified tool gateway from builtin + evo providers."""
    return UnifiedToolGateway(
        config.tools,
        evo_path,
        bundle,
        spec.tools,
        gateway,
        evo_sha=evo_sha,
        tool_timeout_seconds=config.llm.tool_timeout_seconds,
    )


def _setup_llm(config: JobConfig, gateway: EventGateway) -> UnifiedLLMGateway:
    """Create the LLM gateway with configuration."""
    return UnifiedLLMGateway(config.llm, gateway)


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
    role_params: dict[str, object] | None = None,
    runtime_context: RuntimeContext | None = None,
) -> tuple[dict, str]:
    """Stage 3+4: interaction loop with publication recovery. Returns (result, git_ref)."""
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
            runtime_context=runtime_context,  # ADR-0011: pass to tools via injection
        )
        interaction_messages = result["messages"]
        pending_user_prompt = None

        publication_strategy = str(
            (role_params or {}).get("publication_strategy")
            or getattr(spec.publication_fn, "__publication_strategy__", "branch")
        )
        should_publish = publication_strategy != "skip"
        if not should_publish:
            # No publication: return result directly
            # Tool repetition analysis will be done by Trenni post-job (ADR-0017)
            return result, None

        gateway.emit(StageTransitionData(from_stage="interaction", to_stage="publication"))
        try:
            publication_params = dict(role_params or {})
            for reserved_key in ("result", "workspace_path", "job_id", "task_id", "goal", "git_token_env", "base_sha", "runtime_context"):
                publication_params.pop(reserved_key, None)
            # ADR-0011: pass runtime_context if publication_fn accepts it
            pub_sig = inspect.signature(spec.publication_fn)
            if "runtime_context" in pub_sig.parameters and runtime_context is not None:
                publication_params["runtime_context"] = runtime_context
            git_ref, artifact_bindings = spec.publication_fn(
                result=result,
                workspace_path=workspace,
                job_id=job_id,
                task_id=config.task_id or job_id,
                goal=config.goal,
                git_token_env=config.workspace.git_token_env,
                base_sha=base_sha,
                **publication_params,
            )
            result["artifact_bindings"] = artifact_bindings or []
            
            # Tool repetition analysis will be done by Trenni post-job (ADR-0017)
            return result, git_ref
        except PublicationGuardrailViolation as exc:
            can_retry = publication_recovery_attempts < max_recovery_attempts
            gateway.emit(
                RuntimeIssueData(
                    stage="publication",
                    fatal=not can_retry,
                    code="publication_guardrail",
                    violations=exc.violations,
                )
            )
            if not can_retry:
                raise ControlledJobFailure(
                    str(exc),
                    code="publication_guardrail",
                )

            publication_recovery_attempts += 1
            gateway.emit(StageTransitionData(from_stage="publication", to_stage="interaction"))
            pending_user_prompt = (
                "Publication was blocked by runtime guardrails.\n"
                "Issues:\n- " + "\n- ".join(exc.violations) + "\n"
                "Please fix the workspace state, continue using tools if needed, "
                "and stop calling tools when the job is actually done."
            )
            continue


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


@contextmanager
def _materialize_evo_root(requested_sha: str | None):
    live_evo_path = Path.cwd() / _EVO_DIR
    if not requested_sha:
        yield live_evo_path, _read_evo_sha(live_evo_path)
        return

    repo = git.Repo(live_evo_path)
    resolved_commit = repo.commit(requested_sha).hexsha
    current_sha = _read_evo_sha(live_evo_path)
    if current_sha == resolved_commit:
        yield live_evo_path, resolved_commit
        return

    with tempfile.TemporaryDirectory(prefix="palimpsest-evo-") as tmpdir:
        materialized = Path(tmpdir) / "evo"
        materialized.mkdir(parents=True, exist_ok=True)
        archive = subprocess.run(
            ["git", "-C", str(live_evo_path), "archive", "--format=tar", resolved_commit],
            capture_output=True,
            check=True,
        )
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as tar:
            tar.extractall(materialized)
        yield materialized, resolved_commit

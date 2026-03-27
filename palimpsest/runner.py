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

import io
import signal
import subprocess
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
    UnifiedLLMGateway,
    RoleManager,
    UnifiedToolGateway,
)
from palimpsest.runtime.roles import JobSpec
from palimpsest.stages import (
    build_context,
    finalize_workspace_after_job,
    PublicationGuardrailViolation,
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
    with _materialize_evo_root(config.evo_sha) as (evo_path, resolved_evo_sha):
        resolver = RoleManager(evo_path)
        spec = resolver.resolve(config.role, **dict(config.role_params or {}))

        logger.info(
            f"Resolved role '{config.role}' -> JobSpec "
            f"(source_role={spec.source_role!r}, tools={spec.tools})"
        )

        _run_job_from_spec(config, spec, evo_path, resolved_evo_sha=resolved_evo_sha)


# ---------------------------------------------------------------------------
# Pipeline orchestrator
# ---------------------------------------------------------------------------

def _run_job_from_spec(
    config: JobConfig, spec: JobSpec, evo_path: Path, *, resolved_evo_sha: str | None = None
) -> None:
    job_id = config.job_id
    if not job_id:
        raise ValueError("Job ID must be specified in the configuration.")

    emitter = EventEmitter(config.eventstore)
    task_id = config.task_id or job_id
    gateway = EventGateway(emitter, job_id, task_id)

    evo_sha = resolved_evo_sha or _read_evo_sha(evo_path)
    logger.info(f"Starting job {job_id} (evo={evo_sha[:8] if evo_sha else '?'})")

    _install_timeout(config.timeout)

    workspace: str | None = None
    llm = _setup_llm(config, gateway)
    cost_tracking_degraded = llm.cost_tracking_degraded()
    try:
        role_params = dict(config.role_params or {})
        role_params.setdefault("goal", config.task)
        role_params.setdefault("repo", config.workspace.repo)
        role_params.setdefault("init_branch", config.workspace.init_branch)
        branch_prefix = str(
            role_params.get("branch_prefix")
            or getattr(spec.publication_fn, "__publication_branch_prefix__", config.publication.branch_prefix)
        )

        # Stage 1: Workspace (emits stage-transition + job-started internally)
        workspace_cfg = spec.workspace_fn(**role_params)
        workspace = setup_workspace(
            job_id,
            workspace_cfg,
            branch_prefix,
            task_id=config.task_id or job_id,
            goal=config.task,
            gateway=gateway,
            evo_sha=evo_sha,
            cost_tracking_degraded=cost_tracking_degraded,
        )

        # Capture base SHA before any agent modifications (used by guardrails).
        try:
            base_sha = git.Repo(workspace).head.commit.hexsha
        except Exception:
            base_sha = ""

        # Stage 2: Context (emits stage-transition internally)
        context_spec = spec.context_fn(
            workspace=workspace,
            job_id=job_id,
            task=config.task,
            job_config=config,
            evo_root=str(evo_path),
            **role_params,
        )
        context = build_context(
            job_id, workspace, config.task, context_spec, config, gateway, evo_root=evo_path,
        )

        # Stage 3+4: Interaction and publication
        tools = _setup_tools(config, spec, evo_path, evo_sha, gateway)
        result, git_ref = _stage_interaction_and_publication(
            job_id, context, workspace, config, spec, gateway, tools, llm,
            base_sha=base_sha,
            role_params=role_params,
        )

        gateway.emit(
            JobCompletedData(
                git_ref=git_ref,
                summary=result.get("summary", ""),
                status=str(result.get("status", "complete") or "complete"),
                code=str(result.get("code", "") or ""),
                budget_dim=str(result.get("budget_dim", "") or ""),
                cost_tracking_degraded=cost_tracking_degraded,
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
    evo_sha: str,
    gateway: EventGateway,
) -> UnifiedToolGateway:
    """Create the unified tool gateway from builtin + evo providers."""
    return UnifiedToolGateway(
        config.tools,
        evo_path,
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
        )
        interaction_messages = result["messages"]
        pending_user_prompt = None

        publication_strategy = str(
            (role_params or {}).get("publication_strategy")
            or getattr(spec.publication_fn, "__publication_strategy__", "branch")
        )
        should_publish = publication_strategy != "skip"
        if not should_publish:
            return result, None

        gateway.emit(StageTransitionData(from_stage="interaction", to_stage="publication"))
        try:
            publication_params = dict(role_params or {})
            for reserved_key in ("result", "workspace_path", "job_id", "task_id", "goal", "git_token_env", "base_sha"):
                publication_params.pop(reserved_key, None)
            git_ref = spec.publication_fn(
                result=result,
                workspace_path=workspace,
                job_id=job_id,
                task_id=config.task_id or job_id,
                goal=config.task,
                git_token_env=config.workspace.git_token_env,
                base_sha=base_sha,
                **publication_params,
            )
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

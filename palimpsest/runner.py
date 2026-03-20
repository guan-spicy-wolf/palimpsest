"""Four-stage pipeline orchestrator.

The runner is part of the Runtime (skeleton) — it is immutable and not
subject to Agent self-evolution.  It orchestrates:

  1. Workspace setup (clone repo, create job branch)
  2. Context building (from the resolved JobSpec)
  3. Interaction loop (LLM calls + tool execution)
  4. Publication (git commit + push + completion event)

The runner accepts a *resolved* ``JobSpec`` — a flat execution
configuration produced by expanding a Role template.  At execution time
the runner never references the original role name; it depends solely on
the JobSpec.

All events are emitted through the transparent EventGateway.
"""

from __future__ import annotations

import signal
import traceback
import uuid
from pathlib import Path

import git
from loguru import logger

from palimpsest.config import JobConfig
from palimpsest.emitter import EventEmitter
from palimpsest.events import (
    JobCompletedData,
    JobFailedData,
    JobStartedData,
    RuntimeIssueData,
)
from palimpsest.runtime import (
    BuiltinToolProvider,
    EventGateway,
    LiteLLMGateway,
    RoleResolver,
    UnifiedToolGateway,
    resolve_tool_providers,
)
from palimpsest.runtime.role_resolver import JobSpec
from palimpsest.runtime.tools import find_duplicate_tool_names
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

    resolver = RoleResolver(evo_path)
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
    """Execute the four-stage pipeline from a resolved JobSpec."""
    job_id = uuid.uuid4().hex[:12]

    emitter = EventEmitter(config.eventstore)
    gateway = EventGateway(emitter, job_id)

    evo_sha = _read_evo_sha(evo_path)
    logger.info(f"Starting job {job_id} (evo={evo_sha[:8] if evo_sha else '?'})")

    _install_timeout(config.timeout)

    workspace: str | None = None
    try:
        workspace = _stage_workspace(job_id, config, gateway, evo_sha)
        context = _stage_context(job_id, workspace, config, spec, gateway, evo_path)
        tools = _setup_tools(config, spec, evo_path, gateway)
        result, git_ref = _stage_interaction_and_publication(
            job_id, context, workspace, config, spec, gateway, tools,
        )

        gateway.emit_job_completed(
            JobCompletedData(
                status=result["status"],
                git_ref=git_ref,
                summary=result.get("summary", ""),
            )
        )
        logger.info(f"Job {job_id} completed: {result['status']}")

    except ControlledJobFailure as exc:
        error_msg = str(exc)
        logger.error(f"Job {job_id} failed: {error_msg}")
        gateway.emit_job_failed(
            JobFailedData(error=error_msg, code=exc.code)
        )
        raise

    except _JobTimeout:
        logger.error(f"Job {job_id} timed out ({config.timeout}s)")
        gateway.emit_job_failed(
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
        gateway.emit_job_failed(
            JobFailedData(error=error_msg, traceback=tb_str)
        )
        raise

    finally:
        _cleanup(workspace, gateway)


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------

def _stage_workspace(
    job_id: str,
    config: JobConfig,
    gateway: EventGateway,
    evo_sha: str,
) -> str:
    """Stage 1: set up workspace and emit job-started event. Returns workspace path."""
    gateway.emit_stage_transition("init", "workspace")

    workspace = setup_workspace(
        job_id, config.workspace, config.publication.branch_prefix
    )
    base_sha = _read_head_sha(workspace)

    gateway.emit_job_started(
        JobStartedData(
            workspace_path=workspace,
            evo_sha=evo_sha,
            base_sha=base_sha,
        )
    )
    return workspace


def _stage_context(
    job_id: str,
    workspace: str,
    config: JobConfig,
    spec: JobSpec,
    gateway: EventGateway,
    evo_path: Path,
) -> dict:
    """Stage 2: build LLM context from the resolved JobSpec."""
    gateway.emit_stage_transition("workspace", "context")
    return build_context(
        job_id, workspace, config.task, spec, gateway, evo_root=evo_path,
    )


def _setup_tools(
    config: JobConfig,
    spec: JobSpec,
    evo_path: Path,
    gateway: EventGateway,
) -> UnifiedToolGateway:
    """Create the unified tool gateway from builtin + evo providers."""
    builtin = BuiltinToolProvider(config.tools, gateway)
    builtin_providers = builtin.as_provider_dict()
    evo_providers = resolve_tool_providers(evo_path, spec.tools)

    duplicate_tools = find_duplicate_tool_names(builtin_providers, evo_providers)
    if duplicate_tools:
        gateway.emit_runtime_issue(
            RuntimeIssueData(
                stage="interaction",
                fatal=True,
                code="duplicate_tool_name",
                details={"names": duplicate_tools},
            )
        )
        raise ControlledJobFailure(
            "Duplicate tool names configured: " + ", ".join(duplicate_tools),
            code="duplicate_tool_name",
        )

    return UnifiedToolGateway({**builtin_providers, **evo_providers}, gateway)


def _stage_interaction_and_publication(
    job_id: str,
    context: dict,
    workspace: str,
    config: JobConfig,
    spec: JobSpec,
    gateway: EventGateway,
    tools: UnifiedToolGateway,
) -> tuple[dict, str]:
    """Stage 3+4: interaction loop with publication recovery. Returns (result, git_ref)."""
    gateway.emit_stage_transition("context", "interaction")
    llm = LiteLLMGateway(config.llm, gateway)

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
            config.llm.max_iterations,
            messages=interaction_messages,
            user_prompt=pending_user_prompt,
        )
        interaction_messages = result["messages"]
        pending_user_prompt = None

        # Publication guardrails
        gateway.emit_stage_transition("interaction", "publication")
        issues = find_publication_issues(git.Repo(workspace))
        if issues:
            can_retry = publication_recovery_attempts < max_recovery_attempts
            gateway.emit_runtime_issue(
                RuntimeIssueData(
                    stage="publication",
                    fatal=not can_retry,
                    code="publication_guardrail",
                    details={"violations": issues},
                )
            )
            if not can_retry:
                raise ControlledJobFailure(
                    "Publication guardrails triggered:\n- " + "\n- ".join(issues),
                    code="publication_guardrail",
                )

            publication_recovery_attempts += 1
            gateway.emit_stage_transition("publication", "interaction")
            pending_user_prompt = (
                "Publication was blocked by runtime guardrails.\n"
                "Issues:\n- " + "\n- ".join(issues) + "\n"
                "Please fix the workspace state, then explicitly call task_complete again."
            )
            continue

        git_ref = publish_results(job_id, result, workspace, config.publication)
        return result, git_ref


def _cleanup(workspace: str | None, gateway: EventGateway) -> None:
    """Best-effort workspace cleanup and gateway shutdown."""
    if workspace:
        cleanup_issue = finalize_workspace_after_job(workspace)
        if cleanup_issue:
            gateway.emit_runtime_issue(
                RuntimeIssueData(
                    stage="cleanup",
                    fatal=False,
                    code="cleanup_failed",
                    details={"error": cleanup_issue},
                )
            )
    gateway.close()


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


def _read_head_sha(workspace: str) -> str:
    """Return the HEAD SHA of the task repo workspace, or empty string."""
    try:
        return git.Repo(workspace).head.commit.hexsha
    except Exception:
        return ""

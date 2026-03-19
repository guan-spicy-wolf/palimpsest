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
from palimpsest.gateway import BuiltinToolGateway, LiteLLMGateway
from palimpsest.gateway.tool_loader import resolve_tool_providers, EvoToolGateway
from palimpsest.gateway.tools import CompositeToolGateway, find_duplicate_tool_names
from palimpsest.runtime import (
    EventGateway,
    RoleResolver,
)
from palimpsest.runtime.role_resolver import JobSpec
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


def run_job(config: JobConfig) -> None:
    """Resolve the role into a JobSpec and execute the four-stage pipeline."""
    # Resolve the evolvable repo path (hardcoded structural constant)
    evo_path = Path.cwd() / _EVO_DIR

    # Expand the role template into a flat JobSpec — this is the only
    # place where the role name is used.  Everything downstream depends
    # solely on the resolved spec.
    resolver = RoleResolver(evo_path)
    spec = resolver.resolve(config.role)

    logger.info(
        f"Resolved role '{config.role}' -> JobSpec "
        f"(source_role={spec.source_role!r}, tools={spec.tools})"
    )

    _run_job_from_spec(config, spec, evo_path)


def _run_job_from_spec(
    config: JobConfig, spec: JobSpec, evo_path: Path
) -> None:
    """Execute the four-stage pipeline from a resolved JobSpec.

    This function never references a role name — it operates entirely on
    the flat execution specification.
    """
    job_id = uuid.uuid4().hex[:12]

    # Initialise transparent event gateway
    emitter = EventEmitter(config.eventstore)
    gateway = EventGateway(emitter)

    # Read current checkout SHA for informational logging only.
    _log_evo_checkout(evo_path)

    logger.info(f"Starting job {job_id}")

    workspace: str | None = None
    try:
        # Stage 1: Workspace
        gateway.emit_job_started(
            JobStartedData(job_id=job_id, workspace_path="")
        )
        gateway.emit_stage_transition(job_id, "init", "workspace")

        workspace = setup_workspace(
            job_id, config.workspace, config.publication.branch_prefix
        )

        # Stage 2: Context (using JobSpec's prompt and context template)
        gateway.emit_stage_transition(job_id, "workspace", "context")
        context = build_context(
            job_id,
            workspace,
            config.task,
            spec,
            gateway,
            evo_root=evo_path,
        )

        # Stage 3: Interaction
        gateway.emit_stage_transition(job_id, "context", "interaction")
        llm = LiteLLMGateway(config.llm, gateway, job_id)

        # Compose tool gateways: runtime builtins + evo tools
        builtin_tools = BuiltinToolGateway(config.tools, gateway, job_id)
        evo_providers = resolve_tool_providers(evo_path, spec.tools)
        evo_tools = EvoToolGateway(evo_providers, gateway, job_id)
        duplicate_tools = find_duplicate_tool_names([builtin_tools, evo_tools])
        if duplicate_tools:
            message = "Duplicate tool names configured: " + ", ".join(duplicate_tools)
            gateway.emit_runtime_issue(
                RuntimeIssueData(
                    job_id=job_id,
                    stage="interaction",
                    message=message,
                    fatal=True,
                    code="duplicate_tool_name",
                )
            )
            raise ControlledJobFailure(message)

        tools = CompositeToolGateway([builtin_tools, evo_tools])
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

            # Stage 4: Publication
            gateway.emit_stage_transition(job_id, "interaction", "publication")
            issues = find_publication_issues(git.Repo(workspace))
            if issues:
                message = "Publication guardrails triggered:\n- " + "\n- ".join(issues)
                can_retry = publication_recovery_attempts < max_recovery_attempts
                gateway.emit_runtime_issue(
                    RuntimeIssueData(
                        job_id=job_id,
                        stage="publication",
                        message=message,
                        fatal=not can_retry,
                        code="publication_guardrail",
                    )
                )
                if not can_retry:
                    raise ControlledJobFailure(message)

                publication_recovery_attempts += 1
                gateway.emit_stage_transition(job_id, "publication", "interaction")
                pending_user_prompt = (
                    "Publication was blocked by runtime guardrails.\n"
                    f"{message}\n"
                    "Please fix the workspace state, then explicitly call task_complete again."
                )
                continue

            git_ref = publish_results(
                job_id, result, workspace, config.publication
            )
            break

        gateway.emit_job_completed(
            JobCompletedData(
                job_id=job_id,
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
            JobFailedData(job_id=job_id, error=error_msg, traceback=None)
        )
        raise

    except Exception as exc:
        error_msg = str(exc)
        tb_str = traceback.format_exc()
        logger.exception(f"Job {job_id} failed")
        gateway.emit_job_failed(
            JobFailedData(job_id=job_id, error=error_msg, traceback=tb_str)
        )
        raise

    finally:
        if workspace:
            cleanup_issue = finalize_workspace_after_job(workspace)
            if cleanup_issue:
                gateway.emit_runtime_issue(
                    RuntimeIssueData(
                        job_id=job_id,
                        stage="cleanup",
                        message=cleanup_issue,
                        fatal=False,
                        code="cleanup_failed",
                    )
                )
        gateway.close()


def _log_evo_checkout(evo_path: Path) -> None:
    """Log the current checkout SHA of the evolvable repo (informational)."""
    try:
        repo = git.Repo(evo_path)
        logger.info(f"Evolvable repo checkout: {repo.head.commit.hexsha[:8]}")
    except Exception:
        logger.debug("Could not read evolvable repo HEAD")

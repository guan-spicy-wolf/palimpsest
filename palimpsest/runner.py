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

from loguru import logger

from palimpsest.config import JobConfig
from palimpsest.emitter import EventEmitter
from palimpsest.events import (
    JobCompletedData,
    JobFailedData,
    JobStartedData,
)
from palimpsest.gateway import BuiltinToolGateway, LiteLLMGateway
from palimpsest.gateway.tool_loader import resolve_tool_providers, EvoToolGateway
from palimpsest.gateway.tools import CompositeToolGateway
from palimpsest.runtime import (
    EventGateway,
    RoleResolver,
)
from palimpsest.runtime.role_resolver import JobSpec
from palimpsest.stages import (
    build_context,
    publish_results,
    run_interaction_loop,
    setup_workspace,
)

# The evolvable repo is always located at <project_root>/evo.
# This is a structural constant, not a per-job configuration.
_EVO_DIR = "evo"


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

    try:
        # Stage 1: Workspace
        gateway.emit_job_started(
            JobStartedData(job_id=job_id, workspace_path="(pending)")
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

        # Build spawn callback for child task orchestration
        spawn_cb = _make_spawn_callback(config, evo_path)

        # Compose tool gateways: runtime builtins + evo YAML tools
        builtin_tools = BuiltinToolGateway(
            config.tools,
            gateway,
            job_id,
            spawn_callback=spawn_cb,
        )
        evo_providers = resolve_tool_providers(evo_path, spec.tools)
        evo_tools = EvoToolGateway(evo_providers, gateway, job_id)
        tools = CompositeToolGateway([builtin_tools, evo_tools])

        result = run_interaction_loop(
            job_id, context, workspace, llm, tools, config.llm.max_iterations
        )

        # Stage 4: Publication
        gateway.emit_stage_transition(job_id, "interaction", "publication")
        git_ref = publish_results(
            job_id, result, workspace, config.publication
        )
        gateway.emit_job_completed(
            JobCompletedData(
                job_id=job_id,
                status=result["status"],
                git_ref=git_ref,
                summary=result.get("summary", ""),
            )
        )
        logger.info(f"Job {job_id} completed: {result['status']}")

    except Exception as exc:
        error_msg = str(exc)
        tb_str = traceback.format_exc()
        logger.exception(f"Job {job_id} failed")
        gateway.emit_job_failed(
            JobFailedData(job_id=job_id, error=error_msg, traceback=tb_str)
        )
        raise

    finally:
        gateway.close()


def _log_evo_checkout(evo_path: Path) -> None:
    """Log the current checkout SHA of the evolvable repo (informational)."""
    try:
        import git as _git

        repo = _git.Repo(evo_path)
        logger.info(f"Evolvable repo checkout: {repo.head.commit.hexsha[:8]}")
    except Exception:
        logger.debug("Could not read evolvable repo HEAD")


def _make_spawn_callback(config: JobConfig, evo_path: Path):
    """Create a spawn callback that runs child jobs inline.

    In production this would delegate to a Supervisor service.  For now
    child tasks are executed sequentially in-process, each with their
    own job ID, workspace, and Role.
    """

    def spawn_callback(
        parent_job_id: str,
        tasks: list[dict],
        wait_for: str = "all_complete",
    ) -> list[dict]:
        from palimpsest.config import (
            JobConfig as JC,
            WorkspaceConfig,
        )

        results: list[dict] = []
        for task_spec in tasks:
            child_config = JC(
                task=task_spec["task"],
                role=task_spec.get("role", "default"),
                workspace=WorkspaceConfig(
                    repo=task_spec.get("repo", config.workspace.repo),
                    branch=config.workspace.branch,
                    depth=config.workspace.depth,
                    git_token_env=config.workspace.git_token_env,
                ),
                llm=config.llm,
                tools=config.tools,
                publication=config.publication,
                eventstore=config.eventstore,
            )
            try:
                run_job(child_config)
                results.append({"status": "success", "summary": task_spec["task"]})
            except Exception as exc:
                results.append({"status": "failed", "summary": str(exc)[:200]})
                if wait_for == "any_failed":
                    break

        return results

    return spawn_callback

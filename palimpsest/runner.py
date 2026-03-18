"""Four-stage pipeline orchestrator using Role-based architecture.

The runner is part of the Runtime (skeleton) — it is immutable and not
subject to Agent self-evolution.  It orchestrates:

  1. Workspace setup (clone repo, create job branch)
  2. Role resolution (load prompt, context template, tools from evolvable repo)
  3. Interaction loop (LLM calls + tool execution)
  4. Publication (git commit + push + completion event)

All events are emitted through the transparent EventGateway.
Failure on a new evolvable commit triggers automatic rollback.
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
    StageTransitionData,
    VersionAdvancedData,
    VersionRolledBackData,
)
from palimpsest.gateway import BuiltinToolGateway, LiteLLMGateway
from palimpsest.runtime import (
    EventGateway,
    PermissionLayer,
    RoleResolver,
    VersionManager,
)
from palimpsest.stages import (
    build_context,
    publish_results,
    run_interaction_loop,
    setup_workspace,
)


def run_job(config: JobConfig) -> None:
    """Four-stage pipeline orchestrator with Role-based architecture."""
    job_id = uuid.uuid4().hex[:12]

    # Initialise transparent event gateway
    emitter = EventEmitter(config.eventstore)
    gateway = EventGateway(emitter)

    # Resolve the evolvable repo path
    evo_path = Path(config.evolvable.path)
    if not evo_path.is_absolute():
        evo_path = Path.cwd() / evo_path

    # Initialise version manager for evolvable repo
    version_mgr = VersionManager(evo_path)
    logger.info(
        f"Evolvable repo active commit: {version_mgr.active_sha[:8]}"
    )

    # Check for version updates if auto_update enabled
    if config.evolvable.auto_update:
        version_event = version_mgr.check_for_updates()
        if version_event:
            gateway.emit_version_advanced(
                VersionAdvancedData(
                    old_sha=version_event.old_sha,
                    new_sha=version_event.new_sha,
                    changed_files=version_event.changed_files,
                )
            )

    # Initialise permission layer for the evolvable repo
    permissions = PermissionLayer(evo_path)

    # Resolve Role from the evolvable repo
    resolver = RoleResolver(evo_path)
    role = resolver.resolve(config.role)
    logger.info(f"Starting job {job_id} with role '{role.name}'")

    try:
        # Stage 1: Workspace
        gateway.emit_job_started(
            JobStartedData(job_id=job_id, workspace_path="(pending)")
        )
        _emit_transition(gateway, job_id, "init", "workspace")

        workspace = setup_workspace(
            job_id, config.workspace, config.publication.branch_prefix
        )

        # Stage 2: Context (using Role's prompt and context template)
        _emit_transition(gateway, job_id, "workspace", "context")
        context = build_context(
            job_id,
            workspace,
            config.task,
            role,
            gateway,
        )

        # Stage 3: Interaction
        _emit_transition(gateway, job_id, "context", "interaction")
        llm = LiteLLMGateway(config.llm, gateway, job_id)

        # Build spawn callback for child task orchestration
        spawn_cb = _make_spawn_callback(config, evo_path)

        tools = BuiltinToolGateway(
            config.tools,
            gateway,
            job_id,
            permissions=permissions,
            spawn_callback=spawn_cb,
        )
        result = run_interaction_loop(
            job_id, context, workspace, llm, tools, config.llm.max_iterations
        )

        # Stage 4: Publication
        _emit_transition(gateway, job_id, "interaction", "publication")
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

        # Mark active commit as known-good on success
        version_mgr.mark_job_success()

    except Exception as exc:
        error_msg = str(exc)
        tb_str = traceback.format_exc()
        logger.exception(f"Job {job_id} failed")
        gateway.emit_job_failed(
            JobFailedData(job_id=job_id, error=error_msg, traceback=tb_str)
        )

        # Rollback evolvable repo if on a new (unproven) commit
        rollback_event = version_mgr.rollback()
        if rollback_event:
            logger.warning(
                f"Rolled back evolvable repo: "
                f"{rollback_event.old_sha[:8]} -> {rollback_event.new_sha[:8]}"
            )
            gateway.emit_version_rolled_back(
                VersionRolledBackData(
                    old_sha=rollback_event.old_sha,
                    new_sha=rollback_event.new_sha,
                    changed_files=rollback_event.changed_files,
                )
            )

        raise

    finally:
        gateway.close()


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
            EventStoreConfig,
            EvolvableRepoConfig,
            JobConfig as JC,
            LLMConfig,
            PublicationConfig,
            ToolsConfig,
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
                ),
                evolvable=EvolvableRepoConfig(path=str(evo_path)),
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


def _emit_transition(
    gateway: EventGateway, job_id: str, from_stage: str, to_stage: str
) -> None:
    gateway._emitter.emit(
        StageTransitionData(
            job_id=job_id, from_stage=from_stage, to_stage=to_stage
        )
    )

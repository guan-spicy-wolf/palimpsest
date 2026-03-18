"""Four-stage pipeline orchestrator using Role-based architecture.

The runner is part of the Runtime (skeleton) — it is immutable and not
subject to Agent self-evolution.  It orchestrates:

  1. Workspace setup (clone repo, create job branch)
  2. Role resolution (load prompt, context template, tools from evolvable repo)
  3. Interaction loop (LLM calls + tool execution)
  4. Publication (git commit + push + completion event)

All events are emitted through the transparent EventGateway.
"""

from __future__ import annotations

import sys
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
)
from palimpsest.gateway import BuiltinToolGateway, LiteLLMGateway
from palimpsest.runtime import EventGateway, RoleResolver, VersionManager
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
        tools = BuiltinToolGateway(config.tools, gateway, job_id)
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
        raise

    finally:
        gateway.close()


def _emit_transition(
    gateway: EventGateway, job_id: str, from_stage: str, to_stage: str
) -> None:
    gateway._emitter.emit(
        StageTransitionData(
            job_id=job_id, from_stage=from_stage, to_stage=to_stage
        )
    )

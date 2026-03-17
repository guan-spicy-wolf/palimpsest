from __future__ import annotations

import sys
import traceback
import uuid

from loguru import logger

from palimpsest.config import JobConfig
from palimpsest.emitter import EventEmitter
from palimpsest.events import JobCompletedData, JobFailedData, JobStartedData
from palimpsest.gateway import BuiltinToolGateway, LiteLLMGateway
from palimpsest.stages import build_context, publish_results, run_interaction_loop, setup_workspace


def run_job(config: JobConfig) -> None:
    """Four-stage pipeline orchestrator."""
    job_id = uuid.uuid4().hex[:12]
    emitter = EventEmitter(config.eventstore)

    logger.info(f"Starting job {job_id}")

    try:
        # Stage 1: Workspace
        workspace = setup_workspace(job_id, config.workspace, config.publication.branch_prefix)
        emitter.emit(JobStartedData(job_id=job_id, workspace_path=workspace))

        # Stage 2: Context
        context = build_context(job_id, workspace, config.task, config.context, emitter)

        # Stage 3: Interaction
        llm = LiteLLMGateway(config.llm, emitter, job_id)
        tools = BuiltinToolGateway(config.tools, emitter, job_id)
        result = run_interaction_loop(job_id, context, workspace, llm, tools, config.llm.max_iterations)

        # Stage 4: Publication
        git_ref = publish_results(job_id, result, workspace, config.publication)
        emitter.emit(
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
        emitter.emit(JobFailedData(job_id=job_id, error=error_msg, traceback=tb_str))
        raise

    finally:
        emitter.close()

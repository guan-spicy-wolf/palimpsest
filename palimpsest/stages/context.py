"""Stage 2: Context building from the resolved JobSpec.

Assembles the LLM context window using the JobSpec's system prompt and
a registry of ContextProvider implementations loaded from evo/.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from loguru import logger

from palimpsest.config import JobConfig
from palimpsest.runtime.contexts import resolve_context_functions
from palimpsest.runtime.event_gateway import EventGateway
from palimpsest.runtime.roles import JobSpec


def build_context(
    job_id: str,
    workspace_path: str,
    task: str,
    spec: JobSpec,
    job_config: JobConfig,
    gateway: EventGateway,
    evo_root: Path | None = None,
) -> dict:
    """Build LLM context from a resolved JobSpec. Returns {"system": str, "task": str}."""
    from palimpsest.events import StageTransitionData
    gateway.emit(StageTransitionData(from_stage="workspace", to_stage="context"))

    system_prompt = spec.prompt

    sections = spec.context_template.get("sections", [])
    section_types = [s.get("type", "") for s in sections]

    registry = {}
    if evo_root:
        registry = resolve_context_functions(evo_root, section_types)

    parts: list[str] = []
    for section in sections:
        section_type = section.get("type", "")
        provider_fn = registry.get(section_type)
        if provider_fn:
            try:
                sig = inspect.signature(provider_fn)
                kwargs = dict(section)
                # Remove the structural identifier so it isn't passed as a kwarg
                kwargs.pop("type", None)

                # Inject runtime dependencies if requested
                if "workspace" in sig.parameters:
                    kwargs["workspace"] = workspace_path
                if "job_id" in sig.parameters:
                    kwargs["job_id"] = job_id
                if "task" in sig.parameters:
                    kwargs["task"] = task
                if "job_config" in sig.parameters:
                    kwargs["job_config"] = job_config
                if "eventstore" in sig.parameters:
                    kwargs["eventstore"] = job_config.eventstore
                if "evo_root" in sig.parameters and evo_root is not None:
                    kwargs["evo_root"] = str(evo_root)

                content = provider_fn(**kwargs)
                parts.append(str(content))
            except Exception as exc:
                logger.error(f"Context provider {section_type!r} failed: {exc}")
                # Execution-time defense: fallback instead of failing job
                parts.append(f"[Error rendering context section {section_type!r}: {exc}]")
        else:
            logger.warning(f"No provider for context section type: {section_type!r}")

    task_message = "\n\n".join(parts)
    logger.info(f"Built context for job {job_id}")
    return {"system": system_prompt, "task": task_message}

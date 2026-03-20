"""Stage 2: Context building from the resolved JobSpec.

Assembles the LLM context window using the JobSpec's system prompt and
a registry of ContextProvider implementations loaded from evo/.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from palimpsest.runtime.event_gateway import EventGateway
from palimpsest.runtime.interfaces import ContextProvider
from palimpsest.runtime.resolver import resolve_providers
from palimpsest.runtime.role_resolver import JobSpec


def resolve_context_providers(
    evo_root: str | Path,
    requested: list[str],
) -> dict[str, ContextProvider]:
    """Resolve context providers from evo/contexts/*.py."""
    evo_root = Path(evo_root)
    return resolve_providers(
        scan_dir=evo_root / "contexts",
        base_class=ContextProvider,
        key_fn=lambda inst: [inst.section_type],
        requested=requested,
    )


def build_context(
    job_id: str,
    workspace_path: str,
    task: str,
    spec: JobSpec,
    gateway: EventGateway,
    evo_root: Path | None = None,
) -> dict:
    """Build LLM context from a resolved JobSpec. Returns {"system": str, "task": str}."""
    gateway.emit_stage_transition("workspace", "context")

    system_prompt = spec.prompt

    sections = spec.context_template.get("sections", [])
    section_types = [s.get("type", "") for s in sections]

    registry: dict[str, ContextProvider] = {}
    if evo_root:
        registry = resolve_context_providers(evo_root, section_types)

    runtime_deps = {"gateway": gateway, "task": task}

    parts: list[str] = []
    for section in sections:
        section_type = section.get("type", "")
        provider = registry.get(section_type)
        if provider:
            parts.append(provider.render(job_id, workspace_path, section, runtime_deps=runtime_deps))
        else:
            logger.warning(f"No provider for context section type: {section_type!r}")

    task_message = "\n\n".join(parts)
    logger.info(f"Built context for job {job_id}")
    return {"system": system_prompt, "task": task_message}

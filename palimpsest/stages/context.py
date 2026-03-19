"""Stage 2: Context building from the resolved JobSpec.

Assembles the LLM context window using the JobSpec's system prompt and
a registry of ContextProvider implementations. Evo providers are
loaded first; builtin fallbacks are used for section types not found in evo.
"""

from __future__ import annotations

import os
from pathlib import Path

from loguru import logger

from palimpsest.runtime.event_gateway import EventGateway
from palimpsest.runtime.interfaces import ContextProvider
from palimpsest.runtime.resolver import resolve_providers
from palimpsest.runtime.role_resolver import JobSpec


# ---------------------------------------------------------------------------
# Evo context provider resolution
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Built-in fallback context providers
# ---------------------------------------------------------------------------

class _FileTreeFallback(ContextProvider):
    @property
    def section_type(self) -> str:
        return "file_tree"

    def render(self, job_id, workspace, section_config, runtime_deps=None):
        max_files = section_config.get("max_files", 50)
        excludes = set(section_config.get("exclude", [".git"]))
        lines, count = [], 0
        for dirpath, dirnames, filenames in os.walk(workspace):
            dirnames[:] = [d for d in dirnames if d not in excludes]
            rel_dir = os.path.relpath(dirpath, workspace)
            prefix = "" if rel_dir == "." else rel_dir + "/"
            for fname in filenames:
                lines.append(prefix + fname)
                count += 1
                if count >= max_files:
                    lines.append(f"... (truncated at {max_files} files)")
                    tree = "\n".join(lines)
                    return f"## Workspace file tree\n```\n{tree}\n```"
        tree = "\n".join(lines) if lines else "(empty)"
        return f"## Workspace file tree\n```\n{tree}\n```"


class _RecentEventsFallback(ContextProvider):
    @property
    def section_type(self) -> str:
        return "recent_events"

    def render(self, job_id, workspace, section_config, runtime_deps=None):
        gateway = (runtime_deps or {}).get("gateway")
        if not gateway:
            return "## Recent events\n(event gateway not available)"
        limit = section_config.get("limit", 10)
        fmt = section_config.get("format", "- [{ts}] {type}")
        recent = gateway.recent_events(limit, job_id=job_id)
        lines = []
        for e in recent:
            try:
                lines.append(fmt.format_map({
                    "ts": e.get("ts", "N/A"),
                    "type": e.get("type", "unknown"),
                    "summary": e.get("data", {}).get("summary", ""),
                }))
            except KeyError:
                lines.append(f"- [{e.get('ts', 'N/A')}] {e.get('type', 'unknown')}")
        summary = "\n".join(lines) if lines else "(no recent events)"
        return f"## Recent events\n{summary}"


class _TaskDescriptionFallback(ContextProvider):
    @property
    def section_type(self) -> str:
        return "task_description"

    def render(self, job_id, workspace, section_config, runtime_deps=None):
        task = (runtime_deps or {}).get("task", "(no task provided)")
        return f"## Task\n{task}"


class _VersionHistoryFallback(ContextProvider):
    @property
    def section_type(self) -> str:
        return "version_history"

    def render(self, job_id, workspace, section_config, runtime_deps=None):
        return "## Version history\n(reading current checkout only)"


def _build_fallback_registry() -> dict[str, ContextProvider]:
    providers = [
        _FileTreeFallback(),
        _RecentEventsFallback(),
        _TaskDescriptionFallback(),
        _VersionHistoryFallback(),
    ]
    return {p.section_type: p for p in providers}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_context(
    job_id: str,
    workspace_path: str,
    task: str,
    spec: JobSpec,
    gateway: EventGateway,
    evo_root: Path | None = None,
) -> dict:
    """Build LLM context from a resolved JobSpec. Returns {"system": str, "task": str}."""
    system_prompt = spec.prompt

    # Start with builtin fallbacks
    registry = _build_fallback_registry()

    # Override with evo providers where available
    if evo_root:
        section_types = [s.get("type", "") for s in spec.context_template.get("sections", [])]
        evo_providers = resolve_context_providers(evo_root, section_types)
        registry.update(evo_providers)

    runtime_deps = {"gateway": gateway, "task": task}

    sections = spec.context_template.get("sections", [])
    parts: list[str] = []
    for section in sections:
        section_type = section.get("type", "")
        provider = registry.get(section_type)
        if provider:
            parts.append(provider.render(job_id, workspace_path, section, runtime_deps=runtime_deps))
        else:
            logger.warning(f"Unknown context section type: {section_type!r}")

    task_message = "\n\n".join(parts)
    logger.info(f"Built context for job {job_id}")
    return {"system": system_prompt, "task": task_message}

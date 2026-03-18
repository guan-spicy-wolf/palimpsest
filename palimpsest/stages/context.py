"""Stage 2: Context building from the resolved JobSpec.

Assembles the LLM context window using the JobSpec's system prompt and
a registry of ``ContextProvider`` implementations.  Each section type
declared in the context template is handled by its corresponding
provider — no hard-coded ``if/elif`` branches.

The Agent can still query additional events during the interaction loop;
those extra queries are recorded by the transparent event gateway as
evolution signals.
"""

from __future__ import annotations

import os

from loguru import logger

from palimpsest.runtime.event_gateway import EventGateway
from palimpsest.runtime.interfaces import ContextProvider
from palimpsest.runtime.role_resolver import JobSpec


# ---------------------------------------------------------------------------
# Built-in context providers
# ---------------------------------------------------------------------------

class FileTreeProvider(ContextProvider):
    """Renders workspace file listing."""

    @property
    def section_type(self) -> str:
        return "file_tree"

    def render(self, job_id: str, workspace: str, section_config: dict) -> str:
        max_files = section_config.get("max_files", 50)
        excludes = section_config.get("exclude", [".git"])
        file_tree = _list_files(workspace, max_files=max_files, exclude=excludes)
        return f"## Workspace file tree\n```\n{file_tree}\n```"


class RecentEventsProvider(ContextProvider):
    """Renders recent events scoped to the current job."""

    def __init__(self, gateway: EventGateway):
        self._gateway = gateway

    @property
    def section_type(self) -> str:
        return "recent_events"

    def render(self, job_id: str, workspace: str, section_config: dict) -> str:
        limit = section_config.get("limit", 10)
        fmt = section_config.get("format", "- [{ts}] {type}")
        recent = self._gateway.recent_events(limit, job_id=job_id)
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


class TaskDescriptionProvider(ContextProvider):
    """Renders the task description."""

    def __init__(self, task: str):
        self._task = task

    @property
    def section_type(self) -> str:
        return "task_description"

    def render(self, job_id: str, workspace: str, section_config: dict) -> str:
        return f"## Task\n{self._task}"


class VersionHistoryProvider(ContextProvider):
    """Placeholder — version management is currently degraded."""

    @property
    def section_type(self) -> str:
        return "version_history"

    def render(self, job_id: str, workspace: str, section_config: dict) -> str:
        return "## Version history\n(reading current checkout only)"


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

def _build_provider_registry(
    task: str, gateway: EventGateway
) -> dict[str, ContextProvider]:
    """Create the default provider registry."""
    providers: list[ContextProvider] = [
        FileTreeProvider(),
        RecentEventsProvider(gateway),
        TaskDescriptionProvider(task),
        VersionHistoryProvider(),
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
) -> dict:
    """Build LLM context from a resolved JobSpec. Returns {"system": str, "task": str}."""
    system_prompt = spec.prompt
    registry = _build_provider_registry(task, gateway)

    sections = spec.context_template.get("sections", [])
    parts: list[str] = []
    for section in sections:
        section_type = section.get("type", "")
        provider = registry.get(section_type)
        if provider:
            parts.append(provider.render(job_id, workspace_path, section))
        else:
            logger.warning(f"Unknown context section type: {section_type!r}")

    task_message = "\n\n".join(parts)
    logger.info(f"Built context for job {job_id}")
    return {"system": system_prompt, "task": task_message}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _list_files(
    root: str, max_files: int = 50, exclude: list[str] | None = None
) -> str:
    exclude = set(exclude or [".git"])
    lines: list[str] = []
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude]
        rel_dir = os.path.relpath(dirpath, root)
        prefix = "" if rel_dir == "." else rel_dir + "/"
        for fname in filenames:
            lines.append(prefix + fname)
            count += 1
            if count >= max_files:
                lines.append(f"... (truncated at {max_files} files)")
                return "\n".join(lines)
    return "\n".join(lines) if lines else "(empty)"

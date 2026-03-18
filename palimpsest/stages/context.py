"""Stage 2: Context building from Role's prompt and context template.

Assembles the LLM context window using the resolved Role's system prompt,
the workspace file tree, and recent events from the EventStore.

The context template (from the evolvable repo) declares which sections
to include and how to format them.  The Agent can still query additional
events during the interaction loop — those extra queries are recorded by
the transparent event gateway as evolution signals.
"""

from __future__ import annotations

import os

from loguru import logger

from palimpsest.runtime.event_gateway import EventGateway
from palimpsest.runtime.role_resolver import ResolvedRole


def build_context(
    job_id: str,
    workspace_path: str,
    task: str,
    role: ResolvedRole,
    gateway: EventGateway,
) -> dict:
    """Build LLM context from Role definition. Returns {"system": str, "task": str}."""
    system_prompt = role.prompt

    ctx = role.context_template
    sections = ctx.get("sections", [])

    parts: list[str] = []
    for section in sections:
        section_type = section.get("type", "")
        if section_type == "file_tree":
            max_files = section.get("max_files", 50)
            excludes = section.get("exclude", [".git"])
            file_tree = _list_files(workspace_path, max_files=max_files, exclude=excludes)
            parts.append(f"## Workspace file tree\n```\n{file_tree}\n```")

        elif section_type == "recent_events":
            limit = section.get("limit", 10)
            fmt = section.get("format", "- [{ts}] {type}")
            recent = gateway.recent_events(limit)
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
            parts.append(f"## Recent events\n{summary}")

        elif section_type == "task_description":
            parts.append(f"## Task\n{task}")

        elif section_type == "version_history":
            limit = section.get("limit", 5)
            parts.append("## Version history\n(version tracking active)")

    task_message = "\n\n".join(parts)

    logger.info(f"Built context for job {job_id} using role '{role.name}'")
    return {"system": system_prompt, "task": task_message}


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

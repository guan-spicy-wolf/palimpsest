from __future__ import annotations

import os

from loguru import logger

from palimpsest.config import ContextConfig
from palimpsest.emitter import EventEmitter
from palimpsest.prompts import load_prompt


def build_context(
    job_id: str,
    workspace_path: str,
    task: str,
    config: ContextConfig,
    emitter: EventEmitter,
) -> dict:
    """Build LLM context. Returns {"system": str, "task": str}."""
    system_prompt = load_prompt(config.system_prompt)

    file_tree = _list_files(workspace_path, max_files=50)
    recent = emitter.recent_events(config.recent_events)

    recent_summary = "\n".join([f"- [{e.get('ts', 'N/A')}] {e.get('type', 'unknown')}" for e in recent])
    if not recent_summary:
        recent_summary = "(no recent events)"

    task_message = (
        f"## Task\n{task}\n\n"
        f"## Workspace file tree\n```\n{file_tree}\n```\n\n"
        f"## Recent events\n{recent_summary}"
    )

    logger.info(f"Built context for job {job_id}")
    return {"system": system_prompt, "task": task_message}


def _list_files(root: str, max_files: int = 50) -> str:
    lines = []
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        rel_dir = os.path.relpath(dirpath, root)
        prefix = "" if rel_dir == "." else rel_dir + "/"
        for fname in filenames:
            lines.append(prefix + fname)
            count += 1
            if count >= max_files:
                lines.append(f"... (truncated at {max_files} files)")
                return "\n".join(lines)
    return "\n".join(lines) if lines else "(empty)"

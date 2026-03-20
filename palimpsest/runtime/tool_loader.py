"""Tool-specific provider resolver.

Delegates to the generic resolve_providers() for discovery and loading.
"""

from __future__ import annotations

from pathlib import Path

from palimpsest.runtime.interfaces import ToolProvider
from palimpsest.runtime.resolver import resolve_providers


def resolve_tool_providers(
    evo_root: str | Path,
    requested: list[str],
) -> dict[str, ToolProvider]:
    """Resolve tool providers from evo/tools/*.py."""
    evo_root = Path(evo_root)
    return resolve_providers(
        scan_dir=evo_root / "tools",
        base_class=ToolProvider,
        key_fn=lambda inst: [s.name for s in inst.tools()],
        requested=requested,
    )

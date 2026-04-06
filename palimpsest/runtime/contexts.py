"""Context loader — dynamic resolution of context generation functions.

Part of the Runtime (skeleton). Context providers are pure Python functions
marked with the ``@context_provider`` decorator.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Callable

from loguru import logger


def context_provider(section_type: str) -> Callable:
    """Decorator to mark a function as a context provider for a specific section type."""
    def decorator(func: Callable):
        func.__is_context__ = True
        func.__section_type__ = section_type
        return func
    return decorator


def _load_context_functions(py_path: Path) -> dict[str, Callable]:
    """Load a .py file in isolated scope and extract @context_provider functions."""
    module_name = f"_evo_contexts_{py_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, py_path)
    if spec is None or spec.loader is None:
        return {}

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        logger.error(f"Failed to load contexts from {py_path}: {exc}")
        return {}

    funcs = {}
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if callable(attr) and getattr(attr, "__is_context__", False):
            funcs[attr.__section_type__] = attr
    return funcs


def resolve_context_functions(
    evo_root: str | Path,
    requested: list[str],
    team: str = "default",
) -> dict[str, Callable]:
    """Scan evo/teams/<team>/contexts/ first, then evo/contexts/ for fallback.
    
    Per Factorio Tool Evolution MVP: team-specific contexts have higher priority.
    This enables factorio team to have its own context providers (e.g., factorio_scripts).
    """
    requested_set = set(requested)
    result: dict[str, Callable] = {}
    
    # Scan team-specific first (higher priority)
    team_dir = Path(evo_root) / "teams" / team / "contexts"
    global_dir = Path(evo_root) / "contexts"
    
    for scan_dir in (team_dir, global_dir):
        if not scan_dir.is_dir():
            continue
        for py_file in sorted(scan_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            funcs = _load_context_functions(py_file)
            for section_type, func in funcs.items():
                if section_type in requested_set and section_type not in result:
                    result[section_type] = func

    missing = requested_set - set(result.keys())
    if missing:
        logger.warning(f"Context providers not found: {missing}")

    return result
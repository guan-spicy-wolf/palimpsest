"""Generic one-shot provider resolver.

Scans a directory for .py files, loads each in an isolated namespace
(NOT registered in sys.modules), finds subclasses of a given ABC,
instantiates them, and returns a dict keyed by the provider's declared
names, filtered to the requested set.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Callable

from loguru import logger


def resolve_providers(
    scan_dir: Path,
    base_class: type,
    key_fn: Callable,
    requested: list[str],
) -> dict[str, object]:
    """One-shot resolve: scan *.py, return {key: instance} for requested keys."""
    if not scan_dir.is_dir():
        logger.warning(f"Provider directory not found: {scan_dir}")
        return {}

    requested_set = set(requested)
    result: dict[str, object] = {}

    for py_file in sorted(scan_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            instances = _load_subclasses(py_file, base_class)
            for instance in instances:
                for key in key_fn(instance):
                    if key in requested_set:
                        result[key] = instance
        except Exception as exc:
            logger.error(f"Failed to load providers from {py_file}: {exc}")

    missing = requested_set - set(result.keys())
    if missing:
        logger.warning(f"Providers not found in {scan_dir}: {missing}")

    return result


def _load_subclasses(py_path: Path, base_class: type) -> list:
    """Load a .py file in isolated scope, return instances of base_class subclasses."""
    module_name = f"_evo_resolve_{py_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, py_path)
    if spec is None or spec.loader is None:
        return []

    module = importlib.util.module_from_spec(spec)
    # Isolated — NOT registered in sys.modules
    spec.loader.exec_module(module)

    instances = []
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, base_class)
            and attr is not base_class
        ):
            instances.append(attr())

    return instances

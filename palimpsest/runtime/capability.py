"""Capability model implementation per ADR-0016, extended by ADR-0021.

Capability is a runtime service management unit with setup/finalize lifecycle.
Each capability:
- Has a name for role declaration
- Has a surface (ADR-0021): "control_plane" or "job_side" — where it runs
- setup(ctx) returns events, runtime emits them
- finalize(ctx) returns FinalizeResult(events, success), runtime decides job state

Per ADR-0021:
- surface="control_plane": runs in Trenni subprocess from master@switched_sha
- surface="job_side": runs in Palimpsest job container from evolve@job_bundle_sha
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable, Any, Literal

from loguru import logger

from yoitsu_contracts import FinalizeResult, EventData, AnalyzerVersion, TargetSource


@runtime_checkable
class Capability(Protocol):
    """Capability protocol per ADR-0016, extended by ADR-0021.

    Each capability manages a runtime service with setup/finalize lifecycle.
    The surface attribute (ADR-0021) determines where the capability executes.
    """
    name: str
    surface: Literal["control_plane", "job_side"]  # ADR-0021: execution surface

    def setup(self, ctx: JobContext) -> list[EventData]:
        """Setup the capability. Returns event data for runtime to emit.

        Setup failure = job failure (preparation failure path).
        """
        ...

    def finalize(self, ctx: JobContext) -> FinalizeResult:
        """Finalize the capability. Returns (events, success).

        Must NOT raise exceptions. All retry logic is inside finalize.
        Runtime uses success flag to determine job terminal state.
        """
        ...


@dataclass
class JobContext:
    """Context passed to capability setup/finalize.

    Provides access to:
    - Job configuration
    - Workspaces (bundle and target)
    - Resources dict for capability-shared state
    - analyzer_version for observation emission (ADR-0017)
    - target_source for artifact URI construction (ADR-0015)
    """
    job_id: str
    task_id: str
    bundle: str
    role: str
    goal: str
    bundle_workspace: str = ""
    target_workspace: str = ""
    resources: dict[str, Any] = field(default_factory=dict)
    analyzer_version: AnalyzerVersion | None = None  # ADR-0017
    target_source: TargetSource | None = None  # For artifact URI construction


# === Capability Decorator (ADR-0021) ===

def capability(
    surface: Literal["control_plane", "job_side"],
    name: str,
) -> callable:
    """Decorator to register a capability with explicit surface and name.

    Usage:
        @capability(surface="control_plane", name="factorio_mount")
        class FactorioMount: ...

        @capability(surface="job_side", name="factorio_runtime")
        class FactorioRuntime: ...

    The decorator sets `surface` and `name` attributes on the class.
    The class must still implement setup() and finalize() methods
    matching the Capability protocol.
    """
    def decorator(cls: type) -> type:
        cls.surface = surface
        cls.name = name
        return cls
    return decorator


# === Capability Registry ===

# NOTE: Per ADR-0021 A.7, BUILTIN_CAPABILITIES has been deleted.
# Each bundle must provide its own git_workspace and cleanup capabilities.


def get_capability(name: str, extra: dict[str, "Capability"] | None = None) -> "Capability | None":
    """Get a capability by name.

    Per ADR-0021 A.7: builtins have been removed. Only bundle-provided
    capabilities (via extra dict) are used.

    Returns None if capability not found in extra dict.
    """
    if extra and name in extra:
        return extra[name]
    return None


def _load_bundle_capabilities(
    bundle_workspace: str | Path,
    surface_filter: Literal["control_plane", "job_side"] | None = "job_side",
) -> dict[str, Capability]:
    """Load capabilities from bundle_workspace/capabilities/ directory.

    Per ADR-0021: capabilities are filtered by surface attribute.
    - surface_filter="job_side": load only job-side capabilities (for Palimpsest runner)
    - surface_filter="control_plane": load only control-plane capabilities (for Trenni subprocess)
    - surface_filter=None: load all capabilities (rare, for debugging)

    Each Python file in capabilities/ is dynamically imported. Classes that
    implement the Capability protocol (having name, surface, setup, finalize)
    are instantiated and registered by their name attribute.

    Returns empty dict if capabilities/ does not exist or loading fails.
    """
    import importlib.util

    caps_dir = Path(bundle_workspace) / "capabilities"
    if not caps_dir.is_dir():
        return {}

    result: dict[str, Capability] = {}

    for py_file in caps_dir.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"bundle_cap_{py_file.stem}",
                py_file,
            )
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            for obj_name in dir(module):
                obj = getattr(module, obj_name)
                # Skip imported items, non-classes, abstract classes
                if obj_name.startswith("_"):
                    continue
                if not isinstance(obj, type):
                    continue
                # Check Capability protocol attributes
                if not hasattr(obj, "name"):
                    continue
                if not hasattr(obj, "surface"):
                    continue
                if not hasattr(obj, "setup") or not hasattr(obj, "finalize"):
                    continue
                # Surface filtering
                if surface_filter is not None and obj.surface != surface_filter:
                    continue
                # Instantiate and register
                try:
                    instance = obj()
                    result[obj.name] = instance
                except Exception as e:
                    logger.warning(f"Failed to instantiate capability {obj.name}: {e}")
        except Exception as e:
            logger.warning(f"Failed to load capabilities from {py_file}: {e}")

    return result
"""Three-tier permission model per v3 architecture.

Tiers:
  LOCKED   — Runtime code, event gateway, sandbox, EventStore, schema
              validation, version progression/rollback logic.
              Agent cannot touch.  Changes require PR.

  STABLE   — spawn and other business Tool interfaces, evolvable repo
              directory structure conventions.
              Agent can *use* but cannot *modify*.

  FREE     — All files inside the evolvable repository (prompts, context
              templates, tool definitions, role definitions).
              Agent evolves freely via Git operations.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from loguru import logger


class PermissionTier(str, Enum):
    LOCKED = "locked"
    STABLE = "stable"
    FREE = "free"


# Stable directory names inside the evolvable repo
_EVO_DIRECTORIES = {"prompts", "contexts", "tools", "roles"}


class PermissionLayer:
    """Enforces the three-tier permission boundary.

    Given a path relative to the system root, determines whether the
    Agent is allowed to read / modify it, and at which tier it falls.
    """

    def __init__(self, evo_root: str | Path):
        self._evo_root = Path(evo_root).resolve()

    def classify(self, abs_path: str | Path) -> PermissionTier:
        """Classify a file path into a permission tier."""
        abs_path = Path(abs_path).resolve()

        # Inside evolvable repo?
        try:
            rel = abs_path.relative_to(self._evo_root)
        except ValueError:
            # Outside evo repo — locked (Runtime, infra, etc.)
            return PermissionTier.LOCKED

        # Top-level directory structure itself is STABLE
        parts = rel.parts
        if len(parts) == 1 and parts[0] in _EVO_DIRECTORIES:
            return PermissionTier.STABLE

        # Files inside the evo repo directories are FREE
        if len(parts) >= 1 and parts[0] in _EVO_DIRECTORIES:
            return PermissionTier.FREE

        # Other files at evo repo root (e.g. .gitignore) are STABLE
        return PermissionTier.STABLE

    def check_modify(self, abs_path: str | Path) -> bool:
        """Return True if the Agent is allowed to modify this path."""
        tier = self.classify(abs_path)
        if tier == PermissionTier.FREE:
            return True
        logger.warning(
            f"Modification denied: {abs_path} is in {tier.value} tier"
        )
        return False

    def check_read(self, abs_path: str | Path) -> bool:
        """Return True if the Agent is allowed to read this path.

        Agents can read FREE and STABLE resources.  LOCKED resources
        (Runtime internals) are not exposed to the Agent.
        """
        tier = self.classify(abs_path)
        return tier in (PermissionTier.FREE, PermissionTier.STABLE)

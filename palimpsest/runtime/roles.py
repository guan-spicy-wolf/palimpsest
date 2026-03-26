"""Role resolver — reads Role definitions from the evolvable repository.

A Role is a convenience definition used only at the plan/spawn stage.
``RoleManager.resolve()`` extracts a ``RoleDefinition`` object from a `.py` file
and expands it into a ``JobSpec`` — the flat, self-contained execution configuration
that the runtime consumes. After expansion the role object is no longer needed;
the runtime operates solely on the ``JobSpec``.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class RoleDefinition:
    """Agent role definition natively composed in Python by evo developers.
    
    This replaces the legacy YAML configurations.
    """
    name: str
    description: str
    # Either inline markdown text, or a relative path (e.g., "prompts/default.md")
    prompt: str
    # List of context sections mapped to @context_provider functions
    contexts: list[dict[str, Any]] = field(default_factory=list)
    # List of @tool function names available to this agent
    tools: list[str] = field(default_factory=list)


@dataclass
class JobSpec:
    """Fully resolved execution specification consumed by the runtime.

    ``tools`` is a list of tool names to load from evo/tools/*.py.
    Runtime builtins (bash, spawn) are always implicitly available.
    """
    prompt: str
    context_template: dict
    tools: list[str] = field(default_factory=list)
    source_role: str = ""  # informational only; not used at execution time


@dataclass
class TeamDefinition:
    name: str
    description: str
    roles: list[str] = field(default_factory=list)
    planner_role: str = "planner"
    eval_role: str = "evaluator"


class RoleManager:
    """Expands Role templates from a checked-out evolvable repository into JobSpecs."""

    def __init__(self, evo_root: str | Path):
        self._root = Path(evo_root)

    def resolve(self, role_name: str) -> JobSpec:
        """Expand a Python-defined role into a flat JobSpec."""
        role_def = self.get_definition(role_name)

        prompt_text = role_def.prompt
        # If it looks like a path and the file exists, read its contents
        if prompt_text.endswith(".md") or prompt_text.endswith(".txt"):
            potential_path = self._root / prompt_text
            if potential_path.is_file():
                prompt_text = potential_path.read_text(encoding="utf-8")

        # Convert simple list of dicts to the legacy section format expected by the runner
        context_template = {"sections": role_def.contexts}

        return JobSpec(
            prompt=prompt_text,
            context_template=context_template,
            tools=list(role_def.tools),
            source_role=role_def.name,
        )

    def list_roles(self) -> list[str]:
        """List available role names."""
        roles_dir = self._root / "roles"
        if not roles_dir.exists():
            return []
        return [p.stem for p in roles_dir.glob("*.py") if not p.name.startswith("_")]

    def get_definition(self, name: str) -> RoleDefinition:
        """Load and return the raw role definition."""
        return self._load_role(name)

    def _load_role(self, name: str) -> RoleDefinition:
        """Dynamically load the role module and extract the RoleDefinition instance."""
        py_path = self._root / "roles" / f"{name}.py"
        if not py_path.exists():
            raise FileNotFoundError(f"Role definition not found: {name} (expected {py_path})")

        module_name = f"_evo_roles_{name}"
        spec = importlib.util.spec_from_file_location(module_name, py_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load role module from {py_path}")

        module = importlib.util.module_from_spec(spec)
        try:
            # Isolated execution scope without polluting sys.modules
            spec.loader.exec_module(module)
        except Exception as exc:
            logger.error(f"Failed to execute role module {py_path}: {exc}")
            raise RuntimeError(f"Error loading role '{name}': {exc}") from exc

        # Locate the first RoleDefinition instance in the module
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, RoleDefinition):
                return attr

        raise ValueError(f"No RoleDefinition instance found in {py_path}. Please export one.")


class TeamManager:
    """Loads team definitions from evo/teams and provides sane defaults."""

    def __init__(self, evo_root: str | Path):
        self._root = Path(evo_root)

    def list_teams(self) -> list[str]:
        teams_dir = self._root / "teams"
        if not teams_dir.exists():
            return []
        return [p.stem for p in teams_dir.glob("*.py") if not p.name.startswith("_")]

    def resolve(self, name: str) -> TeamDefinition:
        team_name = (name or "default").strip() or "default"
        py_path = self._root / "teams" / f"{team_name}.py"
        if not py_path.exists():
            if team_name == "default":
                return TeamDefinition(
                    name="default",
                    description="Default planning and execution team",
                    roles=["default"],
                    planner_role="planner",
                    eval_role="evaluator",
                )
            raise FileNotFoundError(f"Team definition not found: {team_name} (expected {py_path})")

        module_name = f"_evo_teams_{team_name}"
        spec = importlib.util.spec_from_file_location(module_name, py_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load team module from {py_path}")

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            logger.error(f"Failed to execute team module {py_path}: {exc}")
            raise RuntimeError(f"Error loading team '{team_name}': {exc}") from exc

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, TeamDefinition):
                return attr

        raise ValueError(f"No TeamDefinition instance found in {py_path}. Please export one.")

"""Role resolver — reads Role definitions from the evolvable repository.

A Role is a convenience template used only at the plan/spawn stage.
``RoleResolver.resolve()`` expands a role name into a ``JobSpec`` — the
flat, self-contained execution configuration that the runtime consumes.
After expansion the role name is no longer needed; the runtime operates
solely on the ``JobSpec``.

Roles support single-level inheritance via the ``inherits`` field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class JobSpec:
    """Fully resolved execution specification consumed by the runtime.

    This is the *only* object ``run_job()`` depends on at execution time.
    It is produced by expanding a Role template (or constructed directly
    when a caller supplies a complete configuration without using roles).
    """

    prompt: str
    context_template: dict
    builtin_tools: list[str]
    custom_tools: list[dict]
    source_role: str = ""  # informational only; not used at execution time


# Backwards-compatible alias — will be removed in a future release.
ResolvedRole = JobSpec


class RoleResolver:
    """Expands Role templates from a checked-out evolvable repository into JobSpecs."""

    def __init__(self, evo_root: str | Path):
        self._root = Path(evo_root)

    def resolve(self, role_name: str) -> JobSpec:
        """Expand a role template into a flat JobSpec."""
        role_data = self._load_role(role_name)

        # Handle single-level inheritance
        if "inherits" in role_data:
            parent_data = self._load_role(role_data["inherits"])
            role_data = self._merge(parent_data, role_data)

        prompt_text = self._load_file(role_data["prompt"])
        context_template = self._load_yaml(role_data.get("context", "contexts/default.yaml"))

        tools = role_data.get("tools", {})
        builtin_tools = tools.get("builtin", [])
        custom_tools = [self._load_yaml(f"tools/{t}.yaml") for t in tools.get("custom", [])]

        return JobSpec(
            prompt=prompt_text,
            context_template=context_template,
            builtin_tools=builtin_tools,
            custom_tools=custom_tools,
            source_role=role_data.get("name", role_name),
        )

    def list_roles(self) -> list[str]:
        """List available role names."""
        roles_dir = self._root / "roles"
        if not roles_dir.exists():
            return []
        return [p.stem for p in roles_dir.glob("*.yaml")]

    def _load_role(self, name: str) -> dict:
        path = self._root / "roles" / f"{name}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Role not found: {name}")
        return yaml.safe_load(path.read_text()) or {}

    def _load_file(self, rel_path: str) -> str:
        path = self._root / rel_path
        if not path.exists():
            raise FileNotFoundError(f"File not found in evolvable repo: {rel_path}")
        return path.read_text()

    def _load_yaml(self, rel_path: str) -> dict:
        return yaml.safe_load(self._load_file(rel_path)) or {}

    @staticmethod
    def _merge(parent: dict, child: dict) -> dict:
        """Merge child role onto parent. Child fields override parent."""
        merged = {**parent}
        for key, value in child.items():
            if key == "inherits":
                continue
            merged[key] = value
        return merged

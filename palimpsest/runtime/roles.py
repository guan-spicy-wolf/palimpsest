"""Role resolver — loads decorator-based role functions from the evolvable repo."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from loguru import logger
from palimpsest.config import PublicationConfig, WorkspaceConfig
from yoitsu_contracts.role_metadata import RoleMetadata, RoleMetadataReader


@dataclass
class JobSpec:
    workspace_fn: Callable[..., Any]
    context_fn: Callable[..., dict]
    publication_fn: Callable[..., str | None]
    tools: list[str] = field(default_factory=list)
    source_role: str = ""


@dataclass
class TeamDefinition:
    name: str
    description: str
    roles: list[str] = field(default_factory=list)
    planner_role: str = "planner"
    eval_role: str = "evaluator"
    worker_roles: list[str] = field(default_factory=list)


def workspace_config(
    *,
    repo: str = "",
    init_branch: str = "main",
    new_branch: bool = True,
    depth: int = 1,
) -> Callable[..., WorkspaceConfig]:
    """Build workspace configuration.

    Per ADR-0007: accepts explicit 'goal' parameter (ignored, as goal is not
    workspace config). 'repo' and 'init_branch' come from spawn payload or role defaults.
    """
    def fn(*, goal: str = "", repo: str = "", init_branch: str = "", new_branch: bool = True, depth: int = depth, **params: Any) -> WorkspaceConfig:
        # ADR-0007 D4: repo and init_branch come from spawn payload via explicit
        # kwargs (set by runner from JobConfig.workspace). params fallback removed.
        return WorkspaceConfig(
            repo=str(repo),
            init_branch=str(init_branch or "main"),
            new_branch=bool(params.get("new_branch", new_branch)),
            depth=int(params.get("depth", depth)),
        )

    return fn


def git_publication(
    *,
    strategy: str = "branch",
    branch_prefix: str = "palimpsest/job",
) -> Callable[..., str | None]:
    def fn(
        *,
        result: dict[str, Any],
        workspace_path: str,
        job_id: str,
        task_id: str,
        goal: str,
        git_token_env: str = "",
        base_sha: str = "",
        **params: Any,
    ) -> str | None:
        import git

        from palimpsest.stages.finalization import find_publication_issues
        from palimpsest.stages.publication import PublicationGuardrailViolation, publish_results

        config = PublicationConfig(
            strategy=str(params.get("publication_strategy", strategy) or strategy),
            branch_prefix=str(params.get("branch_prefix", branch_prefix) or branch_prefix),
        )
        if result.get("status") == "failed":
            return None
        if config.strategy == "skip":
            return None

        try:
            repo = git.Repo(workspace_path)
        except git.InvalidGitRepositoryError:
            return None

        issues = find_publication_issues(repo, base_sha=base_sha)
        if issues:
            raise PublicationGuardrailViolation(issues)

        return publish_results(
            job_id,
            task_id,
            goal,
            result,
            workspace_path,
            config,
            git_token_env=git_token_env,
        )

    fn.__publication_strategy__ = strategy
    fn.__publication_branch_prefix__ = branch_prefix

    return fn


def context_spec(
    system: str,
    sections: list[dict[str, Any]],
) -> Callable[..., dict]:
    """Build context specification for LLM.

    Per ADR-0007: accepts explicit 'goal' parameter (not via **params).
    'task' is accepted as an alias for backward compatibility.
    """
    def fn(*, goal: str = "", task: str = "", **params: Any) -> dict:
        effective_goal = goal or task
        return {
            "system": system,
            "sections": list(sections),
            "task": effective_goal,
        }

    return fn


def role(
    *,
    name: str,
    description: str,
    teams: list[str] | None = None,
    role_type: str = "worker",
    min_cost: float = 0.0,
    recommended_cost: float = 0.0,
    max_cost: float = 10.0,  # ADR-0004 D1a: per-job ceiling for spawn-time validation
    min_capability: str = "",
) -> Callable[[Callable[..., JobSpec]], Callable[..., JobSpec]]:
    """Decorator for role functions.

    Per ADR-0007: all arguments must be literal expressions (string/numeric).
    This allows AST-based metadata extraction via RoleMetadataReader.
    """
    def decorator(func: Callable[..., JobSpec]) -> Callable[..., JobSpec]:
        func.__role_metadata__ = RoleMetadata(
            name=name,
            description=description,
            teams=list(teams or ["default"]),
            role_type=role_type,
            min_cost=min_cost,
            recommended_cost=recommended_cost,
            max_cost=max_cost,  # ADR-0004 D1a
            min_capability=min_capability,
        )
        return func

    return decorator


class RoleManager(RoleMetadataReader):
    """Extends RoleMetadataReader with resolve() for full JobSpec execution.

    Per ADR-0007:
    - RoleMetadataReader (yoitsu-contracts): AST-based metadata extraction
    - RoleManager (palimpsest): executes role modules to produce JobSpec
    """

    def __init__(self, evo_root: str | Path) -> None:
        super().__init__(evo_root)

    def resolve(self, role_name: str, **params: Any) -> JobSpec:
        """Load and execute role module to produce JobSpec."""
        func = self._load_role_function(role_name)
        try:
            spec = func(**params)
        except TypeError as exc:
            raise RuntimeError(f"Error calling role '{role_name}': {exc}") from exc
        if not isinstance(spec, JobSpec):
            raise TypeError(f"Role '{role_name}' returned {type(spec).__name__}, expected JobSpec")
        spec.source_role = role_name
        return spec

    def list_roles(self) -> list[str]:
        return [meta.name for meta in self.list_definitions()]

    def _load_role_function(self, name: str) -> Callable[..., JobSpec]:
        func, _ = self._load_role_by_name(name)
        return func

    def _load_role_by_name(self, name: str) -> tuple[Callable[..., JobSpec], RoleMetadata]:
        py_path = self._root / "roles" / f"{name}.py"
        if not py_path.exists():
            raise FileNotFoundError(f"Role definition not found: {name} (expected {py_path})")
        return self._load_role_module(py_path, expected_name=name)

    def _load_role_module(
        self,
        py_path: Path,
        *,
        expected_name: str | None = None,
    ) -> tuple[Callable[..., JobSpec], RoleMetadata]:
        """Load and execute a role module to extract function and metadata."""
        module_name = f"_evo_roles_{py_path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, py_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load role module from {py_path}")

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            logger.error(f"Failed to execute role module {py_path}: {exc}")
            raise RuntimeError(f"Error loading role '{py_path.stem}': {exc}") from exc

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            meta = getattr(attr, "__role_metadata__", None)
            if callable(attr) and isinstance(meta, RoleMetadata):
                if expected_name and meta.name != expected_name:
                    continue
                return attr, meta

        raise ValueError(f"No @role-decorated function found in {py_path}")

class TeamManager:
    def __init__(self, evo_root: str | Path):
        self._root = Path(evo_root)
        self._roles = RoleManager(evo_root)

    def list_teams(self) -> list[str]:
        return sorted({team for meta in self._roles.list_definitions() for team in meta.teams})

    def resolve(self, name: str) -> TeamDefinition:
        team_name = (name or "default").strip() or "default"
        members = [meta for meta in self._roles.list_definitions() if team_name in meta.teams]
        if not members:
            raise FileNotFoundError(f"No roles found for team {team_name!r}")

        planners = [meta.name for meta in members if meta.role_type == "planner"]
        evaluators = [meta.name for meta in members if meta.role_type == "evaluator"]
        workers = [meta.name for meta in members if meta.role_type == "worker"]

        if len(planners) != 1:
            raise ValueError(f"Team {team_name!r} must have exactly one planner role")
        if len(evaluators) > 1:
            raise ValueError(f"Team {team_name!r} must have at most one evaluator role")
        if not workers:
            raise ValueError(f"Team {team_name!r} must have at least one worker role")

        return TeamDefinition(
            name=team_name,
            description=f"Derived team {team_name}",
            roles=[meta.name for meta in members],
            planner_role=planners[0],
            eval_role=evaluators[0] if evaluators else "evaluator",
            worker_roles=workers,
        )

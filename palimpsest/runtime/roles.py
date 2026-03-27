"""Role resolver — loads decorator-based role functions from the evolvable repo."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from loguru import logger
from palimpsest.config import PublicationConfig, WorkspaceConfig


@dataclass
class RoleMetadata:
    name: str
    description: str
    teams: list[str] = field(default_factory=lambda: ["default"])
    role_type: str = "worker"
    min_cost: float = 0.0
    recommended_cost: float = 0.0
    min_capability: str = ""


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
    def fn(**params: Any) -> WorkspaceConfig:
        return WorkspaceConfig(
            repo=str(params.get("repo", repo) or repo),
            init_branch=str(params.get("branch", params.get("init_branch", init_branch)) or init_branch),
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
    def fn(**params: Any) -> dict:
        return {
            "system": system,
            "sections": list(sections),
            "task": str(params.get("goal", params.get("task", ""))),
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
    min_capability: str = "",
) -> Callable[[Callable[..., JobSpec]], Callable[..., JobSpec]]:
    def decorator(func: Callable[..., JobSpec]) -> Callable[..., JobSpec]:
        func.__role_metadata__ = RoleMetadata(
            name=name,
            description=description,
            teams=list(teams or ["default"]),
            role_type=role_type,
            min_cost=min_cost,
            recommended_cost=recommended_cost,
            min_capability=min_capability,
        )
        return func

    return decorator


class RoleManager:
    def __init__(self, evo_root: str | Path):
        self._root = Path(evo_root)

    def resolve(self, role_name: str, **params: Any) -> JobSpec:
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

    def list_definitions(self) -> list[RoleMetadata]:
        roles_dir = self._root / "roles"
        if not roles_dir.exists():
            return []
        result: list[RoleMetadata] = []
        for py_path in sorted(roles_dir.glob("*.py")):
            if py_path.name.startswith("_"):
                continue
            try:
                _, meta = self._load_role_module(py_path)
                result.append(meta)
            except Exception as exc:
                logger.error(f"Failed to load role metadata from {py_path}: {exc}")
        return result

    def get_definition(self, name: str) -> RoleMetadata:
        _, meta = self._load_role_by_name(name)
        return meta

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

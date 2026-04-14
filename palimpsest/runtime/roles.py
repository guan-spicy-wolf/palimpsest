"""Role resolver — loads decorator-based role functions from bundle directories."""

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
    """Job specification from role definition.
    
    Per ADR-0009: preparation_fn is the canonical name for workspace setup.
    Per ADR-0016: needs lists capabilities; capability path replaces preparation_fn/publication_fn.
    workspace_fn is accepted as an alternative for backward compatibility.
    
    Validation is deferred to RoleManager.resolve() to allow needs propagation.
    """
    preparation_fn: Callable[..., Any] | None = None  # ADR-0009: canonical name
    context_fn: Callable[..., dict] | None = None
    publication_fn: Callable[..., str | None] | None = None
    tools: list[str] = field(default_factory=list)
    source_role: str = ""
    workspace_fn: Callable[..., Any] | None = None  # Backward compatibility
    needs: list[str] = field(default_factory=list)  # ADR-0016: capabilities required
    
    def __post_init__(self):
        # Handle backward compatibility: if workspace_fn is provided but not preparation_fn,
        # use workspace_fn as preparation_fn
        if self.preparation_fn is None and self.workspace_fn is not None:
            self.preparation_fn = self.workspace_fn
        # Validation deferred to RoleManager.resolve() for needs propagation
        # context_fn is always required
        if self.context_fn is None:
            raise ValueError("JobSpec requires context_fn")


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
) -> Callable[..., tuple[str | None, list]]:
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
    ) -> tuple[str | None, list]:
        """Git commit/push and artifact binding creation.

        Returns:
            (git_ref, artifact_bindings) tuple.
        """
        import git
        from pathlib import Path

        from palimpsest.stages.finalization import find_publication_issues
        from palimpsest.stages.publication import (
            PublicationGuardrailViolation,
            publish_results,
        )

        config = PublicationConfig(
            strategy=str(params.get("publication_strategy", strategy) or strategy),
            branch_prefix=str(params.get("branch_prefix", branch_prefix) or branch_prefix),
        )
        if result.get("status") == "failed":
            return None, []
        if config.strategy == "skip":
            return None, []

        try:
            repo = git.Repo(workspace_path)
        except git.InvalidGitRepositoryError:
            # Repoless workspace: create artifact bindings without Git
            from pathlib import Path
            from palimpsest.stages.publication import create_artifact_bindings
            artifact_bindings = create_artifact_bindings(workspace_path)
            return None, artifact_bindings

        issues = find_publication_issues(repo, base_sha=base_sha)
        if issues:
            raise PublicationGuardrailViolation(issues)

        git_ref, artifact_bindings = publish_results(
            job_id,
            task_id,
            goal,
            result,
            workspace_path,
            config,
            git_token_env=git_token_env,
        )
        return git_ref, artifact_bindings

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
    teams: list[str] | None = None,  # DEPRECATED: ignored per Bundle MVP
    role_type: str = "worker",
    min_cost: float = 0.0,
    recommended_cost: float = 0.0,
    max_cost: float = 10.0,  # ADR-0004 D1a: per-job ceiling for spawn-time validation
    min_capability: str = "",
    needs: list[str] | None = None,  # ADR-0016: capabilities required
    contexts: list[str] | None = None,  # Context providers for LLM prompt
    output_authority: str = "",  # ADR-0019: "repository", "live_runtime", "analysis"
) -> Callable[[Callable[..., JobSpec]], Callable[..., JobSpec]]:
    """Decorator for role functions.

    Per ADR-0007: all arguments must be literal expressions (string/numeric).
    This allows AST-based metadata extraction via RoleMetadataReader.
    
    Per Bundle MVP: 'teams' parameter is deprecated and ignored. Role membership
    is determined by bundle directory location: evo/<bundle>/roles/<name>.py
    
    Per ADR-0016: 'needs' lists capabilities required by this role.
    Per architecture.md: 'contexts' lists context providers for LLM prompt.
    """
    def decorator(func: Callable[..., JobSpec]) -> Callable[..., JobSpec]:
        func.__role_metadata__ = RoleMetadata(
            name=name,
            description=description,
            teams=[],  # Deprecated field - empty per Bundle MVP
            role_type=role_type,
            min_cost=min_cost,
            recommended_cost=recommended_cost,
            max_cost=max_cost,  # ADR-0004 D1a
            min_capability=min_capability,
            needs=needs or [],
            contexts=contexts or [],
            output_authority=output_authority,  # ADR-0019
        )
        return func

    return decorator


class RoleManager(RoleMetadataReader):
    """Extends RoleMetadataReader with resolve() for full JobSpec execution.

    Per ADR-0007:
    - RoleMetadataReader (yoitsu-contracts): AST-based metadata extraction
    - RoleManager (palimpsest): executes role modules to produce JobSpec

    Per Bundle MVP:
    - Bundle-only resolution: evo/<bundle>/roles/<name>.py
    - No global fallback, no team layer
    - Missing role is a hard error
    """

    def __init__(self, bundle_root: str | Path, bundle: str = "") -> None:
        super().__init__(bundle_root, bundle=bundle)
        self._roles_dir = self._root / "roles"

    def resolve(self, role_name: str, **params: Any) -> JobSpec:
        """Load and execute role module to produce JobSpec.

        Per Bundle MVP: Loads from evo/<bundle>/roles/<name>.py only.
        Per ADR-0016: Propagates needs from RoleMetadata to JobSpec.
        """
        func = self._load_role_function(role_name)
        # ADR-0016: Extract needs from role metadata
        metadata = getattr(func, "__role_metadata__", None)
        metadata_needs = list(metadata.needs) if metadata and hasattr(metadata, "needs") else []
        
        # Inject needs into params for roles that accept it
        if metadata_needs:
            params.setdefault("needs", metadata_needs)
        
        try:
            spec = func(**params)
        except TypeError as exc:
            raise RuntimeError(f"Error calling role '{role_name}': {exc}") from exc
        if not isinstance(spec, JobSpec):
            raise TypeError(f"Role '{role_name}' returned {type(spec).__name__}, expected JobSpec")
        spec.source_role = role_name
        
        # ADR-0016: Ensure needs from metadata is set on JobSpec
        # (role functions may not pass needs parameter to JobSpec constructor)
        if metadata_needs and not spec.needs:
            spec.needs = metadata_needs
        
        # ADR-0018: Enforce capability-only model (no legacy hooks)
        bundle_name = self._bundle or ""
        role_key = f"{bundle_name}:{role_name}"

        if spec.preparation_fn is not None:
            raise ValueError(
                f"Role '{role_key}' uses deprecated preparation_fn. "
                f"Per ADR-0018, roles must use capability-only model. "
                f"Use needs=[] for analysis-only roles or needs=['git_workspace'] for repo roles."
            )
        if spec.publication_fn is not None:
            raise ValueError(
                f"Role '{role_key}' uses deprecated publication_fn. "
                f"Per ADR-0018, roles must use capability-only model. "
                f"Publication is handled by git_workspace capability."
            )
        
        return spec

    def get_definition(self, name: str) -> RoleMetadata | None:
        """Get a specific role definition by name from bundle.

        Per ADR-0015: Looks in bundle_workspace/roles/<name>.py.
        """
        role_path = self._roles_dir / f"{name}.py"
        if role_path.exists():
            return self._read_role_file(role_path)
        return None

    def list_roles(self) -> list[str]:
        return [meta.name for meta in self.list_definitions()]

    def list_definitions(self) -> list[RoleMetadata]:
        """List all role definitions in the bundle.

        Per ADR-0015: Scans bundle_workspace/roles/.
        """
        if not self._roles_dir.exists():
            return []

        result: list[RoleMetadata] = []
        for py_path in sorted(self._roles_dir.glob("*.py")):
            if py_path.name.startswith("_"):
                continue
            meta = self._read_role_file(py_path)
            if meta:
                result.append(meta)
        return result

    def _load_role_function(self, name: str) -> Callable[..., JobSpec]:
        func, _ = self._load_role_by_name(name)
        return func

    def _load_role_by_name(self, name: str) -> tuple[Callable[..., JobSpec], RoleMetadata]:
        """Load role module from bundle directory.

        Per ADR-0015: Looks in bundle_workspace/roles/<name>.py.
        Raises FileNotFoundError if not found.
        """
        role_path = self._roles_dir / f"{name}.py"
        if not role_path.exists():
            raise FileNotFoundError(
                f"Role '{name}' not found in bundle workspace "
                f"(expected {role_path})"
            )
        return self._load_role_module(role_path, expected_name=name)

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
"""Preparation stage — workspace setup before agent loop (ADR-0009).

This module was renamed from workspace.py per ADR-0009 Decision 1.
The setup_workspace function is retained as an alias for run_preparation.
"""

from __future__ import annotations

import tempfile
import base64
import os
from pathlib import Path

import git
from loguru import logger

from palimpsest.config import WorkspaceConfig, PreparationConfig
from palimpsest.events import JobStartedData
from palimpsest.runtime.event_gateway import EventGateway
from yoitsu_contracts.artifact import ArtifactBinding
from yoitsu_contracts.local_fs_backend import LocalFSBackend

_DEFAULT_GIT_USER_NAME = "Palimpsest Agent"
_DEFAULT_GIT_USER_EMAIL = "palimpsest@local.invalid"


def _slugify_goal(goal: str, *, max_length: int = 48) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "-" for ch in (goal or "").strip())
    text = "-".join(part for part in text.split("-") if part)
    return (text[:max_length].rstrip("-") or "task")


def branch_name_for_task(branch_prefix: str, task_id: str, goal: str) -> str:
    task_token = (task_id or "task")[:12]
    slug = _slugify_goal(goal)
    return f"{branch_prefix}/{task_token}/{slug}"


def run_preparation(
    job_id: str,
    config: WorkspaceConfig | PreparationConfig,
    branch_prefix: str = "palimpsest/job",
    *,
    task_id: str = "",
    goal: str = "",
    gateway: EventGateway | None = None,
    evo_sha: str = "",
    cost_tracking_degraded: bool = False,
) -> str:
    """Run preparation: clone repo, create job branch. Returns workspace path.

    Per ADR-0009: this is the canonical name for workspace setup.
    The preparation function handles deterministic setup before the agent loop.

    When *gateway* is provided, emits stage-transition and job-started
    events so the runner does not have to.
    """
    if gateway:
        from palimpsest.events import StageTransitionData
        gateway.emit(StageTransitionData(from_stage="init", to_stage="workspace"))

    workspace_path = tempfile.mkdtemp(prefix="palimpsest-")
    logger.info(f"Created workspace: {workspace_path}")

    # ADR-0013: Clone first if repo exists, then materialize artifacts
    # This avoids "destination path already exists" when both repo and artifacts are set
    if config.repo:
        logger.info(f"Cloning {config.repo} branch={config.init_branch}")
        clone_kwargs = {
            "branch": config.init_branch,
            "depth": config.depth,
        }

        # Environment-first: if GIT_CONFIG_COUNT is set, the runtime environment
        # (e.g. Trenni isolation layer) has already configured git credentials.
        # Fallback: use git_token_env for standalone/development usage.
        if not os.environ.get("GIT_CONFIG_COUNT"):
            token_env = getattr(config, "git_token_env", "")
            token = os.environ.get(token_env, "") if token_env else ""
            if token:
                auth_str = f"x-access-token:{token}"
                b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
                clone_kwargs["c"] = f"http.extraHeader=AUTHORIZATION: basic {b64_auth}"

        try:
            repo = git.Repo.clone_from(
                config.repo,
                workspace_path,
                allow_unsafe_options=True,
                **clone_kwargs,
            )
        except Exception as exc:
            # ADR-0010: emit preparation_failure observation
            if gateway:
                from yoitsu_contracts.observation import (
                    ObservationPreparationFailureData,
                    OBSERVATION_PREPARATION_FAILURE,
                )
                gateway.emit(
                    OBSERVATION_PREPARATION_FAILURE,
                    ObservationPreparationFailureData(
                        task_id=task_id or job_id,
                        job_id=job_id,
                        role="",  # role not available in preparation stage
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    ).model_dump(),
                )
            raise
    else:
        logger.info("Using repoless scratch workspace")
        repo = None

    # ADR-0013: Materialize input artifacts after clone (or for repoless workspace)
    if hasattr(config, 'input_artifacts') and config.input_artifacts:
        _materialize_input_artifacts(config.input_artifacts, workspace_path)
    
    if repo is not None and config.repo:
        _ensure_repo_identity(repo)

    if repo is not None and config.new_branch:
        job_branch = branch_name_for_task(
            branch_prefix,
            task_id or job_id,
            goal,
        )
        repo.git.checkout("-b", job_branch)
        logger.info(f"Created branch: {job_branch}")
    elif repo is not None:
        logger.info(f"Working directly on branch: {config.init_branch}")

    if gateway:
        base_sha = _read_head_sha(workspace_path)
        gateway.emit(
            JobStartedData(
                workspace_path=workspace_path,
                evo_sha=evo_sha,
                base_sha=base_sha,
                cost_tracking_degraded=cost_tracking_degraded,
            )
        )

    return workspace_path


# Backward compatibility alias (ADR-0009)
setup_workspace = run_preparation


def _read_head_sha(workspace: str) -> str:
    """Return the HEAD SHA of a git workspace, or empty string."""
    try:
        return git.Repo(workspace).head.commit.hexsha
    except Exception:
        return ""


def _ensure_repo_identity(repo: git.Repo) -> None:
    """Ensure the workspace repo has a usable commit identity.

    Containers and CI environments often lack global git config. Set a local
    per-repo identity unless one is already configured.
    """
    reader = repo.config_reader(config_level="repository")
    has_name = reader.has_option("user", "name")
    has_email = reader.has_option("user", "email")
    if has_name and has_email:
        return

    user_name = os.environ.get("PALIMPSEST_GIT_USER_NAME") or os.environ.get("GIT_AUTHOR_NAME") or _DEFAULT_GIT_USER_NAME
    user_email = os.environ.get("PALIMPSEST_GIT_USER_EMAIL") or os.environ.get("GIT_AUTHOR_EMAIL") or _DEFAULT_GIT_USER_EMAIL

    with repo.config_writer() as writer:
        if not has_name:
            writer.set_value("user", "name", user_name)
        if not has_email:
            writer.set_value("user", "email", user_email)


def _materialize_input_artifacts(
    artifacts: list[ArtifactBinding],
    workspace_path: str,
) -> None:
    """Materialize input artifacts into workspace (ADR-0013).

    Args:
        artifacts: List of ArtifactBinding to materialize.
        workspace_path: Target workspace directory.
    """
    # Determine artifact store root from environment or default
    store_root = Path(os.environ.get("PALIMPSEST_ARTIFACT_STORE", "~/.cache/palimpsest/artifacts"))
    store_root = store_root.expanduser()

    backend = LocalFSBackend(store_root)

    for binding in artifacts:
        ref = binding.ref
        target_path = Path(workspace_path) / binding.path if binding.path else Path(workspace_path)

        if ref.object_kind == "tree":
            # Materialize directory tree
            logger.info(f"Materializing tree artifact {ref.digest} to {target_path}")
            target_path.mkdir(parents=True, exist_ok=True)
            backend.materialize_tree(ref, target_path)
        elif ref.object_kind == "blob":
            # Write single file
            logger.info(f"Materializing blob artifact {ref.digest} to {target_path}")
            data = backend.retrieve_blob(ref)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(data)
        else:
            logger.warning(f"Unknown artifact kind: {ref.object_kind}, skipping")
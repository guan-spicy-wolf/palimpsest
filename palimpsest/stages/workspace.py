from __future__ import annotations

import tempfile
import base64
import os
from pathlib import Path

import git
from loguru import logger

from palimpsest.config import WorkspaceConfig
from palimpsest.events import JobStartedData
from palimpsest.runtime.event_gateway import EventGateway

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


def setup_workspace(
    job_id: str,
    config: WorkspaceConfig,
    branch_prefix: str = "palimpsest/job",
    *,
    task_id: str = "",
    goal: str = "",
    gateway: EventGateway | None = None,
    evo_sha: str = "",
    cost_tracking_degraded: bool = False,
) -> str:
    """Clone repo and create job branch. Returns workspace path.

    When *gateway* is provided, emits stage-transition and job-started
    events so the runner does not have to.
    """
    if gateway:
        from palimpsest.events import StageTransitionData
        gateway.emit(StageTransitionData(from_stage="init", to_stage="workspace"))

    workspace_path = tempfile.mkdtemp(prefix="palimpsest-")
    logger.info(f"Created workspace: {workspace_path}")

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

        repo = git.Repo.clone_from(
            config.repo,
            workspace_path,
            allow_unsafe_options=True,
            **clone_kwargs,
        )
    else:
        logger.info("Using repoless scratch workspace")
        repo = None
    
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

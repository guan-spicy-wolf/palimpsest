from __future__ import annotations

import base64
import os

import git
from loguru import logger

from palimpsest.config import PublicationConfig


def _push_auth_environment(git_token_env: str) -> dict[str, str]:
    """Build transient git config env for authenticated HTTPS pushes.

    Returns a dict to be merged with os.environ. The empty first value clears
    any inherited ``http.extraHeader`` entries so the final request contains
    only one Authorization header.

    Returns an empty dict when:
    - GIT_CONFIG_COUNT is already set (runtime environment handles auth), or
    - no token is available.
    """
    # Environment-first: if the runtime environment already configured
    # git credentials via GIT_CONFIG_*, skip the fallback.
    if os.environ.get("GIT_CONFIG_COUNT"):
        return {}

    token = os.environ.get(git_token_env, "") if git_token_env else ""
    if not token:
        return {}

    auth_str = f"x-access-token:{token}"
    b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
    return {
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "http.extraHeader",
        "GIT_CONFIG_VALUE_1": f"AUTHORIZATION: basic {b64_auth}",
    }


def publish_results(
    job_id: str,
    result: dict,
    workspace_path: str,
    config: PublicationConfig,
    *,
    git_token_env: str = "",
) -> str | None:
    """Git commit and push. Returns git_ref 'branch:sha' or None.

    Raises on failure — the caller (runner.py) handles the exception
    and emits the appropriate job_failed event.
    """
    if result.get("status") == "failed":
        logger.warning("Skipping publication for failed job")
        return None
    if config.strategy == "skip":
        logger.info("Skipping publication because publication.strategy=skip")
        return None

    try:
        repo = git.Repo(workspace_path)
    except git.InvalidGitRepositoryError:
        logger.info("Skipping publication for repoless workspace")
        return None
    repo.git.add("-A")

    status = result.get("status", "completed")
    summary = result.get("summary", "")[:500]
    commit_prefix = "feat"
    if repo.is_dirty(index=True) or repo.untracked_files:
        commit = repo.index.commit(f"{commit_prefix}: palimpsest job {job_id}\n\n{summary}")
        logger.info(f"Committed {commit.hexsha[:8]}")
    else:
        repo.git.commit("--allow-empty", "-m", f"chore: palimpsest job {job_id} (no changes)")
        commit = repo.head.commit
        logger.info(f"Empty commit {commit.hexsha[:8]}")

    branch_name = repo.active_branch.name
    git_ref = f"{branch_name}:{commit.hexsha}"

    if repo.remotes:
        logger.info(f"Pushing {branch_name}")
        auth_env = _push_auth_environment(git_token_env)
        if auth_env:
            # Merge with os.environ so git still has PATH, HOME, etc.
            merged_env = {**os.environ, **auth_env}
            repo.git.execute(
                ["git", "push", "--porcelain", "--", repo.remotes[0].name, branch_name],
                env=merged_env,
            )
        else:
            repo.git.push("--porcelain", "--", repo.remotes[0].name, branch_name)
    else:
        logger.warning("No remote configured, skipping push")

    return git_ref

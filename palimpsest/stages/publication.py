from __future__ import annotations

import base64
import os
from contextlib import nullcontext

import git
from loguru import logger

from palimpsest.config import PublicationConfig


def _push_auth_environment(git_token_env: str) -> dict[str, str]:
    """Build transient git config env for authenticated HTTPS pushes."""
    token = os.environ.get(git_token_env, "") if git_token_env else ""
    if not token:
        return {}

    auth_str = f"x-access-token:{token}"
    b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {b64_auth}",
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

    repo = git.Repo(workspace_path)
    repo.git.add("-A")

    status = result.get("status", "success")
    summary = result.get("summary", "")[:500]
    commit_prefix = "wip" if status == "partial" else "feat"
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
        auth_ctx = repo.git.custom_environment(**auth_env) if auth_env else nullcontext()
        with auth_ctx:
            repo.remotes[0].push(branch_name)
    else:
        logger.warning("No remote configured, skipping push")

    return git_ref

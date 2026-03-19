from __future__ import annotations

import git
from loguru import logger

from palimpsest.config import PublicationConfig


def publish_results(
    job_id: str,
    result: dict,
    workspace_path: str,
    config: PublicationConfig,
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

    summary = result.get("summary", "")[:500]
    if repo.is_dirty(index=True) or repo.untracked_files:
        commit = repo.index.commit(f"feat: palimpsest job {job_id}\n\n{summary}")
        logger.info(f"Committed {commit.hexsha[:8]}")
    else:
        repo.git.commit("--allow-empty", "-m", f"chore: palimpsest job {job_id} (no changes)")
        commit = repo.head.commit
        logger.info(f"Empty commit {commit.hexsha[:8]}")

    branch_name = repo.active_branch.name
    git_ref = f"{branch_name}:{commit.hexsha}"

    if repo.remotes:
        logger.info(f"Pushing {branch_name}")
        repo.remotes[0].push(branch_name)
    else:
        logger.warning("No remote configured, skipping push")

    return git_ref

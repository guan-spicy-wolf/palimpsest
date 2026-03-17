from __future__ import annotations

import tempfile
from pathlib import Path

import git
from loguru import logger

from palimpsest.config import WorkspaceConfig


def setup_workspace(job_id: str, config: WorkspaceConfig, branch_prefix: str = "palimpsest/job") -> str:
    """Clone repo and create job branch. Returns workspace path."""
    workspace_path = tempfile.mkdtemp(prefix="palimpsest-")
    logger.info(f"Created workspace: {workspace_path}")

    if config.repo:
        logger.info(f"Cloning {config.repo} branch={config.branch}")
        repo = git.Repo.clone_from(
            config.repo,
            workspace_path,
            branch=config.branch,
            depth=config.depth,
        )
    else:
        logger.info("Initializing empty repo")
        repo = git.Repo.init(workspace_path)
        dummy = Path(workspace_path) / ".palimpsest"
        dummy.write_text(f"job_id: {job_id}\n")
        repo.index.add([".palimpsest"])
        repo.index.commit(f"init: workspace for job {job_id}")

    job_branch = f"{branch_prefix}/{job_id}"
    repo.git.checkout("-b", job_branch)
    logger.info(f"Created branch: {job_branch}")

    return workspace_path

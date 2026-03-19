from __future__ import annotations

import tempfile
import base64
import os
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
        clone_kwargs = {
            "branch": config.branch,
            "depth": config.depth,
        }
        
        token_env = getattr(config, "git_token_env", "")
        token = os.environ.get(token_env, "") if token_env else ""
        if token:
            # Injecting token as HTTP basic auth extra header avoids logging and URL leaks
            auth_str = f"x-access-token:{token}"
            b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
            clone_kwargs["c"] = f"http.extraHeader=AUTHORIZATION: basic {b64_auth}"
        
        repo = git.Repo.clone_from(
            config.repo,
            workspace_path,
            **clone_kwargs,
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

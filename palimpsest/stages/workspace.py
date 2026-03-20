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


def setup_workspace(
    job_id: str,
    config: WorkspaceConfig,
    branch_prefix: str = "palimpsest/job",
    gateway: EventGateway | None = None,
    evo_sha: str = "",
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

    if gateway:
        base_sha = _read_head_sha(workspace_path)
        gateway.emit(
            JobStartedData(
                workspace_path=workspace_path,
                evo_sha=evo_sha,
                base_sha=base_sha,
            )
        )

    return workspace_path


def _read_head_sha(workspace: str) -> str:
    """Return the HEAD SHA of a git workspace, or empty string."""
    try:
        return git.Repo(workspace).head.commit.hexsha
    except Exception:
        return ""

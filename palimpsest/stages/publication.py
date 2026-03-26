from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Literal

import git
from loguru import logger

from palimpsest.config import PublicationConfig

PublicationMode = Literal["branch_only", "pr_draft", "approval_required"]


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


def _write_completion_artifact(
    workspace_path: str,
    job_id: str,
    result: dict,
    git_ref: str | None,
    publication_config: PublicationConfig,
) -> str | None:
    """Write a normalized completion artifact to the workspace.

    Returns the path to the artifact file, or None if workspace is not a git repo.
    """
    try:
        repo = git.Repo(workspace_path)
    except git.InvalidGitRepositoryError:
        # For repoless workspaces, write artifact to workspace root
        artifact_path = Path(workspace_path) / ".palimpsest" / "completion.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        # Write to .palimpsest directory in repo root
        artifact_path = Path(workspace_path) / ".palimpsest" / "completion.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)

    # Determine publication mode and trust level
    mode = publication_config.mode
    trust_level = _determine_trust_level(mode, publication_config)

    artifact = {
        "schema_version": "1.0",
        "job_id": job_id,
        "status": result.get("status", "completed"),
        "code": result.get("code", ""),
        "summary": result.get("summary", ""),
        "git_ref": git_ref,
        "publication": {
            "mode": mode,
            "trust_level": trust_level,
            "requires_review": mode in ("pr_draft", "approval_required"),
            "auto_merge": mode == "branch_only" and publication_config.strategy == "branch",
        },
        "artifacts": {
            "files_changed": [],  # Populated below if in git repo
        },
    }

    # Add file changes if in a git repo
    try:
        repo = git.Repo(workspace_path)
        if git_ref:
            # Parse branch:sha from git_ref
            ref_parts = git_ref.split(":", 1)
            if len(ref_parts) == 2:
                commit_sha = ref_parts[1]
                try:
                    # Get diff against parent or empty tree for first commit
                    commit = repo.commit(commit_sha)
                    if commit.parents:
                        diff = commit.parents[0].diff(commit, create_patch=False)
                    else:
                        diff = commit.diff(git.NULL_TREE, create_patch=False)
                    
                    files_changed = []
                    for d in diff:
                        change_type = d.change_type if d.change_type else "M"
                        files_changed.append({
                            "path": d.a_path if d.a_path else d.b_path,
                            "change_type": change_type,
                        })
                    artifact["artifacts"]["files_changed"] = files_changed
                except Exception as e:
                    logger.debug(f"Could not compute file changes: {e}")
    except Exception:
        pass

    artifact_path.write_text(json.dumps(artifact, indent=2))
    logger.info(f"Wrote completion artifact: {artifact_path}")
    return str(artifact_path)


def _determine_trust_level(mode: PublicationMode, config: PublicationConfig) -> str:
    """Determine the trust level based on publication mode and config."""
    if mode == "approval_required":
        return "human_required"
    elif mode == "pr_draft":
        return "review_suggested"
    elif mode == "branch_only":
        return "automated"
    return "unknown"


def publish_results(
    job_id: str,
    result: dict,
    workspace_path: str,
    config: PublicationConfig,
    *,
    git_token_env: str = "",
) -> str | None:
    """Git commit and push. Returns git_ref 'branch:sha' or None.

    Handles different publication modes:
    - branch_only: Commit and push to branch, no PR created
    - pr_draft: Commit, push, and create draft PR (if supported by platform)
    - approval_required: Commit, push, and mark for human approval

    Also writes a normalized completion artifact to the workspace.

    Raises on failure — the caller (runner.py) handles the exception
    and emits the appropriate job_failed event.
    """
    if result.get("status") == "failed":
        logger.warning("Skipping publication for failed job")
        _write_completion_artifact(workspace_path, job_id, result, None, config)
        return None
    if config.strategy == "skip":
        logger.info("Skipping publication because publication.strategy=skip")
        _write_completion_artifact(workspace_path, job_id, result, None, config)
        return None

    try:
        repo = git.Repo(workspace_path)
    except git.InvalidGitRepositoryError:
        logger.info("Skipping publication for repoless workspace")
        # Still write artifact for repoless workspaces
        _write_completion_artifact(workspace_path, job_id, result, None, config)
        return None

    repo.git.add("-A")

    status = result.get("status", "completed")
    summary = result.get("summary", "")[:500]
    
    # Determine commit prefix based on publication mode
    commit_prefix = "feat"
    if config.mode == "approval_required":
        commit_prefix = "wip"  # Work-in-progress for approval required
    elif config.mode == "pr_draft":
        commit_prefix = "draft"
    
    if repo.is_dirty(index=True) or repo.untracked_files:
        commit_message = f"{commit_prefix}: palimpsest job {job_id}\n\n{summary}"
        if config.mode == "approval_required":
            commit_message += "\n\n[REQUIRES-HUMAN-APPROVAL]"
        elif config.mode == "pr_draft":
            commit_message += "\n\n[DRAFT-PR]"
        
        commit = repo.index.commit(commit_message)
        logger.info(f"Committed {commit.hexsha[:8]}")
    else:
        commit_message = f"chore: palimpsest job {job_id} (no changes)"
        if config.mode == "approval_required":
            commit_message += "\n\n[REQUIRES-HUMAN-APPROVAL]"
        elif config.mode == "pr_draft":
            commit_message += "\n\n[DRAFT-PR]"
        
        repo.git.commit("--allow-empty", "-m", commit_message)
        commit = repo.head.commit
        logger.info(f"Empty commit {commit.hexsha[:8]}")

    branch_name = repo.active_branch.name
    git_ref = f"{branch_name}:{commit.hexsha}"

    # Handle different publication modes
    if config.mode == "branch_only":
        logger.info(f"Publication mode: branch_only - pushing to {branch_name}")
    elif config.mode == "pr_draft":
        logger.info(f"Publication mode: pr_draft - pushing to {branch_name} (draft PR to be created)")
    elif config.mode == "approval_required":
        logger.info(f"Publication mode: approval_required - pushing to {branch_name} (awaiting approval)")

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
        
        # Log mode-specific guidance
        if config.mode == "pr_draft":
            logger.info(f"Draft PR should be created from {branch_name} to {config.pr_base}")
        elif config.mode == "approval_required":
            logger.info(f"Changes pushed to {branch_name} - awaiting human approval")
    else:
        logger.warning("No remote configured, skipping push")

    # Write completion artifact after successful push
    _write_completion_artifact(workspace_path, job_id, result, git_ref, config)

    return git_ref

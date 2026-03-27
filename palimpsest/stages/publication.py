from __future__ import annotations

import base64
import os
import re

import git
from loguru import logger

from palimpsest.config import PublicationConfig


class PublicationGuardrailViolation(Exception):
    def __init__(self, violations: list[str]):
        self.violations = violations
        super().__init__("Publication guardrails triggered:\n- " + "\n- ".join(violations))


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
    task_id: str,
    goal: str,
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

    summary = str(result.get("summary", "") or "").strip()[:500]
    code = str(result.get("code", "") or "")
    subject = _commit_subject(summary, goal, code=code)
    body = _commit_body(job_id=job_id, task_id=task_id, summary=summary, code=code)
    if repo.is_dirty(index=True) or repo.untracked_files:
        commit = repo.index.commit(f"{subject}\n\n{body}")
        logger.info(f"Committed {commit.hexsha[:8]}")
    else:
        repo.git.commit("--allow-empty", "-m", f"{subject}\n\n{body}\n\nNo workspace changes.")
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
        raise RuntimeError("Publication strategy requires a configured remote, but none was found")

    return git_ref


def _commit_subject(summary: str, goal: str, *, code: str = "") -> str:
    first_line = next((line.strip() for line in summary.splitlines() if line.strip()), "")
    text = first_line or goal.strip() or "completed job"
    text = re.sub(r"\s+", " ", text).strip()
    text = text[:72]
    if code == "budget_exhausted":
        return f"agent.job.completed (budget_exhausted): {text}"
    return f"agent.job.completed: {text}"


def _commit_body(*, job_id: str, task_id: str, summary: str, code: str = "") -> str:
    lines = [
        f"job_id: {job_id}",
        f"task_id: {task_id}",
    ]
    if code:
        lines.append(f"code: {code}")
    if summary:
        lines.extend(["", summary])
    return "\n".join(lines)

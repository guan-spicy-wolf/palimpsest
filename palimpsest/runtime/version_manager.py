"""Version manager — tracks the active commit of the evolvable repository.

The evolvable repo's Git commit SHA *is* the version number.  The
VersionManager maintains an "active commit" pointer and handles version
progression (when main advances) and rollback (when the first Job on a
new commit fails to start).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import git
from loguru import logger


@dataclass
class VersionEvent:
    """Emitted when the active commit changes."""

    old_sha: str
    new_sha: str
    changed_files: list[str]
    action: str  # "advance" | "rollback"


class VersionManager:
    """Manages the active commit pointer for the evolvable repository."""

    def __init__(self, evo_path: str | Path):
        self._evo_path = Path(evo_path)
        self._repo = git.Repo(self._evo_path)
        self._active_sha: str = self._repo.head.commit.hexsha
        self._last_known_good: str = self._active_sha

    @property
    def active_sha(self) -> str:
        return self._active_sha

    @property
    def last_known_good(self) -> str:
        return self._last_known_good

    def mark_job_success(self) -> None:
        """Record that a Job succeeded on the current active commit."""
        self._last_known_good = self._active_sha
        logger.info(f"Marked {self._active_sha[:8]} as last known good")

    def check_for_updates(self) -> VersionEvent | None:
        """Check if the evolvable repo's main branch has advanced.

        If a new commit is detected, advance the active pointer and return
        a VersionEvent describing the change.  Returns None if no change.
        """
        try:
            self._repo.remotes.origin.fetch()
        except Exception:
            logger.debug("No remote to fetch for evolvable repo")
            return None

        try:
            remote_sha = self._repo.remotes.origin.refs["main"].commit.hexsha
        except (IndexError, KeyError):
            return None

        if remote_sha == self._active_sha:
            return None

        old_sha = self._active_sha
        changed = self._diff_files(old_sha, remote_sha)

        self._repo.git.checkout(remote_sha)
        self._active_sha = remote_sha

        event = VersionEvent(
            old_sha=old_sha,
            new_sha=remote_sha,
            changed_files=changed,
            action="advance",
        )
        logger.info(
            f"Version advanced: {old_sha[:8]} -> {remote_sha[:8]} "
            f"({len(changed)} files changed)"
        )
        return event

    def rollback(self) -> VersionEvent | None:
        """Roll back to the last known good commit.

        Called when the first Job on a new commit fails to start.
        Returns None if already at the last known good.
        """
        if self._active_sha == self._last_known_good:
            return None

        old_sha = self._active_sha
        changed = self._diff_files(old_sha, self._last_known_good)

        self._repo.git.checkout(self._last_known_good)
        self._active_sha = self._last_known_good

        event = VersionEvent(
            old_sha=old_sha,
            new_sha=self._last_known_good,
            changed_files=changed,
            action="rollback",
        )
        logger.warning(f"Version rolled back: {old_sha[:8]} -> {self._last_known_good[:8]}")
        return event

    def _diff_files(self, sha_a: str, sha_b: str) -> list[str]:
        """List files changed between two commits."""
        try:
            diff = self._repo.git.diff("--name-only", sha_a, sha_b)
            return [f for f in diff.strip().split("\n") if f]
        except Exception:
            return []

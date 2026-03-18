"""Version reader — reads the current checkout of the evolvable repository.

The evolvable repo is treated as a plain git repo / submodule.  The
runtime reads whatever is currently checked out.  No version advancement,
rollback, or "last known good" tracking is performed at this stage.

When a complete version state machine is implemented in the future, this
module will be extended to support transparent version progression.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger


def read_evo_sha(evo_path: str | Path) -> str | None:
    """Return the HEAD SHA of the evolvable repo, or None on failure."""
    try:
        import git as _git

        repo = _git.Repo(Path(evo_path))
        return repo.head.commit.hexsha
    except Exception:
        logger.debug(f"Could not read HEAD of {evo_path}")
        return None

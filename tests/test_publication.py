import pytest
from unittest.mock import patch, MagicMock

import git

from palimpsest.stages.publication import publish_results
from palimpsest.config import PublicationConfig


def test_publication_commits_changes(tmp_path):
    """Normal case: changes are committed and git_ref returned."""
    repo = git.Repo.init(tmp_path)
    (tmp_path / "init.txt").write_text("init")
    repo.index.add(["init.txt"])
    repo.index.commit("init")
    repo.git.checkout("-b", "palimpsest/job/test-1")

    (tmp_path / "new.txt").write_text("content")

    config = PublicationConfig()
    result = {"status": "success", "summary": "test"}
    git_ref = publish_results("test-1", result, str(tmp_path), config)

    assert git_ref is not None
    assert "palimpsest/job/test-1:" in git_ref


def test_publication_skips_failed_job(tmp_path):
    """Failed jobs skip publication."""
    config = PublicationConfig()
    result = {"status": "failed"}
    git_ref = publish_results("test-2", result, str(tmp_path), config)
    assert git_ref is None


def test_publication_push_failure_propagates(tmp_path):
    """Push failure must propagate as an exception, not return None."""
    repo = git.Repo.init(tmp_path)
    (tmp_path / "init.txt").write_text("init")
    repo.index.add(["init.txt"])
    repo.index.commit("init")
    repo.git.checkout("-b", "palimpsest/job/test-3")

    config = PublicationConfig()
    result = {"status": "success", "summary": "test"}

    # Mock push to raise — gitpython push doesn't reliably raise on its own
    with patch.object(git.Remote, "push", side_effect=git.GitCommandError("push", "simulated failure")):
        repo.create_remote("origin", "https://example.com/repo.git")
        with pytest.raises(git.GitCommandError):
            publish_results("test-3", result, str(tmp_path), config)

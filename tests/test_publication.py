import pytest
from contextlib import nullcontext
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


def test_publication_push_uses_git_token_env_for_authenticated_https(monkeypatch, tmp_path):
    repo = git.Repo.init(tmp_path)
    (tmp_path / "init.txt").write_text("init")
    repo.index.add(["init.txt"])
    repo.index.commit("init")
    repo.git.checkout("-b", "palimpsest/job/test-4")
    repo.create_remote("origin", "https://example.com/repo.git")

    (tmp_path / "new.txt").write_text("content")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    config = PublicationConfig()
    result = {"status": "success", "summary": "test"}

    orig_call_process = git.cmd.Git._call_process

    def call_process_side_effect(self, method, *args, **kwargs):
        if method == "push":
            return ""
        return orig_call_process(self, method, *args, **kwargs)

    with (
        patch("palimpsest.stages.publication.git.Repo", return_value=repo),
        patch("git.cmd.Git.custom_environment", return_value=nullcontext()) as custom_env,
        patch.object(git.cmd.Git, "_call_process", autospec=True, side_effect=call_process_side_effect) as call_process,
    ):
        publish_results(
            "test-4",
            result,
            str(tmp_path),
            config,
            git_token_env="GITHUB_TOKEN",
        )

    custom_env.assert_called_once()
    assert call_process.call_args.args[1] == "push"
    env_kwargs = custom_env.call_args.kwargs
    assert env_kwargs["GIT_CONFIG_COUNT"] == "1"
    assert env_kwargs["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    assert env_kwargs["GIT_CONFIG_VALUE_0"].startswith("AUTHORIZATION: basic ")


def test_publication_push_skips_env_auth_when_repo_already_has_extra_header(monkeypatch, tmp_path):
    repo = git.Repo.init(tmp_path)
    (tmp_path / "init.txt").write_text("init")
    repo.index.add(["init.txt"])
    repo.index.commit("init")
    repo.git.checkout("-b", "palimpsest/job/test-5")
    repo.create_remote("origin", "https://example.com/repo.git")
    repo.git.config("http.extraHeader", "AUTHORIZATION: basic existing")

    (tmp_path / "new.txt").write_text("content")
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    config = PublicationConfig()
    result = {"status": "success", "summary": "test"}
    orig_call_process = git.cmd.Git._call_process

    def call_process_side_effect(self, method, *args, **kwargs):
        if method == "push":
            return ""
        return orig_call_process(self, method, *args, **kwargs)

    with (
        patch("palimpsest.stages.publication.git.Repo", return_value=repo),
        patch("git.cmd.Git.custom_environment", return_value=nullcontext()) as custom_env,
        patch.object(git.cmd.Git, "_call_process", autospec=True, side_effect=call_process_side_effect),
    ):
        publish_results(
            "test-5",
            result,
            str(tmp_path),
            config,
            git_token_env="GITHUB_TOKEN",
        )

    custom_env.assert_not_called()

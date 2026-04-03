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
    repo.create_remote("origin", "https://example.com/repo.git")
    orig_execute = git.cmd.Git.execute

    def execute_side_effect(self, command, *args, **kwargs):
        if command[:2] == ["git", "push"]:
            return ""
        return orig_execute(self, command, *args, **kwargs)

    with patch.object(git.cmd.Git, "execute", autospec=True, side_effect=execute_side_effect):
        git_ref, artifact_bindings = publish_results("test-1", "task-1", "Define shared graph contracts", result, str(tmp_path), config)

    assert git_ref is not None
    assert "palimpsest/job/test-1:" in git_ref
    assert repo.head.commit.message.startswith("agent.job.completed: test")
    # Artifact bindings should be created
    assert len(artifact_bindings) > 0
    assert any(b.relation == "output" for b in artifact_bindings)


def test_publication_skips_failed_job(tmp_path):
    """Failed jobs skip publication."""
    config = PublicationConfig()
    result = {"status": "failed"}
    git_ref, artifact_bindings = publish_results("test-2", "task-2", "Goal", result, str(tmp_path), config)
    assert git_ref is None
    assert artifact_bindings == []


def test_publication_skips_repoless_workspace(tmp_path):
    config = PublicationConfig()
    result = {"status": "success", "summary": "meta job"}
    git_ref, artifact_bindings = publish_results("test-meta", "task-meta", "Meta", result, str(tmp_path), config)
    assert git_ref is None
    # Repoless workspace still creates artifact bindings
    assert len(artifact_bindings) > 0


def test_publication_skips_when_strategy_is_skip(tmp_path):
    repo = git.Repo.init(tmp_path)
    (tmp_path / "init.txt").write_text("init")
    repo.index.add(["init.txt"])
    repo.index.commit("init")
    repo.git.checkout("-b", "main")

    config = PublicationConfig(strategy="skip")
    result = {"status": "success", "summary": "eval"}
    git_ref, artifact_bindings = publish_results("test-skip", "task-skip", "Eval", result, str(tmp_path), config)
    assert git_ref is None
    assert artifact_bindings == []


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
            publish_results("test-3", "task-3", "Goal", result, str(tmp_path), config)


def test_publication_without_remote_fails(tmp_path):
    repo = git.Repo.init(tmp_path)
    (tmp_path / "init.txt").write_text("init")
    repo.index.add(["init.txt"])
    repo.index.commit("init")
    repo.git.checkout("-b", "palimpsest/job/task-4/test")
    (tmp_path / "new.txt").write_text("content")

    config = PublicationConfig()
    result = {"status": "success", "summary": "test"}

    with pytest.raises(RuntimeError, match="configured remote"):
        publish_results("test-4", "task-4", "Goal", result, str(tmp_path), config)


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
    orig_execute = git.cmd.Git.execute

    def execute_side_effect(self, command, *args, **kwargs):
        if command[:2] == ["git", "push"]:
            return ""
        return orig_execute(self, command, *args, **kwargs)

    with (
        patch("palimpsest.stages.publication.git.Repo", return_value=repo),
        patch.object(git.cmd.Git, "execute", autospec=True, side_effect=execute_side_effect) as execute_mock,
    ):
        publish_results(
            "test-4",
            "task-4",
            "Goal",
            result,
            str(tmp_path),
            config,
            git_token_env="GITHUB_TOKEN",
        )

    execute_args = execute_mock.call_args.args[1]
    execute_env = execute_mock.call_args.kwargs["env"]
    assert execute_args[:3] == ["git", "push", "--porcelain"]
    assert execute_env["GIT_CONFIG_COUNT"] == "2"
    assert execute_env["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    assert execute_env["GIT_CONFIG_VALUE_0"] == ""
    assert execute_env["GIT_CONFIG_KEY_1"] == "http.extraHeader"
    assert execute_env["GIT_CONFIG_VALUE_1"].startswith("AUTHORIZATION: basic ")

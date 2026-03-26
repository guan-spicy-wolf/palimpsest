import json
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

import git

from palimpsest.stages.publication import publish_results, _write_completion_artifact
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
    
    # Check completion artifact was written
    artifact_path = tmp_path / ".palimpsest" / "completion.json"
    assert artifact_path.exists()
    artifact = json.loads(artifact_path.read_text())
    assert artifact["job_id"] == "test-1"
    assert artifact["status"] == "success"
    assert artifact["publication"]["mode"] == "branch_only"
    assert artifact["publication"]["trust_level"] == "automated"


def test_publication_skips_failed_job(tmp_path):
    """Failed jobs skip publication."""
    config = PublicationConfig()
    result = {"status": "failed"}
    git_ref = publish_results("test-2", result, str(tmp_path), config)
    assert git_ref is None
    
    # Check completion artifact was still written
    artifact_path = tmp_path / ".palimpsest" / "completion.json"
    assert artifact_path.exists()
    artifact = json.loads(artifact_path.read_text())
    assert artifact["status"] == "failed"


def test_publication_skips_repoless_workspace(tmp_path):
    config = PublicationConfig()
    result = {"status": "success", "summary": "meta job"}
    git_ref = publish_results("test-meta", result, str(tmp_path), config)
    assert git_ref is None
    
    # Check completion artifact was still written
    artifact_path = tmp_path / ".palimpsest" / "completion.json"
    assert artifact_path.exists()


def test_publication_skips_when_strategy_is_skip(tmp_path):
    repo = git.Repo.init(tmp_path)
    (tmp_path / "init.txt").write_text("init")
    repo.index.add(["init.txt"])
    repo.index.commit("init")
    repo.git.checkout("-b", "main")

    config = PublicationConfig(strategy="skip")
    result = {"status": "success", "summary": "eval"}
    git_ref = publish_results("test-skip", result, str(tmp_path), config)
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
    """Test that git token env is used for authenticated HTTPS pushes.
    
    This test verifies the _push_auth_environment function generates
    the correct git config environment variables.
    """
    from palimpsest.stages.publication import _push_auth_environment
    
    # Set up the token
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    
    # Get the auth environment
    auth_env = _push_auth_environment("GITHUB_TOKEN")
    
    # Verify the auth environment is correctly structured
    assert auth_env["GIT_CONFIG_COUNT"] == "2"
    assert auth_env["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    assert auth_env["GIT_CONFIG_VALUE_0"] == ""
    assert auth_env["GIT_CONFIG_KEY_1"] == "http.extraHeader"
    assert auth_env["GIT_CONFIG_VALUE_1"].startswith("AUTHORIZATION: basic ")
    
    # Verify the token is encoded in the auth header
    import base64
    auth_header = auth_env["GIT_CONFIG_VALUE_1"]
    # Extract the base64 part after "basic "
    b64_part = auth_header.split("basic ")[1]
    decoded = base64.b64decode(b64_part).decode("utf-8")
    assert decoded == "x-access-token:test-token"
    
    # Test that when GIT_CONFIG_COUNT is already set, we return empty dict
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    auth_env = _push_auth_environment("GITHUB_TOKEN")
    assert auth_env == {}
    
    # Test that when no token is available, we return empty dict
    monkeypatch.delenv("GIT_CONFIG_COUNT")
    monkeypatch.delenv("GITHUB_TOKEN")
    auth_env = _push_auth_environment("GITHUB_TOKEN")
    assert auth_env == {}
    auth_env = _push_auth_environment("")
    assert auth_env == {}


def test_publication_mode_pr_draft(tmp_path):
    """Test pr_draft publication mode."""
    repo = git.Repo.init(tmp_path)
    (tmp_path / "init.txt").write_text("init")
    repo.index.add(["init.txt"])
    repo.index.commit("init")
    repo.git.checkout("-b", "palimpsest/job/test-draft")

    (tmp_path / "new.txt").write_text("content")

    config = PublicationConfig(mode="pr_draft")
    result = {"status": "success", "summary": "test draft"}
    git_ref = publish_results("test-draft", result, str(tmp_path), config)

    assert git_ref is not None
    
    # Check completion artifact
    artifact_path = tmp_path / ".palimpsest" / "completion.json"
    artifact = json.loads(artifact_path.read_text())
    assert artifact["publication"]["mode"] == "pr_draft"
    assert artifact["publication"]["trust_level"] == "review_suggested"
    assert artifact["publication"]["requires_review"] is True
    
    # Check commit message contains DRAFT marker
    commit = repo.head.commit
    assert "[DRAFT-PR]" in commit.message


def test_publication_mode_approval_required(tmp_path):
    """Test approval_required publication mode."""
    repo = git.Repo.init(tmp_path)
    (tmp_path / "init.txt").write_text("init")
    repo.index.add(["init.txt"])
    repo.index.commit("init")
    repo.git.checkout("-b", "palimpsest/job/test-approval")

    (tmp_path / "new.txt").write_text("content")

    config = PublicationConfig(mode="approval_required")
    result = {"status": "success", "summary": "test approval"}
    git_ref = publish_results("test-approval", result, str(tmp_path), config)

    assert git_ref is not None
    
    # Check completion artifact
    artifact_path = tmp_path / ".palimpsest" / "completion.json"
    artifact = json.loads(artifact_path.read_text())
    assert artifact["publication"]["mode"] == "approval_required"
    assert artifact["publication"]["trust_level"] == "human_required"
    assert artifact["publication"]["requires_review"] is True
    
    # Check commit message contains approval marker and uses wip prefix
    commit = repo.head.commit
    assert "[REQUIRES-HUMAN-APPROVAL]" in commit.message
    assert commit.message.startswith("wip:")


def test_completion_artifact_includes_files_changed(tmp_path):
    """Test that completion artifact includes list of changed files."""
    repo = git.Repo.init(tmp_path)
    (tmp_path / "init.txt").write_text("init")
    repo.index.add(["init.txt"])
    repo.index.commit("init")
    repo.git.checkout("-b", "palimpsest/job/test-files")

    # Add multiple files
    (tmp_path / "new1.txt").write_text("content1")
    (tmp_path / "new2.txt").write_text("content2")

    config = PublicationConfig()
    result = {"status": "success", "summary": "test files"}
    git_ref = publish_results("test-files", result, str(tmp_path), config)

    # Check completion artifact includes files changed
    artifact_path = tmp_path / ".palimpsest" / "completion.json"
    artifact = json.loads(artifact_path.read_text())
    files_changed = artifact["artifacts"]["files_changed"]
    
    # Should have 2 new files
    assert len(files_changed) == 2
    paths = [f["path"] for f in files_changed]
    assert "new1.txt" in paths
    assert "new2.txt" in paths

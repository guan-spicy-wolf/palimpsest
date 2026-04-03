"""Tests for unified GitHub client."""
import pytest
from unittest.mock import patch, MagicMock
import os

from palimpsest.runtime.github_client import (
    GitHubClient,
    GitHubPR,
    GitHubIssue,
    GitHubError,
    get_github_client,
)


class TestGitHubClient:
    """Tests for GitHub client."""

    def test_parse_repo_slug_https_url(self):
        """Parse HTTPS GitHub URL."""
        owner, repo = GitHubClient.parse_repo_slug("https://github.com/owner/repo")
        assert owner == "owner"
        assert repo == "repo"

    def test_parse_repo_slug_ssh_url(self):
        """Parse SSH GitHub URL."""
        owner, repo = GitHubClient.parse_repo_slug("git@github.com:owner/repo.git")
        assert owner == "owner"
        assert repo == "repo"

    def test_parse_repo_slug_plain(self):
        """Parse plain owner/repo slug."""
        owner, repo = GitHubClient.parse_repo_slug("owner/repo")
        assert owner == "owner"
        assert repo == "repo"

    def test_parse_repo_slug_invalid(self):
        """Invalid repo slug raises error."""
        with pytest.raises(ValueError):
            GitHubClient.parse_repo_slug("invalid")

    def test_get_auth_from_env(self):
        """Get auth token from environment."""
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            client = GitHubClient()
            auth = client._get_auth()
            assert auth.token == "test-token"
            assert auth.token_source == "env:GITHUB_TOKEN"

    def test_get_auth_from_custom_env(self):
        """Get auth token from custom env var."""
        with patch.dict(os.environ, {"MY_TOKEN": "custom-token"}, clear=True):
            # Remove GITHUB_TOKEN if exists
            os.environ.pop("GITHUB_TOKEN", None)
            os.environ.pop("GH_TOKEN", None)
            client = GitHubClient(token_env="MY_TOKEN")
            auth = client._get_auth()
            assert auth.token == "custom-token"
            assert auth.token_source == "env:MY_TOKEN"

    def test_get_auth_missing_token(self):
        """Missing token raises error."""
        with patch.dict(os.environ, {}, clear=True):
            # Clear all token env vars
            for key in ["GITHUB_TOKEN", "GH_TOKEN"]:
                os.environ.pop(key, None)
            client = GitHubClient()
            with pytest.raises(ValueError, match="No GitHub token"):
                client._get_auth()

    def test_create_pr_success(self):
        """Create PR successfully."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "number": 42,
            "url": "https://api.github.com/repos/owner/repo/pulls/42",
            "html_url": "https://github.com/owner/repo/pull/42",
            "title": "Test PR",
            "body": "Test body",
            "head": {"ref": "feature"},
            "base": {"ref": "main"},
            "state": "open",
        }

        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            client = GitHubClient()
            with patch("httpx.post", return_value=mock_response):
                pr = client.create_pr(
                    owner="owner",
                    repo="repo",
                    head_branch="feature",
                    base_branch="main",
                    title="Test PR",
                    body="Test body",
                )

        assert pr.number == 42
        assert pr.title == "Test PR"
        assert pr.head_branch == "feature"
        assert pr.base_branch == "main"
        assert pr.owner == "owner"
        assert pr.repo == "repo"

    def test_create_pr_failure(self):
        """Create PR failure raises error."""
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.status_code = 422
        mock_response.json.return_value = {"message": "Validation failed"}

        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            client = GitHubClient()
            with patch("httpx.post", return_value=mock_response):
                with pytest.raises(GitHubError, match="Validation failed"):
                    client.create_pr(
                        owner="owner",
                        repo="repo",
                        head_branch="feature",
                        base_branch="main",
                        title="Test PR",
                    )

    def test_get_pr_success(self):
        """Get PR successfully."""
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "number": 42,
            "url": "https://api.github.com/repos/owner/repo/pulls/42",
            "html_url": "https://github.com/owner/repo/pull/42",
            "title": "Test PR",
            "body": "",
            "head": {"ref": "feature"},
            "base": {"ref": "main"},
            "state": "open",
        }

        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            client = GitHubClient()
            with patch("httpx.get", return_value=mock_response):
                pr = client.get_pr(owner="owner", repo="repo", number=42)

        assert pr is not None
        assert pr.number == 42

    def test_get_pr_not_found(self):
        """Get PR returns None if not found."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            client = GitHubClient()
            with patch("httpx.get", return_value=mock_response):
                pr = client.get_pr(owner="owner", repo="repo", number=999)

        assert pr is None


class TestGitHubClientSingleton:
    """Tests for singleton client."""

    def test_get_github_client_singleton(self):
        """get_github_client returns singleton."""
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"}):
            client1 = get_github_client()
            client2 = get_github_client()
            assert client1 is client2
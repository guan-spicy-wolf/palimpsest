"""Unified GitHub API client for Palimpsest.

This module provides a single entry point for all GitHub API operations,
ensuring consistent authentication, error handling, and rate limiting.

Per Phase 3: GitHub Client and External Trigger Ingestion.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger


@dataclass
class GitHubAuth:
    """GitHub authentication configuration."""
    token: str
    token_source: str  # e.g., "env:GITHUB_TOKEN"


@dataclass
class GitHubPR:
    """GitHub Pull Request data."""
    number: int
    url: str
    html_url: str
    title: str
    body: str
    head_branch: str
    base_branch: str
    state: str
    owner: str
    repo: str


@dataclass
class GitHubIssue:
    """GitHub Issue data."""
    number: int
    url: str
    html_url: str
    title: str
    body: str
    state: str
    owner: str
    repo: str


@dataclass
class GitHubComment:
    """GitHub Issue/PR comment data."""
    id: int
    url: str
    body: str
    user: str


class GitHubClient:
    """Unified GitHub API client.

    Provides a single entry point for all GitHub operations:
    - PR creation/query
    - Issue comments
    - File contents

    Authentication is handled via environment variables:
    - GITHUB_TOKEN (preferred)
    - GH_TOKEN
    - Or custom env var via constructor
    """

    API_VERSION = "2022-11-28"
    API_BASE = "https://api.github.com"

    def __init__(
        self,
        token_env: str = "GITHUB_TOKEN",
        api_base: str | None = None,
    ):
        self._token_env = token_env
        self._api_base = api_base or self.API_BASE
        self._auth: GitHubAuth | None = None

    def _get_auth(self) -> GitHubAuth:
        """Get GitHub authentication token."""
        if self._auth is not None:
            return self._auth

        # Try configured env var first
        token = os.environ.get(self._token_env, "")
        if token:
            self._auth = GitHubAuth(token=token, token_source=f"env:{self._token_env}")
            return self._auth

        # Fallback to common env vars
        for env_var in ["GITHUB_TOKEN", "GH_TOKEN"]:
            token = os.environ.get(env_var, "")
            if token:
                self._auth = GitHubAuth(token=token, token_source=f"env:{env_var}")
                return self._auth

        raise ValueError(
            f"No GitHub token found. Set {self._token_env} or GITHUB_TOKEN environment variable."
        )

    def _headers(self) -> dict[str, str]:
        """Build request headers with authentication."""
        auth = self._get_auth()
        return {
            "Authorization": f"Bearer {auth.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.API_VERSION,
        }

    @staticmethod
    def parse_repo_slug(repo: str) -> tuple[str, str]:
        """Parse a GitHub repository URL or slug into (owner, repo_name).

        Supports:
        - https://github.com/owner/repo
        - git@github.com:owner/repo.git
        - owner/repo
        """
        text = repo.strip()

        # SSH URL: git@github.com:owner/repo.git
        if text.startswith("git@github.com:"):
            slug = text[len("git@github.com:"):].rstrip("/")
            if slug.endswith(".git"):
                slug = slug[:-4]
            parts = slug.split("/", 1)
            if len(parts) == 2:
                return parts[0], parts[1]

        # HTTPS URL: https://github.com/owner/repo
        if text.startswith("https://github.com/"):
            slug = text[len("https://github.com/"):].rstrip("/")
            if slug.endswith(".git"):
                slug = slug[:-4]
            parts = slug.split("/", 1)
            if len(parts) == 2:
                return parts[0], parts[1]

        # Plain slug: owner/repo
        parts = text.split("/", 1)
        if len(parts) == 2:
            return parts[0], parts[1]

        raise ValueError(f"Invalid GitHub repository: {repo}")

    def create_pr(
        self,
        owner: str,
        repo: str,
        head_branch: str,
        base_branch: str,
        title: str,
        body: str = "",
    ) -> GitHubPR:
        """Create a pull request.

        Args:
            owner: Repository owner
            repo: Repository name
            head_branch: Source branch
            base_branch: Target branch
            title: PR title
            body: PR description

        Returns:
            GitHubPR with created PR data

        Raises:
            GitHubError on failure
        """
        url = f"{self._api_base}/repos/{owner}/{repo}/pulls"

        try:
            response = httpx.post(
                url,
                headers=self._headers(),
                json={
                    "title": title,
                    "body": body,
                    "head": head_branch,
                    "base": base_branch,
                },
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub PR create failed: {exc}") from exc

        if not response.is_success:
            detail = self._parse_error(response)
            raise GitHubError(f"GitHub PR create failed ({response.status_code}): {detail}")

        payload = response.json()
        return GitHubPR(
            number=payload["number"],
            url=payload["url"],
            html_url=payload["html_url"],
            title=payload["title"],
            body=payload.get("body", ""),
            head_branch=payload["head"]["ref"],
            base_branch=payload["base"]["ref"],
            state=payload["state"],
            owner=owner,
            repo=repo,
        )

    def get_pr(
        self,
        owner: str,
        repo: str,
        number: int,
    ) -> GitHubPR | None:
        """Get a pull request by number.

        Returns None if PR not found.
        """
        url = f"{self._api_base}/repos/{owner}/{repo}/pulls/{number}"

        try:
            response = httpx.get(url, headers=self._headers(), timeout=30.0)
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub PR get failed: {exc}") from exc

        if response.status_code == 404:
            return None

        if not response.is_success:
            detail = self._parse_error(response)
            raise GitHubError(f"GitHub PR get failed ({response.status_code}): {detail}")

        payload = response.json()
        return GitHubPR(
            number=payload["number"],
            url=payload["url"],
            html_url=payload["html_url"],
            title=payload["title"],
            body=payload.get("body", ""),
            head_branch=payload["head"]["ref"],
            base_branch=payload["base"]["ref"],
            state=payload["state"],
            owner=owner,
            repo=repo,
        )

    def list_pr_comments(
        self,
        owner: str,
        repo: str,
        pr_number: int,
    ) -> list[GitHubComment]:
        """List comments on a pull request."""
        url = f"{self._api_base}/repos/{owner}/{repo}/issues/{pr_number}/comments"

        try:
            response = httpx.get(url, headers=self._headers(), timeout=30.0)
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub PR comments list failed: {exc}") from exc

        if not response.is_success:
            detail = self._parse_error(response)
            raise GitHubError(f"GitHub PR comments list failed ({response.status_code}): {detail}")

        comments = []
        for item in response.json():
            comments.append(GitHubComment(
                id=item["id"],
                url=item["url"],
                body=item.get("body", ""),
                user=item.get("user", {}).get("login", ""),
            ))
        return comments

    def create_pr_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
    ) -> GitHubComment:
        """Create a comment on a pull request."""
        url = f"{self._api_base}/repos/{owner}/{repo}/issues/{pr_number}/comments"

        try:
            response = httpx.post(
                url,
                headers=self._headers(),
                json={"body": body},
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub PR comment create failed: {exc}") from exc

        if not response.is_success:
            detail = self._parse_error(response)
            raise GitHubError(f"GitHub PR comment create failed ({response.status_code}): {detail}")

        payload = response.json()
        return GitHubComment(
            id=payload["id"],
            url=payload["url"],
            body=payload.get("body", ""),
            user=payload.get("user", {}).get("login", ""),
        )

    def get_issue(
        self,
        owner: str,
        repo: str,
        number: int,
    ) -> GitHubIssue | None:
        """Get an issue by number."""
        url = f"{self._api_base}/repos/{owner}/{repo}/issues/{number}"

        try:
            response = httpx.get(url, headers=self._headers(), timeout=30.0)
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub issue get failed: {exc}") from exc

        if response.status_code == 404:
            return None

        if not response.is_success:
            detail = self._parse_error(response)
            raise GitHubError(f"GitHub issue get failed ({response.status_code}): {detail}")

        payload = response.json()
        return GitHubIssue(
            number=payload["number"],
            url=payload["url"],
            html_url=payload["html_url"],
            title=payload["title"],
            body=payload.get("body", ""),
            state=payload["state"],
            owner=owner,
            repo=repo,
        )

    def get_file_content(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str = "main",
    ) -> str | None:
        """Get file content from a repository.

        Returns None if file not found.
        """
        url = f"{self._api_base}/repos/{owner}/{repo}/contents/{path}?ref={ref}"

        try:
            response = httpx.get(url, headers=self._headers(), timeout=30.0)
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub file get failed: {exc}") from exc

        if response.status_code == 404:
            return None

        if not response.is_success:
            detail = self._parse_error(response)
            raise GitHubError(f"GitHub file get failed ({response.status_code}): {detail}")

        import base64
        payload = response.json()
        if payload.get("type") != "file":
            raise GitHubError(f"Not a file: {path}")

        content = payload.get("content", "")
        encoding = payload.get("encoding", "base64")
        if encoding == "base64":
            return base64.b64decode(content).decode("utf-8")
        return content

    def _parse_error(self, response: httpx.Response) -> str:
        """Parse error message from GitHub response."""
        try:
            payload = response.json()
            return str(payload.get("message") or payload)
        except Exception:
            return response.text.strip() or "unknown error"


class GitHubError(Exception):
    """GitHub API error."""
    pass


# Singleton instance for convenience
_default_client: GitHubClient | None = None


def get_github_client(token_env: str = "GITHUB_TOKEN") -> GitHubClient:
    """Get the default GitHub client instance."""
    global _default_client
    if _default_client is None:
        _default_client = GitHubClient(token_env=token_env)
    return _default_client
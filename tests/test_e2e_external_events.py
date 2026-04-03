"""End-to-end smoke test for external event -> task flow.

This test verifies the complete flow:
1. External event received
2. Converted to TriggerData
3. GitHub context injected into params
4. Context provider can render the context
"""
import pytest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

from yoitsu_contracts.events import TriggerData
from yoitsu_contracts.external_events import (
    PRLabeledEvent,
    IssueLabeledEvent,
    pr_labeled_to_trigger,
    issue_labeled_to_trigger,
)


class TestE2EExternalEventFlow:
    """End-to-end tests for external event processing."""

    def test_pr_labeled_e2e_trigger_conversion(self):
        """PR labeled event converts to valid TriggerData with GitHub context."""
        # Step 1: Create external event
        event = PRLabeledEvent(
            repo="owner/repo",
            pr_number=42,
            label="ready-for-review",
            title="Add new feature",
            body="This PR adds a new feature",
            head_branch="feature/new",
            base_branch="main",
            author="developer",
            team="backend",
            budget=0.5,
        )

        # Step 2: Convert to trigger data
        trigger_dict = pr_labeled_to_trigger(event)
        assert trigger_dict is not None

        # Step 3: Validate TriggerData
        trigger = TriggerData.model_validate(trigger_dict)

        assert trigger.trigger_type == "pr_labeled"
        assert trigger.goal == "Add new feature"
        assert trigger.role == "reviewer"
        assert trigger.repo == "owner/repo"
        assert trigger.init_branch == "feature/new"
        assert trigger.team == "backend"
        assert trigger.budget == 0.5

        # Step 4: Validate GitHub context in params
        assert "github_context" in trigger.params
        github = trigger.params["github_context"]
        assert "pr" in github
        assert github["pr"]["number"] == 42
        assert github["pr"]["title"] == "Add new feature"
        assert "url" in github["pr"]

    def test_issue_labeled_e2e_trigger_conversion(self):
        """Issue labeled event converts to valid TriggerData with GitHub context."""
        # Step 1: Create external event
        event = IssueLabeledEvent(
            repo="owner/repo",
            issue_number=123,
            label="needs-review",
            title="Bug in feature X",
            body="Detailed description",
            author="reporter",
            team="default",
            budget=0.3,
        )

        # Step 2: Convert to trigger data
        trigger_dict = issue_labeled_to_trigger(event)
        assert trigger_dict is not None

        # Step 3: Validate TriggerData
        trigger = TriggerData.model_validate(trigger_dict)

        assert trigger.trigger_type == "issue_labeled"
        assert "Bug in feature X" in trigger.goal
        assert trigger.role == "reviewer"
        assert trigger.repo == "owner/repo"
        assert trigger.team == "default"

        # Step 4: Validate GitHub context
        assert "github_context" in trigger.params
        github = trigger.params["github_context"]
        assert "issue" in github
        assert github["issue"]["number"] == 123
        assert "needs-review" in github["issue"]["labels"]

    def test_github_context_provider_renders_pr_context(self):
        """GitHub context provider can render PR context."""
        # Create a mock job config with GitHub context
        from palimpsest.config import JobConfig

        job_config = JobConfig(
            job_id="test-job",
            goal="Review PR #42",
            role_params={
                "github_context": {
                    "pr": {
                        "number": 42,
                        "owner": "owner",
                        "repo": "repo",
                        "url": "https://github.com/owner/repo/pull/42",
                        "title": "Add new feature",
                        "body": "Description",
                        "head_branch": "feature/new",
                        "base_branch": "main",
                        "author": "developer",
                        "state": "open",
                        "files": ["src/main.py", "src/test.py"],
                    }
                }
            }
        )

        # Import and call the context provider
        # Note: This tests the provider function directly
        from evo.contexts.loaders import github_context

        result = github_context(job_config)

        assert "GitHub Context" in result
        assert "Pull Request" in result
        assert "#42" in result
        assert "Add new feature" in result
        assert "feature/new" in result
        assert "developer" in result
        assert "src/main.py" in result

    def test_github_context_provider_renders_issue_context(self):
        """GitHub context provider can render Issue context."""
        from palimpsest.config import JobConfig
        from evo.contexts.loaders import github_context

        job_config = JobConfig(
            job_id="test-job",
            goal="Review Issue #123",
            role_params={
                "github_context": {
                    "issue": {
                        "number": 123,
                        "owner": "owner",
                        "repo": "repo",
                        "url": "https://github.com/owner/repo/issues/123",
                        "title": "Bug report",
                        "body": "Detailed description",
                        "author": "reporter",
                        "state": "open",
                        "labels": ["bug", "needs-review"],
                    }
                }
            }
        )

        result = github_context(job_config)

        assert "GitHub Context" in result
        assert "Issue" in result
        assert "#123" in result
        assert "Bug report" in result
        assert "bug" in result
        assert "reporter" in result

    def test_external_event_unmapped_label_returns_none(self):
        """Unmapped label returns None, no trigger created."""
        event = IssueLabeledEvent(
            repo="owner/repo",
            issue_number=999,
            label="wontfix",  # Not in default mapping
            title="Not a bug",
        )

        trigger_dict = issue_labeled_to_trigger(event)
        assert trigger_dict is None

    def test_ci_failure_e2e_trigger_conversion(self):
        """CI failure event converts to valid TriggerData."""
        from yoitsu_contracts.external_events import (
            CIFailureEvent,
            ci_failure_to_trigger,
        )

        event = CIFailureEvent(
            repo="owner/repo",
            branch="main",
            commit_sha="abc123",
            workflow="CI",
            message="Tests failed",
            team="backend",
            budget=1.0,
        )

        trigger_dict = ci_failure_to_trigger(event)
        trigger = TriggerData.model_validate(trigger_dict)

        assert trigger.trigger_type == "ci_failure"
        assert trigger.role == "implementer"
        assert trigger.repo == "owner/repo"
        assert trigger.init_branch == "main"
        assert trigger.sha == "abc123"
        assert trigger.team == "backend"
        assert trigger.budget == 1.0


class TestE2EFullPipeline:
    """Full pipeline tests from external event to context rendering."""

    def test_pr_labeled_to_context_rendering(self):
        """Complete flow: PR labeled event -> context rendering."""
        # Step 1: External event
        event = PRLabeledEvent(
            repo="myorg/myrepo",
            pr_number=99,
            label="ready-for-review",
            title="Feature: Add OAuth support",
            body="This PR adds OAuth authentication",
            head_branch="feature/oauth",
            base_branch="main",
            author="contributor",
        )

        # Step 2: Convert to trigger
        trigger_dict = pr_labeled_to_trigger(event)
        assert trigger_dict is not None

        # Step 3: Validate trigger
        trigger = TriggerData.model_validate(trigger_dict)
        assert trigger.role == "reviewer"

        # Step 4: Create job config with params
        from palimpsest.config import JobConfig
        job_config = JobConfig(
            job_id="review-pr-99",
            goal=trigger.goal,
            role=trigger.role,
            role_params=trigger.params,
        )

        # Step 5: Render context
        from evo.contexts.loaders import github_context
        context = github_context(job_config)

        # Step 6: Verify context contains PR info
        assert "Feature: Add OAuth support" in context
        assert "feature/oauth" in context
        assert "contributor" in context
        assert "https://github.com/myorg/myrepo/pull/99" in context
"""Tests for runner team parameter passing to RoleManager.

Issue: run_job() was not passing config.team to RoleManager,
causing team-specific role override to never work in production code path.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from palimpsest.runner import run_job


@pytest.fixture
def minimal_evo_with_role(tmp_path: Path) -> Path:
    """Create a minimal evo structure with a single role for testing."""
    evo_root = tmp_path / "evo"
    evo_root.mkdir()

    # Global roles
    roles_dir = evo_root / "roles"
    roles_dir.mkdir()

    (roles_dir / "test_role.py").write_text('''
from palimpsest.runtime.roles import role, JobSpec, context_spec, workspace_config, git_publication

@role(name="test_role", description="Test role")
def test_role(**params):
    return JobSpec(
        preparation_fn=workspace_config(repo="", init_branch="main"),
        context_fn=context_spec("test system", []),
        publication_fn=git_publication(strategy="skip"),
    )
''')

    return evo_root


class TestRunnerTeamPassing:
    """Tests verifying run_job passes config.team to RoleManager."""

    @patch("palimpsest.runner.RoleManager")
    @patch("palimpsest.runner._run_job_from_spec")
    def test_role_manager_receives_team_from_config(
        self, mock_run_from_spec, mock_role_manager_class, tmp_path: Path
    ):
        """RoleManager should be instantiated with config.team parameter."""
        # Setup minimal mock evo root
        evo_path = tmp_path / "evo"
        evo_path.mkdir()
        roles_dir = evo_path / "roles"
        roles_dir.mkdir()

        # Create a mock RoleManager instance
        mock_manager_instance = MagicMock()
        mock_role_manager_class.return_value = mock_manager_instance

        # Mock resolve to return a minimal JobSpec-like object
        from palimpsest.runtime.roles import JobSpec
        mock_spec = MagicMock(spec=JobSpec)
        mock_spec.source_role = "test_role"
        mock_spec.tools = []
        mock_manager_instance.resolve.return_value = mock_spec

        # Create config with a specific team
        from palimpsest.config import JobConfig, LLMConfig, WorkspaceConfig

        config = JobConfig(
            job_id="test-job",
            role="test_role",
            task="test task",
            team="factorio",  # This should be passed to RoleManager
            llm=LLMConfig(model="test-model"),
            workspace=WorkspaceConfig(repo="https://github.com/test/repo.git"),
        )

        # Run with mocked evo path resolution
        with patch("palimpsest.runner._materialize_evo_root") as mock_materialize:
            mock_materialize.return_value.__enter__ = MagicMock(return_value=(evo_path, "abc123"))
            mock_materialize.return_value.__exit__ = MagicMock(return_value=False)

            # Call run_job (will stop at _run_job_from_spec mock)
            try:
                run_job(config)
            except Exception:
                pass  # Ignore errors from mocked _run_job_from_spec

        # Verify RoleManager was called with team parameter
        mock_role_manager_class.assert_called_once()
        call_kwargs = mock_role_manager_class.call_args[1]
        assert "team" in call_kwargs, "RoleManager should receive 'team' parameter"
        assert call_kwargs["team"] == "factorio", "RoleManager should receive config.team value"

    @patch("palimpsest.runner.RoleManager")
    @patch("palimpsest.runner._run_job_from_spec")
    def test_role_manager_defaults_to_default_team_when_not_specified(
        self, mock_run_from_spec, mock_role_manager_class, tmp_path: Path
    ):
        """RoleManager should receive 'default' when team is not in config."""
        evo_path = tmp_path / "evo"
        evo_path.mkdir()

        mock_manager_instance = MagicMock()
        mock_role_manager_class.return_value = mock_manager_instance

        from palimpsest.runtime.roles import JobSpec
        mock_spec = MagicMock(spec=JobSpec)
        mock_spec.source_role = "test_role"
        mock_spec.tools = []
        mock_manager_instance.resolve.return_value = mock_spec

        from palimpsest.config import JobConfig, LLMConfig, WorkspaceConfig

        # Config with default team
        config = JobConfig(
            job_id="test-job",
            role="test_role",
            task="test task",
            # team not specified, should default
            llm=LLMConfig(model="test-model"),
            workspace=WorkspaceConfig(repo="https://github.com/test/repo.git"),
        )

        with patch("palimpsest.runner._materialize_evo_root") as mock_materialize:
            mock_materialize.return_value.__enter__ = MagicMock(return_value=(evo_path, "abc123"))
            mock_materialize.return_value.__exit__ = MagicMock(return_value=False)

            try:
                run_job(config)
            except Exception:
                pass

        # Verify RoleManager was called with team parameter (should be "default")
        mock_role_manager_class.assert_called_once()
        call_kwargs = mock_role_manager_class.call_args[1]
        assert "team" in call_kwargs
        assert call_kwargs["team"] == "default"
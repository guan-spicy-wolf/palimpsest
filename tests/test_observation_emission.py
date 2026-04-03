"""Tests for observation signal emission from palimpsest (ADR-0010)."""
import pytest
from unittest.mock import MagicMock

from yoitsu_contracts.observation import (
    OBSERVATION_PREPARATION_FAILURE,
)


class TestPreparationFailureEmission:
    """Tests for preparation_failure observation emission."""

    def test_emit_preparation_failure_on_clone_error(self):
        """preparation_failure is emitted when git clone fails."""
        from palimpsest.stages.preparation import run_preparation
        from palimpsest.config import WorkspaceConfig

        gateway = MagicMock()

        config = WorkspaceConfig(
            repo="https://github.com/nonexistent/invalid-repo-that-does-not-exist.git",
            init_branch="main",
        )

        with pytest.raises(Exception):  # Clone will fail
            run_preparation(
                job_id="test-job-003",
                config=config,
                task_id="task-003",
                goal="Test task",
                gateway=gateway,
            )

        # Verify emission
        emit_calls = gateway.emit.call_args_list
        event_types = [call[0][0] for call in emit_calls]
        assert OBSERVATION_PREPARATION_FAILURE in event_types

        # Find the preparation_failure call
        for call in emit_calls:
            if call[0][0] == OBSERVATION_PREPARATION_FAILURE:
                data = call[0][1]
                assert data["task_id"] == "task-003"
                assert data["job_id"] == "test-job-003"
                assert "error_type" in data
                assert "error_message" in data
                break

    def test_no_preparation_failure_on_success(self):
        """No preparation_failure emitted when preparation succeeds."""
        from palimpsest.stages.preparation import run_preparation
        from palimpsest.config import WorkspaceConfig
        import tempfile
        import os

        gateway = MagicMock()

        # Use repoless config (no git clone, so no failure)
        config = WorkspaceConfig(
            repo="",  # No repo
            init_branch="main",
        )

        workspace = run_preparation(
            job_id="test-job-004",
            config=config,
            task_id="task-004",
            goal="Test task",
            gateway=gateway,
        )

        # Verify no preparation_failure emission
        emit_calls = gateway.emit.call_args_list
        event_types = [call[0][0] for call in emit_calls]
        assert OBSERVATION_PREPARATION_FAILURE not in event_types

        # Cleanup
        import shutil
        shutil.rmtree(workspace, ignore_errors=True)
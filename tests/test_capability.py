"""Tests for capability model integration (ADR-0016).

Tests capability setup/finalize lifecycle in runner.
"""
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from palimpsest.config import JobConfig
from palimpsest.events import JobCompletedData, JobFailedData, RuntimeIssueData
from palimpsest.runtime.roles import JobSpec
from palimpsest.runner import _run_job_from_spec
from palimpsest.runtime.capability import JobContext, FinalizeResult
from yoitsu_contracts import AnalyzerVersion


class RecordingEmitter:
    def __init__(self):
        self.events = []

    def emit(self, event_data):
        self.events.append(event_data)
    
    def close(self):
        pass


def _spec(publication_fn=None) -> JobSpec:
    return JobSpec(
        workspace_fn=lambda **params: MagicMock(repo="", init_branch="main", new_branch=True, depth=1, git_token_env=""),
        context_fn=lambda **params: {"system": "sys", "sections": [], "task": params.get("goal") or params.get("task", "")},
        publication_fn=publication_fn or MagicMock(return_value=("branch:sha", [])),
        tools=[],
    )


def _base_patches(emitter, tmp_path):
    """Return a dict of common patches for runner tests."""
    return {
        "palimpsest.runner.EventEmitter": MagicMock(return_value=emitter),
        "palimpsest.runner._read_bundle_sha": MagicMock(return_value="abc123"),
        "palimpsest.runner.setup_workspace": MagicMock(return_value=str(tmp_path)),
        "palimpsest.runner.build_context": MagicMock(return_value={"system": "sys", "task": "task"}),
        "palimpsest.runner.UnifiedLLMGateway": MagicMock(),
        "palimpsest.runner.UnifiedToolGateway": MagicMock(),
        "palimpsest.runner.git.Repo": MagicMock(),
        "palimpsest.runner.run_interaction_loop": MagicMock(return_value={"status": "complete", "summary": "ok"}),
        "palimpsest.runner.finalize_workspace_after_job": MagicMock(),
    }


def _apply_patches(patches):
    """Create a nested context manager from a dict of patches."""
    stack = ExitStack()
    mocks = {}
    for target, mock_val in patches.items():
        m = stack.enter_context(patch(target, mock_val))
        mocks[target] = m
    return stack, mocks


def test_capability_setup_called_for_needs(tmp_path):
    """When role has needs=['git_workspace'], cap.setup must be called."""
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-1", task="x")
    
    # Mock capability
    mock_cap = MagicMock()
    mock_cap.name = "git_workspace"
    mock_cap.setup.return_value = []  # No events
    mock_cap.finalize.return_value = FinalizeResult(events=[], success=True)
    
    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.get_capability"] = MagicMock(return_value=mock_cap)
    
    with _apply_patches(patches)[0]:
        _run_job_from_spec(
            config, _spec(), tmp_path,
            bundle_workspace="",
            target_workspace="/tmp/target",
            needs=["git_workspace"],
        )
    
    # Verify setup was called with JobContext
    mock_cap.setup.assert_called_once()
    setup_call_args = mock_cap.setup.call_args
    assert len(setup_call_args.args) == 1
    ctx = setup_call_args.args[0]
    assert isinstance(ctx, JobContext)
    assert ctx.job_id == "job-1"
    assert ctx.target_workspace == "/tmp/target"


def test_capability_setup_failure_emits_runtime_issue(tmp_path):
    """When cap.setup raises, emit RuntimeIssueData with fatal=True and fail job."""
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-fail", task="x")
    
    mock_cap = MagicMock()
    mock_cap.name = "git_workspace"
    mock_cap.setup.side_effect = RuntimeError("setup failed")
    
    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.get_capability"] = MagicMock(return_value=mock_cap)
    
    with _apply_patches(patches)[0]:
        with pytest.raises(Exception):  # ControlledJobFailure
            _run_job_from_spec(
                config, _spec(), tmp_path,
                bundle_workspace="",
                target_workspace="",
                needs=["git_workspace"],
            )
    
    # Verify RuntimeIssueData emitted
    issues = [e for e in emitter.events if isinstance(e, RuntimeIssueData)]
    assert len(issues) == 1
    assert issues[0].stage == "setup"
    assert issues[0].fatal is True
    assert "git_workspace_setup_failed" in issues[0].code


def test_capability_finalize_success_emits_events(tmp_path):
    """When cap.finalize returns success=True, emit events and JobCompletedData."""
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-ok", task="x")
    
    mock_cap = MagicMock()
    mock_cap.name = "git_workspace"
    mock_cap.setup.return_value = []
    mock_cap.finalize.return_value = FinalizeResult(
        events=[],  # Empty events list for simplicity
        success=True,
    )
    
    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.get_capability"] = MagicMock(return_value=mock_cap)
    
    with _apply_patches(patches)[0]:
        _run_job_from_spec(
            config, _spec(), tmp_path,
            bundle_workspace="",
            target_workspace="",
            needs=["git_workspace"],
        )
    
    # Verify finalize called
    mock_cap.finalize.assert_called_once()
    
    # Verify JobCompletedData emitted
    completed = [e for e in emitter.events if isinstance(e, JobCompletedData)]
    assert len(completed) == 1


def test_capability_finalize_failure_emits_job_failed(tmp_path):
    """When cap.finalize returns success=False, emit JobFailedData."""
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-hallucination", task="x")
    
    mock_cap = MagicMock()
    mock_cap.name = "git_workspace"
    mock_cap.setup.return_value = []
    mock_cap.finalize.return_value = FinalizeResult(
        events=[],  # Empty events for simplicity
        success=False,  # Hallucination gate
    )
    
    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.get_capability"] = MagicMock(return_value=mock_cap)
    
    with _apply_patches(patches)[0]:
        _run_job_from_spec(
            config, _spec(), tmp_path,
            bundle_workspace="",
            target_workspace="",
            needs=["git_workspace"],
        )
    
    # Verify JobFailedData emitted
    failed = [e for e in emitter.events if isinstance(e, JobFailedData)]
    assert len(failed) == 1
    assert "finalize" in failed[0].code or "success" in failed[0].error.lower()


def test_multiple_capabilities_all_called(tmp_path):
    """When needs=['git', 'slack'], both setup/finalize called in order."""
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-multi", task="x")
    
    mock_git = MagicMock()
    mock_git.name = "git_workspace"
    mock_git.setup.return_value = []
    mock_git.finalize.return_value = FinalizeResult(events=[], success=True)
    
    mock_slack = MagicMock()
    mock_slack.name = "slack_notify"
    mock_slack.setup.return_value = []
    mock_slack.finalize.return_value = FinalizeResult(events=[], success=True)
    
    patches = _base_patches(emitter, tmp_path)
    cap_map = {"git_workspace": mock_git, "slack_notify": mock_slack}
    patches["palimpsest.runner.get_capability"] = MagicMock(side_effect=lambda n: cap_map.get(n))
    
    with _apply_patches(patches)[0]:
        _run_job_from_spec(
            config, _spec(), tmp_path,
            bundle_workspace="",
            target_workspace="",
            needs=["git_workspace", "slack_notify"],
        )
    
    # Both setup called
    mock_git.setup.assert_called_once()
    mock_slack.setup.assert_called_once()
    
    # Both finalize called
    mock_git.finalize.assert_called_once()
    mock_slack.finalize.assert_called_once()


def test_job_context_analyzer_version():
    """JobContext receives analyzer_version from config."""
    av = AnalyzerVersion(
        bundle_sha="abc123",
        trenni_sha="def456",
        palimpsest_sha="ghi789",
    )
    
    ctx = JobContext(
        job_id="job-1",
        task_id="task-1",
        bundle="factorio",
        role="worker",
        goal="mine iron",
        bundle_workspace="/tmp/bundle",
        target_workspace="/tmp/target",
        analyzer_version=av,
    )
    
    assert ctx.analyzer_version is not None
    assert ctx.analyzer_version.bundle_sha == "abc123"
    assert ctx.analyzer_version.trenni_sha == "def456"
    assert ctx.analyzer_version.palimpsest_sha == "ghi789"


def test_backward_compat_no_needs_uses_publication_fn(tmp_path):
    """When needs=[], use old _stage_interaction_and_publication path."""
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-old", task="x")
    config.workspace.repo = "https://example.com/repo.git"
    
    publication_mock = MagicMock(return_value=("branch:sha", []))
    publication_mock.__publication_strategy__ = "branch"
    publication_mock.__publication_branch_prefix__ = "palimpsest/job"
    
    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner._stage_interaction_and_publication"] = MagicMock(
        return_value=({"summary": "ok"}, "branch:sha")
    )
    
    with _apply_patches(patches)[0]:
        _run_job_from_spec(
            config, _spec(publication_fn=publication_mock), tmp_path,
            bundle_workspace="",
            target_workspace="",
            needs=[],  # No capabilities
        )
    
    # Verify old path used (JobCompletedData emitted)
    completed = [e for e in emitter.events if isinstance(e, JobCompletedData)]
    assert len(completed) == 1
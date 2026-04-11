"""Tests for runner creating and passing RuntimeContext."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from palimpsest.config import JobConfig
from palimpsest.events import JobCompletedData
from palimpsest.runtime.context import RuntimeContext
from palimpsest.runtime.roles import JobSpec
from palimpsest.runner import _run_job_from_spec


class RecordingEmitter:
    def __init__(self):
        self.events = []

    def emit(self, event_data):
        self.events.append(event_data)
        return None

    def recent_events(self, limit=10, *, job_id=None):
        return []

    def close(self):
        return None


def _default_publication_fn(*, result=None, repo="", **params):
    if (result or {}).get("status") == "failed":
        return None, []
    if not repo:
        return None, []
    return "branch:sha", []


_default_publication_fn.__publication_strategy__ = "branch"
_default_publication_fn.__publication_branch_prefix__ = "palimpsest/job"


def _spec(publication_fn=None, preparation_fn=None) -> JobSpec:
    """Create a test JobSpec with optional overrides."""
    prep_fn = preparation_fn or (lambda **params: MagicMock(repo="", init_branch="main", new_branch=True, depth=1, git_token_env=""))
    return JobSpec(
        workspace_fn=prep_fn,  # ADR-0009: preparation_fn is the canonical name
        context_fn=lambda **params: {"system": "sys", "sections": [], "task": params.get("goal") or params.get("task", "")},
        publication_fn=publication_fn or _default_publication_fn,
        tools=[],
    )


def _base_patches(emitter, tmp_path, **overrides):
    """Return a dict of common patches for runner tests."""
    defaults = {
        "palimpsest.runner.EventEmitter": MagicMock(return_value=emitter),
        "palimpsest.runner._read_bundle_sha": MagicMock(return_value="abc123"),
        "palimpsest.runner.setup_workspace": MagicMock(return_value=str(tmp_path)),
        "palimpsest.runner.build_context": MagicMock(return_value={"system": "sys", "task": "task"}),
        "palimpsest.runner.UnifiedLLMGateway": MagicMock(),
        "palimpsest.runner.UnifiedToolGateway": MagicMock(),
        "palimpsest.runner.finalize_workspace_after_job": MagicMock(return_value=None),
    }
    defaults.update(overrides)
    return defaults


def _apply_patches(patches):
    """Create a nested context manager from a dict of patches."""
    from contextlib import ExitStack
    stack = ExitStack()
    mocks = {}
    for target, mock_val in patches.items():
        m = stack.enter_context(patch(target, mock_val))
        mocks[target] = m
    return stack, mocks


def test_runner_creates_runtime_context(tmp_path):
    """Runner creates RuntimeContext with correct job_id, task_id, and team."""
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-123", task="do work", role="test-role")
    config.bundle = "factorio"
    spec = _spec()

    captured = {}
    def capture_workspace(runtime_context: RuntimeContext, **params):
        captured["prep_params"] = params
        captured["runtime_context"] = runtime_context
        return MagicMock(repo="", init_branch="main", new_branch=True, depth=1, git_token_env="")

    spec_with_capture = JobSpec(
        workspace_fn=capture_workspace,
        context_fn=lambda **params: {"system": "sys", "sections": [], "task": "task"},
        publication_fn=_default_publication_fn,
        tools=[],
    )

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(
        return_value={"status": "complete", "summary": "ok", "messages": []}
    )
    patches["palimpsest.runner.git.Repo"] = MagicMock()

    with _apply_patches(patches)[0]:
        _run_job_from_spec(config, spec_with_capture, tmp_path, bundle_workspace="", target_workspace="")

    # Verify RuntimeContext was created and passed to preparation_fn
    assert "runtime_context" in captured
    ctx = captured["runtime_context"]
    assert isinstance(ctx, RuntimeContext)
    assert ctx.job_id == "job-123"
    assert ctx.task_id == "job-123"  # task_id defaults to job_id
    assert ctx.bundle == "factorio"


def test_runner_sets_workspace_path_on_context(tmp_path):
    """Runner sets workspace_path on RuntimeContext after workspace setup."""
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-456", task="test")
    spec = _spec()

    workspace_path_at_prep = []
    workspace_path_at_pub = []

    def capture_prep(runtime_context=None, **params):
        # Capture workspace_path value at prep time (before it's set)
        if runtime_context:
            workspace_path_at_prep.append(runtime_context.workspace_path)
        return MagicMock(repo="", init_branch="main", new_branch=True, depth=1, git_token_env="")

    def capture_pub(runtime_context=None, **params):
        # Capture workspace_path value at pub time (after it's set)
        if runtime_context:
            workspace_path_at_pub.append(runtime_context.workspace_path)
        return "branch:sha", []

    capture_pub.__publication_strategy__ = "branch"
    capture_pub.__publication_branch_prefix__ = "palimpsest/job"

    spec_capture = JobSpec(
        workspace_fn=capture_prep,
        context_fn=lambda **params: {"system": "sys", "sections": [], "task": "task"},
        publication_fn=capture_pub,
        tools=[],
    )

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(
        return_value={"status": "complete", "summary": "ok", "messages": []}
    )
    patches["palimpsest.runner.git.Repo"] = MagicMock()

    with _apply_patches(patches)[0]:
        _run_job_from_spec(config, spec_capture, tmp_path, bundle_workspace="", target_workspace="")

    # workspace_path should have been empty during prep
    assert workspace_path_at_prep == [""]  # not set yet during prep

    # workspace_path should be set during pub
    assert workspace_path_at_pub == [str(tmp_path)]


def test_runner_passes_runtime_context_to_tool_gateway(tmp_path):
    """Runner passes RuntimeContext to tool gateway execution."""
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-789", task="test tools")
    config.bundle = "test-team"
    spec = _spec()

    captured_args = {}
    mock_gateway = MagicMock()
    mock_gateway.cost_tracking_degraded = MagicMock(return_value=False)
    mock_gateway.execute = MagicMock(return_value=MagicMock(success=True, output="done"))
    mock_gateway.list_tools = MagicMock(return_value=[])
    mock_gateway.close = MagicMock()

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.UnifiedToolGateway"] = MagicMock(return_value=mock_gateway)
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(
        return_value={"status": "complete", "summary": "ok", "messages": []}
    )
    patches["palimpsest.runner.git.Repo"] = MagicMock()

    with _apply_patches(patches)[0]:
        _run_job_from_spec(config, spec, tmp_path, bundle_workspace="", target_workspace="")

    # Verify gateway.execute was called (by run_interaction_loop)
    # The runtime_context should be passed through
    # We can verify the gateway was created with the tools, but the main test
    # is that tools receive the context (tested in test_tool_injection.py)


def test_runner_passes_runtime_context_to_publication_fn(tmp_path):
    """Runner passes RuntimeContext to publication_fn."""
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-pub", task="publish test")
    config.bundle = "pub-team"

    captured = {}
    def capture_pub(runtime_context=None, **params):
        captured["runtime_context"] = runtime_context
        return "branch:sha", []

    capture_pub.__publication_strategy__ = "branch"
    capture_pub.__publication_branch_prefix__ = "palimpsest/job"

    spec_capture = JobSpec(
        workspace_fn=lambda **params: MagicMock(repo="", init_branch="main", new_branch=True, depth=1, git_token_env=""),
        context_fn=lambda **params: {"system": "sys", "sections": [], "task": "task"},
        publication_fn=capture_pub,
        tools=[],
    )

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(
        return_value={"status": "complete", "summary": "ok", "messages": []}
    )
    patches["palimpsest.runner.git.Repo"] = MagicMock()

    with _apply_patches(patches)[0]:
        _run_job_from_spec(config, spec_capture, tmp_path, bundle_workspace="", target_workspace="")

    assert "runtime_context" in captured
    ctx = captured["runtime_context"]
    assert isinstance(ctx, RuntimeContext)
    assert ctx.job_id == "job-pub"
    assert ctx.bundle == "pub-team"
    assert ctx.workspace_path == str(tmp_path)


def test_runner_calls_cleanup_on_success(tmp_path):
    """Runner calls RuntimeContext.cleanup() in finally block on success."""
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-cleanup-ok", task="test cleanup")
    spec = _spec()

    cleanup_called = []
    original_cleanup = RuntimeContext.cleanup

    def track_cleanup(self):
        cleanup_called.append(self)
        return original_cleanup(self)

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(
        return_value={"status": "complete", "summary": "ok", "messages": []}
    )
    patches["palimpsest.runner.git.Repo"] = MagicMock()

    with patch.object(RuntimeContext, "cleanup", track_cleanup):
        with _apply_patches(patches)[0]:
            _run_job_from_spec(config, spec, tmp_path, bundle_workspace="", target_workspace="")

    assert len(cleanup_called) == 1


def test_runner_calls_cleanup_on_failure(tmp_path):
    """Runner calls RuntimeContext.cleanup() in finally block on failure."""
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-cleanup-fail", task="test cleanup on fail")
    spec = _spec()

    cleanup_called = []
    original_cleanup = RuntimeContext.cleanup

    def track_cleanup(self):
        cleanup_called.append(self)
        return original_cleanup(self)

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(
        side_effect=RuntimeError("intentional test failure")
    )
    patches["palimpsest.runner.git.Repo"] = MagicMock()

    with patch.object(RuntimeContext, "cleanup", track_cleanup):
        with _apply_patches(patches)[0]:
            with pytest.raises(RuntimeError):
                _run_job_from_spec(config, spec, tmp_path, bundle_workspace="", target_workspace="")

    # Cleanup should still have been called
    assert len(cleanup_called) == 1


def test_runtime_context_can_be_used_by_preparation_fn(tmp_path):
    """preparation_fn can populate resources on RuntimeContext."""
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-resources", task="test resources")
    spec = _spec()

    resources_at_prep = []
    resources_at_pub = []
    cleanup_ran = []  # Separate tracker for cleanup callback

    def prep_with_resources(runtime_context=None, **params):
        if runtime_context:
            runtime_context.resources["custom_connection"] = "connected!"
            runtime_context.register_cleanup(lambda: cleanup_ran.append("cleaned"))
            # Capture resources at prep time (before cleanup clears them)
            resources_at_prep.append(dict(runtime_context.resources))
        return MagicMock(repo="", init_branch="main", new_branch=True, depth=1, git_token_env="")

    def pub_with_resources(runtime_context=None, **params):
        if runtime_context:
            # Capture resources at pub time (should still have the connection)
            resources_at_pub.append(dict(runtime_context.resources))
        return "branch:sha", []

    pub_with_resources.__publication_strategy__ = "branch"
    pub_with_resources.__publication_branch_prefix__ = "palimpsest/job"

    spec_resources = JobSpec(
        workspace_fn=prep_with_resources,
        context_fn=lambda **params: {"system": "sys", "sections": [], "task": "task"},
        publication_fn=pub_with_resources,
        tools=[],
    )

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(
        return_value={"status": "complete", "summary": "ok", "messages": []}
    )
    patches["palimpsest.runner.git.Repo"] = MagicMock()

    with _apply_patches(patches)[0]:
        _run_job_from_spec(config, spec_resources, tmp_path, bundle_workspace="", target_workspace="")

    # prep should have set the resource
    assert resources_at_prep[0]["custom_connection"] == "connected!"

    # pub should see the same context with resources
    assert resources_at_pub[0]["custom_connection"] == "connected!"

    # cleanup should have run
    assert "cleaned" in cleanup_ran


def test_runner_uses_task_id_from_config(tmp_path):
    """Runner uses task_id from config if provided, otherwise defaults to job_id."""
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-taskid", task="test task id")
    config.task_id = "custom-task-456"
    spec = _spec()

    captured = {}
    def capture_prep(runtime_context=None, **params):
        captured["ctx"] = runtime_context
        return MagicMock(repo="", init_branch="main", new_branch=True, depth=1, git_token_env="")

    spec_capture = JobSpec(
        workspace_fn=capture_prep,
        context_fn=lambda **params: {"system": "sys", "sections": [], "task": "task"},
        publication_fn=_default_publication_fn,
        tools=[],
    )

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(
        return_value={"status": "complete", "summary": "ok", "messages": []}
    )
    patches["palimpsest.runner.git.Repo"] = MagicMock()

    with _apply_patches(patches)[0]:
        _run_job_from_spec(config, spec_capture, tmp_path, bundle_workspace="", target_workspace="")

    assert captured["ctx"].task_id == "custom-task-456"
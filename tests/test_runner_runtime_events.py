from unittest.mock import MagicMock, patch

import pytest

from palimpsest.config import JobConfig
from palimpsest.events import JobCompletedData, JobFailedData, JobStartedData, RuntimeIssueData
from palimpsest.runtime.role_resolver import JobSpec
from palimpsest.runner import ControlledJobFailure, _run_job_from_spec


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


def _base_patches(emitter, tmp_path, **overrides):
    """Return a dict of common patches for runner tests."""
    fake_builtin = MagicMock()
    fake_builtin.as_provider_dict.return_value = {}

    defaults = {
        "palimpsest.runner.EventEmitter": MagicMock(return_value=emitter),
        "palimpsest.runner._read_evo_sha": MagicMock(return_value="abc123"),
        "palimpsest.runner._read_head_sha": MagicMock(return_value="def456"),
        "palimpsest.runner.setup_workspace": MagicMock(return_value=str(tmp_path)),
        "palimpsest.runner.build_context": MagicMock(return_value={"system": "sys", "task": "task"}),
        "palimpsest.runner.LiteLLMGateway": MagicMock(),
        "palimpsest.runner.BuiltinToolProvider": MagicMock(return_value=fake_builtin),
        "palimpsest.runner.resolve_tool_providers": MagicMock(return_value={}),
        "palimpsest.runner.find_duplicate_tool_names": MagicMock(return_value=[]),
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
        if isinstance(mock_val, MagicMock):
            m = stack.enter_context(patch(target, mock_val))
        else:
            m = stack.enter_context(patch(target, mock_val))
        mocks[target] = m
    return stack, mocks


def test_duplicate_tool_names_emit_runtime_issue_and_job_failed(tmp_path):
    emitter = RecordingEmitter()
    config = JobConfig(task="x")
    spec = JobSpec(prompt="sys", context_template={"sections": []}, tools=[])

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.find_duplicate_tool_names"] = MagicMock(return_value=["dup_tool"])

    with _apply_patches(patches)[0]:
        with pytest.raises(Exception):
            _run_job_from_spec(config, spec, tmp_path)

    assert any(isinstance(event, RuntimeIssueData) and event.stage == "interaction" for event in emitter.events)
    assert any(isinstance(event, JobFailedData) and "dup_tool" in event.error and event.code == "duplicate_tool_name" for event in emitter.events)


def test_cleanup_issue_emits_runtime_issue(tmp_path):
    emitter = RecordingEmitter()
    config = JobConfig(task="x")
    spec = JobSpec(prompt="sys", context_template={"sections": []}, tools=[])

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(return_value={"status": "success", "summary": "ok", "messages": []})
    patches["palimpsest.runner.git.Repo"] = MagicMock()
    patches["palimpsest.runner.find_publication_issues"] = MagicMock(return_value=[])
    patches["palimpsest.runner.publish_results"] = MagicMock(return_value="branch:sha")
    patches["palimpsest.runner.finalize_workspace_after_job"] = MagicMock(return_value="cleanup boom")

    with _apply_patches(patches)[0]:
        _run_job_from_spec(config, spec, tmp_path)

    assert any(isinstance(event, JobCompletedData) for event in emitter.events)
    assert any(
        isinstance(event, RuntimeIssueData) and event.stage == "cleanup" and "cleanup boom" in event.details.get("error", "")
        for event in emitter.events
    )


def test_publication_guardrail_reenters_interaction_with_user_prompt(tmp_path):
    emitter = RecordingEmitter()
    config = JobConfig(task="x")
    spec = JobSpec(prompt="sys", context_template={"sections": []}, tools=[])
    interaction_results = [
        {"status": "success", "summary": "first", "messages": [{"role": "user", "content": "initial"}]},
        {"status": "success", "summary": "fixed", "messages": [{"role": "user", "content": "initial"}]},
    ]

    interaction_mock = MagicMock(side_effect=interaction_results)
    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = interaction_mock
    patches["palimpsest.runner.git.Repo"] = MagicMock()
    patches["palimpsest.runner.find_publication_issues"] = MagicMock(side_effect=[["Sensitive-looking file tracked: .env"], []])
    patches["palimpsest.runner.publish_results"] = MagicMock(return_value="branch:sha")

    with _apply_patches(patches)[0]:
        _run_job_from_spec(config, spec, tmp_path)

    assert interaction_mock.call_count == 2
    assert interaction_mock.call_args_list[1].kwargs["messages"] == interaction_results[0]["messages"]
    assert "Publication was blocked" in interaction_mock.call_args_list[1].kwargs["user_prompt"]
    assert any(isinstance(event, RuntimeIssueData) and event.stage == "publication" for event in emitter.events)


def test_publication_guardrail_can_fail_without_retry(tmp_path):
    emitter = RecordingEmitter()
    config = JobConfig(task="x")
    config.publication.max_recovery_attempts = 0
    spec = JobSpec(prompt="sys", context_template={"sections": []}, tools=[])

    interaction_mock = MagicMock(return_value={"status": "success", "summary": "first", "messages": []})
    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = interaction_mock
    patches["palimpsest.runner.git.Repo"] = MagicMock()
    patches["palimpsest.runner.find_publication_issues"] = MagicMock(return_value=["Sensitive-looking file tracked: .env"])

    with _apply_patches(patches)[0]:
        with pytest.raises(Exception):
            _run_job_from_spec(config, spec, tmp_path)

    assert interaction_mock.call_count == 1
    assert any(
        isinstance(event, RuntimeIssueData) and event.stage == "publication" and event.fatal
        for event in emitter.events
    )
    assert any(isinstance(event, JobFailedData) and event.code == "publication_guardrail" for event in emitter.events)


def test_job_started_carries_evo_sha_and_base_sha(tmp_path):
    emitter = RecordingEmitter()
    config = JobConfig(task="x")
    spec = JobSpec(prompt="sys", context_template={"sections": []}, tools=[])

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner._read_evo_sha"] = MagicMock(return_value="evo_abc123")
    patches["palimpsest.runner._read_head_sha"] = MagicMock(return_value="base_def456")
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(return_value={"status": "success", "summary": "ok", "messages": []})
    patches["palimpsest.runner.git.Repo"] = MagicMock()
    patches["palimpsest.runner.find_publication_issues"] = MagicMock(return_value=[])
    patches["palimpsest.runner.publish_results"] = MagicMock(return_value="branch:sha")

    with _apply_patches(patches)[0]:
        _run_job_from_spec(config, spec, tmp_path)

    started = [e for e in emitter.events if isinstance(e, JobStartedData)]
    assert len(started) == 1
    assert started[0].evo_sha == "evo_abc123"
    assert started[0].base_sha == "base_def456"
    assert started[0].workspace_path == str(tmp_path)


def test_job_timeout_emits_failed_with_timeout_code(tmp_path):
    import time as _time
    emitter = RecordingEmitter()
    config = JobConfig(task="x", timeout=1)
    spec = JobSpec(prompt="sys", context_template={"sections": []}, tools=[])

    def slow_interaction(*args, **kwargs):
        _time.sleep(3)
        return {"status": "success", "summary": "ok", "messages": []}

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = slow_interaction

    with _apply_patches(patches)[0]:
        with pytest.raises(ControlledJobFailure) as exc_info:
            _run_job_from_spec(config, spec, tmp_path)
        assert exc_info.value.code == "timeout"

    assert any(
        isinstance(e, JobFailedData) and e.code == "timeout"
        for e in emitter.events
    )

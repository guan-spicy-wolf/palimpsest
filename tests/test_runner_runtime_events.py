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


def test_duplicate_tool_names_emit_runtime_issue_and_job_failed(tmp_path):
    emitter = RecordingEmitter()
    config = JobConfig(task="x")
    spec = JobSpec(prompt="sys", context_template={"sections": []}, tools=[])
    fake_builtin = MagicMock()
    fake_evo = MagicMock()

    with patch("palimpsest.runner.EventEmitter", return_value=emitter), \
         patch("palimpsest.runner._read_evo_sha", return_value="abc123"), \
         patch("palimpsest.runner._read_head_sha", return_value="def456"), \
         patch("palimpsest.runner.setup_workspace", return_value=str(tmp_path)), \
         patch("palimpsest.runner.build_context", return_value={"system": "sys", "task": "task"}), \
         patch("palimpsest.runner.LiteLLMGateway"), \
         patch("palimpsest.runner.BuiltinToolGateway", return_value=fake_builtin), \
         patch("palimpsest.runner.resolve_tool_providers", return_value={}), \
         patch("palimpsest.runner.EvoToolGateway", return_value=fake_evo), \
         patch("palimpsest.runner.find_duplicate_tool_names", return_value=["dup_tool"]), \
         patch("palimpsest.runner.finalize_workspace_after_job", return_value=None):
        with pytest.raises(Exception):
            _run_job_from_spec(config, spec, tmp_path)

    assert any(isinstance(event, RuntimeIssueData) and event.stage == "interaction" for event in emitter.events)
    assert any(isinstance(event, JobFailedData) and "dup_tool" in event.error and event.code == "duplicate_tool_name" for event in emitter.events)


def test_cleanup_issue_emits_runtime_issue(tmp_path):
    emitter = RecordingEmitter()
    config = JobConfig(task="x")
    spec = JobSpec(prompt="sys", context_template={"sections": []}, tools=[])
    fake_repo = MagicMock()

    with patch("palimpsest.runner.EventEmitter", return_value=emitter), \
         patch("palimpsest.runner._read_evo_sha", return_value="abc123"), \
         patch("palimpsest.runner._read_head_sha", return_value="def456"), \
         patch("palimpsest.runner.setup_workspace", return_value=str(tmp_path)), \
         patch("palimpsest.runner.build_context", return_value={"system": "sys", "task": "task"}), \
         patch("palimpsest.runner.LiteLLMGateway"), \
         patch("palimpsest.runner.BuiltinToolGateway", return_value=MagicMock(schema=lambda: [])), \
         patch("palimpsest.runner.resolve_tool_providers", return_value={}), \
         patch("palimpsest.runner.EvoToolGateway", return_value=MagicMock(schema=lambda: [])), \
         patch("palimpsest.runner.find_duplicate_tool_names", return_value=[]), \
         patch("palimpsest.runner.run_interaction_loop", return_value={"status": "success", "summary": "ok", "messages": []}), \
         patch("palimpsest.runner.git.Repo", return_value=fake_repo), \
         patch("palimpsest.runner.find_publication_issues", return_value=[]), \
         patch("palimpsest.runner.publish_results", return_value="branch:sha"), \
         patch("palimpsest.runner.finalize_workspace_after_job", return_value="cleanup boom"):
        _run_job_from_spec(config, spec, tmp_path)

    assert any(isinstance(event, JobCompletedData) for event in emitter.events)
    assert any(
        isinstance(event, RuntimeIssueData) and event.stage == "cleanup" and "cleanup boom" in event.message
        for event in emitter.events
    )


def test_publication_guardrail_reenters_interaction_with_user_prompt(tmp_path):
    emitter = RecordingEmitter()
    config = JobConfig(task="x")
    spec = JobSpec(prompt="sys", context_template={"sections": []}, tools=[])
    fake_repo = MagicMock()
    interaction_results = [
        {"status": "success", "summary": "first", "messages": [{"role": "user", "content": "initial"}]},
        {"status": "success", "summary": "fixed", "messages": [{"role": "user", "content": "initial"}]},
    ]

    with patch("palimpsest.runner.EventEmitter", return_value=emitter), \
         patch("palimpsest.runner._read_evo_sha", return_value="abc123"), \
         patch("palimpsest.runner._read_head_sha", return_value="def456"), \
         patch("palimpsest.runner.setup_workspace", return_value=str(tmp_path)), \
         patch("palimpsest.runner.build_context", return_value={"system": "sys", "task": "task"}), \
         patch("palimpsest.runner.LiteLLMGateway"), \
         patch("palimpsest.runner.BuiltinToolGateway", return_value=MagicMock(schema=lambda: [])), \
         patch("palimpsest.runner.resolve_tool_providers", return_value={}), \
         patch("palimpsest.runner.EvoToolGateway", return_value=MagicMock(schema=lambda: [])), \
         patch("palimpsest.runner.find_duplicate_tool_names", return_value=[]), \
         patch("palimpsest.runner.run_interaction_loop", side_effect=interaction_results) as interaction_loop, \
         patch("palimpsest.runner.git.Repo", return_value=fake_repo), \
         patch("palimpsest.runner.find_publication_issues", side_effect=[["Sensitive-looking file tracked: .env"], []]), \
         patch("palimpsest.runner.publish_results", return_value="branch:sha"), \
         patch("palimpsest.runner.finalize_workspace_after_job", return_value=None):
        _run_job_from_spec(config, spec, tmp_path)

    assert interaction_loop.call_count == 2
    assert interaction_loop.call_args_list[1].kwargs["messages"] == interaction_results[0]["messages"]
    assert "Publication was blocked" in interaction_loop.call_args_list[1].kwargs["user_prompt"]
    assert any(isinstance(event, RuntimeIssueData) and event.stage == "publication" for event in emitter.events)


def test_publication_guardrail_can_fail_without_retry(tmp_path):
    emitter = RecordingEmitter()
    config = JobConfig(task="x")
    config.publication.max_recovery_attempts = 0
    spec = JobSpec(prompt="sys", context_template={"sections": []}, tools=[])
    fake_repo = MagicMock()

    with patch("palimpsest.runner.EventEmitter", return_value=emitter), \
         patch("palimpsest.runner._read_evo_sha", return_value="abc123"), \
         patch("palimpsest.runner._read_head_sha", return_value="def456"), \
         patch("palimpsest.runner.setup_workspace", return_value=str(tmp_path)), \
         patch("palimpsest.runner.build_context", return_value={"system": "sys", "task": "task"}), \
         patch("palimpsest.runner.LiteLLMGateway"), \
         patch("palimpsest.runner.BuiltinToolGateway", return_value=MagicMock(schema=lambda: [])), \
         patch("palimpsest.runner.resolve_tool_providers", return_value={}), \
         patch("palimpsest.runner.EvoToolGateway", return_value=MagicMock(schema=lambda: [])), \
         patch("palimpsest.runner.find_duplicate_tool_names", return_value=[]), \
         patch("palimpsest.runner.run_interaction_loop", return_value={"status": "success", "summary": "first", "messages": []}) as interaction_loop, \
         patch("palimpsest.runner.git.Repo", return_value=fake_repo), \
         patch("palimpsest.runner.find_publication_issues", return_value=["Sensitive-looking file tracked: .env"]), \
         patch("palimpsest.runner.finalize_workspace_after_job", return_value=None):
        with pytest.raises(Exception):
            _run_job_from_spec(config, spec, tmp_path)

    assert interaction_loop.call_count == 1
    assert any(
        isinstance(event, RuntimeIssueData) and event.stage == "publication" and event.fatal
        for event in emitter.events
    )
    assert any(isinstance(event, JobFailedData) and event.code == "publication_guardrail" for event in emitter.events)


def test_job_started_carries_evo_sha_and_base_sha(tmp_path):
    emitter = RecordingEmitter()
    config = JobConfig(task="x")
    spec = JobSpec(prompt="sys", context_template={"sections": []}, tools=[])
    fake_repo = MagicMock()

    with patch("palimpsest.runner.EventEmitter", return_value=emitter), \
         patch("palimpsest.runner._read_evo_sha", return_value="evo_abc123"), \
         patch("palimpsest.runner._read_head_sha", return_value="base_def456"), \
         patch("palimpsest.runner.setup_workspace", return_value=str(tmp_path)), \
         patch("palimpsest.runner.build_context", return_value={"system": "sys", "task": "task"}), \
         patch("palimpsest.runner.LiteLLMGateway"), \
         patch("palimpsest.runner.BuiltinToolGateway", return_value=MagicMock(schema=lambda: [])), \
         patch("palimpsest.runner.resolve_tool_providers", return_value={}), \
         patch("palimpsest.runner.EvoToolGateway", return_value=MagicMock(schema=lambda: [])), \
         patch("palimpsest.runner.find_duplicate_tool_names", return_value=[]), \
         patch("palimpsest.runner.run_interaction_loop", return_value={"status": "success", "summary": "ok", "messages": []}), \
         patch("palimpsest.runner.git.Repo", return_value=fake_repo), \
         patch("palimpsest.runner.find_publication_issues", return_value=[]), \
         patch("palimpsest.runner.publish_results", return_value="branch:sha"), \
         patch("palimpsest.runner.finalize_workspace_after_job", return_value=None):
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

    with patch("palimpsest.runner.EventEmitter", return_value=emitter), \
         patch("palimpsest.runner._read_evo_sha", return_value="abc123"), \
         patch("palimpsest.runner._read_head_sha", return_value="def456"), \
         patch("palimpsest.runner.setup_workspace", return_value=str(tmp_path)), \
         patch("palimpsest.runner.build_context", return_value={"system": "sys", "task": "task"}), \
         patch("palimpsest.runner.LiteLLMGateway"), \
         patch("palimpsest.runner.BuiltinToolGateway", return_value=MagicMock(schema=lambda: [])), \
         patch("palimpsest.runner.resolve_tool_providers", return_value={}), \
         patch("palimpsest.runner.EvoToolGateway", return_value=MagicMock(schema=lambda: [])), \
         patch("palimpsest.runner.find_duplicate_tool_names", return_value=[]), \
         patch("palimpsest.runner.run_interaction_loop", side_effect=slow_interaction), \
         patch("palimpsest.runner.finalize_workspace_after_job", return_value=None):
        with pytest.raises(ControlledJobFailure) as exc_info:
            _run_job_from_spec(config, spec, tmp_path)
        assert exc_info.value.code == "timeout"

    assert any(
        isinstance(e, JobFailedData) and e.code == "timeout"
        for e in emitter.events
    )

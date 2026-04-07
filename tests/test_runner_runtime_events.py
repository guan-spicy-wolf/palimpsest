from pathlib import Path
from unittest.mock import MagicMock, patch, call

import git
import pytest

from palimpsest.config import JobConfig
from palimpsest.events import (
    JobCompletedData,
    JobFailedData,
    JobStartedData,
    JobStartedData,
    RuntimeIssueData,
)
from palimpsest.runtime.roles import JobSpec
from palimpsest.stages.publication import PublicationGuardrailViolation
from palimpsest.runner import ControlledJobFailure, _run_job_from_spec, run_job


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


def _default_publication_fn(
    *,
    result=None,
    repo="",
    **params,
):
    if (result or {}).get("status") == "failed":
        return None, []
    if not repo:
        return None, []
    return "branch:sha", []


_default_publication_fn.__publication_strategy__ = "branch"
_default_publication_fn.__publication_branch_prefix__ = "palimpsest/job"


def _spec(publication_fn=None) -> JobSpec:
    return JobSpec(
        workspace_fn=lambda **params: MagicMock(repo="", init_branch="main", new_branch=True, depth=1, git_token_env=""),
        context_fn=lambda **params: {"system": "sys", "sections": [], "task": params.get("goal") or params.get("task", "")},
        publication_fn=publication_fn or _default_publication_fn,
        tools=[],
    )


def _base_patches(emitter, tmp_path, **overrides):
    """Return a dict of common patches for runner tests."""
    defaults = {
        "palimpsest.runner.EventEmitter": MagicMock(return_value=emitter),
        "palimpsest.runner._read_evo_sha": MagicMock(return_value="abc123"),
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
        if isinstance(mock_val, MagicMock):
            m = stack.enter_context(patch(target, mock_val))
        else:
            m = stack.enter_context(patch(target, mock_val))
        mocks[target] = m
    return stack, mocks


def test_duplicate_tool_names_emit_runtime_issue_and_job_failed(tmp_path):
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-1", task="x")
    spec = _spec()

    # Make UnifiedToolGateway raise ValueError for duplicate tools
    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.UnifiedToolGateway"] = MagicMock(
        side_effect=ValueError("Duplicate tool names configured: dup_tool")
    )
    patches["palimpsest.runner.git.Repo"] = MagicMock()

    with _apply_patches(patches)[0]:
        with pytest.raises(Exception):
            _run_job_from_spec(config, spec, tmp_path)

    assert any(isinstance(event, JobFailedData) and "dup_tool" in event.error for event in emitter.events)


def test_cleanup_issue_calls_finalize_with_gateway(tmp_path):
    """Verify finalize_workspace_after_job is called with the gateway so it can emit events."""
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-1", task="x")
    spec = _spec()

    finalize_mock = MagicMock(return_value="cleanup boom")
    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(
        return_value={"status": "complete", "summary": "ok", "messages": []}
    )
    patches["palimpsest.runner.git.Repo"] = MagicMock()
    patches["palimpsest.runner.finalize_workspace_after_job"] = finalize_mock

    with _apply_patches(patches)[0]:
        _run_job_from_spec(config, spec, tmp_path)

    assert any(isinstance(event, JobCompletedData) for event in emitter.events)
    # Verify finalize was called with gateway kwarg (stage handles event emission)
    finalize_mock.assert_called_once()
    _, kwargs = finalize_mock.call_args
    assert "gateway" in kwargs
    assert kwargs["gateway"] is not None


def test_publication_guardrail_reenters_interaction_with_user_prompt(tmp_path):
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-1", task="x")
    config.workspace.repo = "https://example.com/repo.git"
    publication_mock = MagicMock(
        side_effect=[
            PublicationGuardrailViolation(["Sensitive-looking file tracked: .env"]),
            ("branch:sha", []),
        ]
    )
    publication_mock.__publication_strategy__ = "branch"
    publication_mock.__publication_branch_prefix__ = "palimpsest/job"
    spec = _spec(publication_fn=publication_mock)
    interaction_results = [
        {"summary": "first", "messages": [{"role": "user", "content": "initial"}]},
        {"summary": "fixed", "messages": [{"role": "user", "content": "initial"}]},
    ]

    interaction_mock = MagicMock(side_effect=interaction_results)
    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = interaction_mock
    patches["palimpsest.runner.git.Repo"] = MagicMock()

    with _apply_patches(patches)[0]:
        _run_job_from_spec(config, spec, tmp_path)

    assert interaction_mock.call_count == 2
    assert interaction_mock.call_args_list[1].kwargs["messages"] == interaction_results[0]["messages"]
    assert "Publication was blocked" in interaction_mock.call_args_list[1].kwargs["user_prompt"]
    assert any(isinstance(event, RuntimeIssueData) and event.stage == "publication" for event in emitter.events)


def test_publication_guardrail_can_fail_without_retry(tmp_path):
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-1", task="x")
    config.workspace.repo = "https://example.com/repo.git"
    config.publication.max_recovery_attempts = 0
    publication_mock = MagicMock(side_effect=PublicationGuardrailViolation(["Sensitive-looking file tracked: .env"]))
    publication_mock.__publication_strategy__ = "branch"
    publication_mock.__publication_branch_prefix__ = "palimpsest/job"
    spec = _spec(publication_fn=publication_mock)

    interaction_mock = MagicMock(return_value={"summary": "first", "messages": []})
    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = interaction_mock
    patches["palimpsest.runner.git.Repo"] = MagicMock()

    with _apply_patches(patches)[0]:
        with pytest.raises(Exception):
            _run_job_from_spec(config, spec, tmp_path)

    assert interaction_mock.call_count == 1
    assert any(
        isinstance(event, RuntimeIssueData) and event.stage == "publication" and event.fatal
        for event in emitter.events
    )
    assert any(isinstance(event, JobFailedData) and event.code == "publication_guardrail" for event in emitter.events)


def test_job_started_emitted_by_setup_workspace(tmp_path):
    """Verify setup_workspace is called with gateway and evo_sha so it can emit JobStartedData."""
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-1", task="x")
    spec = _spec()

    setup_mock = MagicMock(return_value=str(tmp_path))
    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner._read_evo_sha"] = MagicMock(return_value="evo_abc123")
    patches["palimpsest.runner.setup_workspace"] = setup_mock
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(
        return_value={"status": "complete", "summary": "ok", "messages": []}
    )
    patches["palimpsest.runner.git.Repo"] = MagicMock()

    with _apply_patches(patches)[0]:
        _run_job_from_spec(config, spec, tmp_path)

    # Verify setup_workspace was called with gateway and evo_sha
    setup_mock.assert_called_once()
    _, kwargs = setup_mock.call_args
    assert kwargs["evo_sha"] == "evo_abc123"
    assert kwargs["gateway"] is not None


def test_runner_propagates_cost_tracking_degraded_flag(tmp_path):
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-1", task="x")
    config.llm.model = "unknown-model"
    config.llm.max_total_cost = 0.5
    publication_mock = MagicMock(return_value=("branch:sha", []))
    publication_mock.__publication_strategy__ = "branch"
    publication_mock.__publication_branch_prefix__ = "palimpsest/job"
    spec = _spec(publication_fn=publication_mock)

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(
        return_value={"status": "partial", "code": "budget_exhausted", "budget_dim": "cost", "summary": "wip", "messages": []}
    )
    patches["palimpsest.runner.git.Repo"] = MagicMock()

    with _apply_patches(patches)[0]:
        _run_job_from_spec(config, spec, tmp_path)

    completed = [event for event in emitter.events if isinstance(event, JobCompletedData)]
    assert completed[-1].cost_tracking_degraded is True


def test_job_timeout_emits_failed_with_timeout_code(tmp_path):
    import time as _time
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-1", task="x", timeout=1)
    spec = _spec()

    def slow_interaction(*args, **kwargs):
        _time.sleep(3)
        return {"summary": "ok", "messages": []}

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = slow_interaction
    patches["palimpsest.runner.git.Repo"] = MagicMock()

    with _apply_patches(patches)[0]:
        with pytest.raises(ControlledJobFailure) as exc_info:
            _run_job_from_spec(config, spec, tmp_path)
        assert exc_info.value.code == "timeout"

    assert any(
        isinstance(e, JobFailedData) and e.code == "timeout"
        for e in emitter.events
    )


def test_runner_emits_budget_exhausted_code_on_clean_partial_exit(tmp_path):
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-1", task="x")
    publication_mock = MagicMock(return_value=("branch:sha", []))
    publication_mock.__publication_strategy__ = "branch"
    publication_mock.__publication_branch_prefix__ = "palimpsest/job"
    spec = _spec(publication_fn=publication_mock)

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(
        return_value={"status": "partial", "code": "budget_exhausted", "budget_dim": "cost", "summary": "wip", "messages": []}
    )
    patches["palimpsest.runner.git.Repo"] = MagicMock()

    with _apply_patches(patches)[0]:
        _run_job_from_spec(config, spec, tmp_path)

    completed = [event for event in emitter.events if isinstance(event, JobCompletedData)]
    assert completed
    assert completed[-1].code == "budget_exhausted"
    assert completed[-1].budget_dim == "cost"


def test_runner_skips_publication_for_repoless_job(tmp_path):
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-1", task="x")
    config.workspace.repo = ""
    publication_mock = MagicMock(side_effect=_default_publication_fn)
    publication_mock.__publication_strategy__ = "branch"
    publication_mock.__publication_branch_prefix__ = "palimpsest/job"
    spec = _spec(publication_fn=publication_mock)

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(
        return_value={"status": "complete", "summary": "meta", "messages": []}
    )
    patches["palimpsest.runner.git.Repo"] = MagicMock(side_effect=Exception("no repo"))

    with _apply_patches(patches)[0]:
        _run_job_from_spec(config, spec, tmp_path)

    publication_mock.assert_called_once()
    completed = [event for event in emitter.events if isinstance(event, JobCompletedData)]
    assert completed[-1].git_ref is None


def test_runner_marks_job_failed_when_publication_fails_after_budget_exhaustion(tmp_path):
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-1", task="x")
    config.workspace.repo = "https://example.com/repo.git"
    publication_mock = MagicMock(side_effect=RuntimeError("push failed"))
    publication_mock.__publication_strategy__ = "branch"
    publication_mock.__publication_branch_prefix__ = "palimpsest/job"
    spec = _spec(publication_fn=publication_mock)

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(
        return_value={"status": "partial", "code": "budget_exhausted", "budget_dim": "cost", "summary": "wip", "messages": []}
    )
    patches["palimpsest.runner.git.Repo"] = MagicMock()

    with _apply_patches(patches)[0]:
        with pytest.raises(Exception):
            _run_job_from_spec(config, spec, tmp_path)

    assert any(isinstance(event, JobFailedData) and "push failed" in event.error for event in emitter.events)
    assert not any(isinstance(event, JobCompletedData) for event in emitter.events)


def test_run_job_resolves_role_before_dispatch(monkeypatch, tmp_path):
    called = {}

    def fake_run_from_spec(config, spec, evo_path, *, resolved_evo_sha=None):
        called["job_id"] = config.job_id
        called["source_role"] = spec.source_role
        called["evo_path"] = evo_path.name
        called["resolved_evo_sha"] = resolved_evo_sha

    # Create a minimal role in the evo directory
    monkeypatch.chdir(Path(__file__).resolve().parent.parent)
    # Bundle MVP: create bundle roles directory
    roles_dir = Path("evo/factorio/roles")
    roles_dir.mkdir(parents=True, exist_ok=True)
    (roles_dir / "worker.py").write_text("""\nfrom palimpsest.runtime import JobSpec, role, workspace_config, context_spec, git_publication

@role(name='worker', description='test role')
def worker() -> JobSpec:
    return JobSpec(
        preparation_fn=workspace_config(),
        context_fn=context_spec('sys', []),
        publication_fn=git_publication(strategy='skip'),
        tools=[],
    )
""")
    
    monkeypatch.setattr("palimpsest.runner._run_job_from_spec", fake_run_from_spec)

    config = JobConfig(job_id="job-1", task="x", role="worker", bundle="factorio")
    run_job(config)

    assert called["job_id"] == "job-1"
    assert called["source_role"] == "worker"
    assert called["evo_path"] == "evo"


def test_run_job_materializes_requested_evo_sha(monkeypatch, tmp_path):
    root = tmp_path / "root"
    evo = root / "evo"
    # Bundle MVP: roles are in evo/<bundle>/roles/
    roles = evo / "factorio" / "roles"
    roles.mkdir(parents=True)
    repo = git.Repo.init(evo)
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Test Agent")
        writer.set_value("user", "email", "agent@example.com")

    def write_role(version: str) -> None:
        (roles / "worker.py").write_text(
            "\n".join(
                [
                    "from palimpsest.runtime import JobSpec, context_spec, git_publication, role, workspace_config",
                    "",
                    "@role(name='worker', description='test role')",
                    "def worker_role() -> JobSpec:",
                    "    return JobSpec(",
                    "        workspace_fn=workspace_config(),",
                    "        context_fn=context_spec('sys', []),",
                    "        publication_fn=git_publication(strategy='skip'),",
                    f"        tools=['{version}'],",
                    "    )",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    write_role("v1")
    repo.index.add(["factorio/roles/worker.py"])
    commit_v1 = repo.index.commit("v1").hexsha

    write_role("v2")
    repo.index.add(["factorio/roles/worker.py"])
    repo.index.commit("v2")

    captured = {}

    def fake_run_from_spec(config, spec, evo_path, *, resolved_evo_sha=None):
        captured["tools"] = spec.tools
        captured["evo_path"] = evo_path
        captured["resolved_evo_sha"] = resolved_evo_sha

    monkeypatch.chdir(root)
    monkeypatch.setattr("palimpsest.runner._run_job_from_spec", fake_run_from_spec)

    run_job(JobConfig(job_id="job-sha", task="x", role="worker", bundle="factorio", evo_sha=commit_v1))

    assert captured["tools"] == ["v1"]
    assert captured["resolved_evo_sha"] == commit_v1
    assert captured["evo_path"] != evo


def test_job_completed_exposes_empty_artifact_bindings_by_default(tmp_path):
    """ADR-0013: JobCompletedData exposes artifact_bindings=[] by default.

    This regression test verifies that the new artifact_bindings field
    lands without changing today's git-first execution. When branch-based
    publication succeeds, the event should have artifact_bindings == [].
    """
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-adr0013", task="verify artifact bindings")
    config.workspace.repo = "https://example.com/repo.git"
    publication_mock = MagicMock(return_value=("palimpsest/job-adr0013:abc123", []))
    publication_mock.__publication_strategy__ = "branch"
    publication_mock.__publication_branch_prefix__ = "palimpsest/job"
    spec = _spec(publication_fn=publication_mock)

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(
        return_value={"status": "complete", "summary": "ok", "messages": []}
    )
    patches["palimpsest.runner.git.Repo"] = MagicMock()

    with _apply_patches(patches)[0]:
        _run_job_from_spec(config, spec, tmp_path)

    completed = [event for event in emitter.events if isinstance(event, JobCompletedData)]
    assert completed, "Expected JobCompletedData event"
    event = completed[-1]
    # ADR-0013 contract: artifact_bindings defaults to empty list
    assert event.artifact_bindings == [], f"Expected [], got {event.artifact_bindings}"
    # git_ref remains unchanged (current behavior)
    assert event.git_ref == "palimpsest/job-adr0013:abc123"

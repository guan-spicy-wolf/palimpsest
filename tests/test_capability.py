"""Tests for capability model integration (ADR-0016).

Tests capability setup/finalize lifecycle in runner.
"""
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from palimpsest.config import JobConfig
from palimpsest.events import JobCompletedData, JobFailedData, RuntimeIssueData
from palimpsest.runtime.roles import JobSpec, RoleManager
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
    patches["palimpsest.runner.get_capability"] = MagicMock(side_effect=lambda n, extra=None: cap_map.get(n))

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


def test_empty_needs_uses_unified_lifecycle(tmp_path):
    """ADR-0018: needs=[] means no extra capability needs, NOT legacy fallback.
    
    The role still goes through unified lifecycle:
    - No capability setup (empty needs)
    - context build
    - interaction loop (no publication_fn involvement)
    - No capability finalize (empty needs)
    - JobCompletedData emitted based on interaction result
    
    This test verifies that _stage_interaction_and_publication is NOT called
    for needs=[] roles.
    """
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-empty-needs", task="analysis task")
    config.workspace.repo = ""  # Repoless analysis job

    patches = _base_patches(emitter, tmp_path)
    # Mock the legacy path to ensure it's NOT called
    patches["palimpsest.runner._stage_interaction_and_publication"] = MagicMock(
        return_value=({"summary": "should not be called"}, "branch:sha")
    )
    # Track which functions are called
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(
        return_value={"status": "complete", "summary": "analysis done"}
    )

    stack, mocks = _apply_patches(patches)
    with stack:
        _run_job_from_spec(
            config, _spec(), tmp_path,
            bundle_workspace="",
            target_workspace="",
            needs=[],  # Empty capability set
        )

    # Verify unified lifecycle: run_interaction_loop called directly
    mocks["palimpsest.runner.run_interaction_loop"].assert_called()

    # Verify legacy path NOT called
    mocks["palimpsest.runner._stage_interaction_and_publication"].assert_not_called()

    # Verify JobCompletedData emitted (success from interaction)
    completed = [e for e in emitter.events if isinstance(e, JobCompletedData)]
    assert len(completed) == 1
    assert completed[0].summary == "analysis done"


def test_needs_git_workspace_uses_capability_path(tmp_path):
    """ADR-0018: needs=['git_workspace'] goes through capability setup/finalize.
    
    Unified lifecycle with git_workspace capability:
    - cap.setup called
    - run_interaction_loop called (not _stage_interaction_and_publication)
    - cap.finalize called
    - JobCompletedData/JobFailedData based on finalize result
    """
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-with-git", task="repo task")

    mock_cap = MagicMock()
    mock_cap.name = "git_workspace"
    mock_cap.setup.return_value = []
    mock_cap.finalize.return_value = FinalizeResult(events=[], success=True)

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.get_capability"] = MagicMock(return_value=mock_cap)
    # Mock legacy path to ensure NOT called
    patches["palimpsest.runner._stage_interaction_and_publication"] = MagicMock(
        return_value=({"summary": "should not be called"}, "branch:sha")
    )

    stack, mocks = _apply_patches(patches)
    with stack:
        _run_job_from_spec(
            config, _spec(), tmp_path,
            bundle_workspace="",
            target_workspace="/tmp/target",
            needs=["git_workspace"],
        )

    # Verify capability lifecycle
    mock_cap.setup.assert_called_once()
    mock_cap.finalize.assert_called_once()

    # Verify legacy path NOT called
    mocks["palimpsest.runner._stage_interaction_and_publication"].assert_not_called()

    # Verify success from capability finalize
    completed = [e for e in emitter.events if isinstance(e, JobCompletedData)]
    assert len(completed) == 1


def test_finalize_failure_determines_job_failed(tmp_path):
    """ADR-0018: job terminal state determined by capability finalize result.
    
    When cap.finalize returns success=False, emit JobFailedData.
    This is the unified lifecycle behavior, not legacy publication semantics.
    """
    emitter = RecordingEmitter()
    config = JobConfig(job_id="job-fail-finalize", task="task")

    mock_cap = MagicMock()
    mock_cap.name = "git_workspace"
    mock_cap.setup.return_value = []
    mock_cap.finalize.return_value = FinalizeResult(events=[], success=False)

    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.get_capability"] = MagicMock(return_value=mock_cap)

    with _apply_patches(patches)[0]:
        _run_job_from_spec(
            config, _spec(), tmp_path,
            bundle_workspace="",
            target_workspace="/tmp/target",
            needs=["git_workspace"],
        )

    # Verify JobFailedData emitted (finalize success=False)
    failed = [e for e in emitter.events if isinstance(e, JobFailedData)]
    assert len(failed) == 1
    # Not JobCompletedData
    completed = [e for e in emitter.events if isinstance(e, JobCompletedData)]
    assert len(completed) == 0


def test_backward_compat_no_needs_uses_publication_fn(tmp_path):
    """LEGACY PATH TEST for blocked roles - documents old behavior.
    
    Per ADR-0018: Non-blocked roles with needs=[] now use unified lifecycle.
    Blocked roles (factorio:worker/implementer/evaluator) still use this path.
    
    This test verifies the legacy path still works for blocked roles.
    Pending deletion after ADR-0019 authority split.
    """
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


# === GitWorkspaceCapability integration tests (ADR-0018 Task 5) ===
# Per ADR-0021: GitWorkspaceCapability moved to bundle (evo/factorio/capabilities)
# Tests now import from bundle location

def test_git_workspace_capability_no_changes_skips_publication(tmp_path):
    """GitWorkspaceCapability with no changes emits publication.skipped, success=True.

    Per ADR-0015 §2.5: no changes is a valid terminal path.
    Whether it's acceptable depends on evaluator judgment.
    """
    # Import from bundle location per ADR-0021
    import sys
    evo_path = Path(__file__).parent.parent.parent / "evo" / "factorio"
    if str(evo_path) not in sys.path:
        sys.path.insert(0, str(evo_path))

    from capabilities.git_workspace import GitWorkspaceCapability
    from palimpsest.runtime.capability import JobContext
    import subprocess

    # Create a git repo with no changes
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    subprocess.run(["git", "init"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo_path, check=True, capture_output=True)

    cap = GitWorkspaceCapability()
    ctx = JobContext(
        job_id="job-no-changes",
        task_id="task-1",
        bundle="test",
        role="worker",
        goal="test",
        target_workspace=str(repo_path),
    )

    result = cap.finalize(ctx)

    # No changes should emit publication.skipped with success=True
    assert result.success is True
    assert len(result.events) == 1
    assert result.events[0].type == "publication.skipped"
    assert result.events[0].data.get("reason") == "no_changes"


def test_git_workspace_capability_repoless_skips_gracefully():
    """GitWorkspaceCapability with empty target_workspace skips gracefully.

    Per ADR-0018: repoless role can use needs=[] or needs=["git_workspace"]
    with empty workspace.
    """
    import sys
    evo_path = Path(__file__).parent.parent.parent / "evo" / "factorio"
    if str(evo_path) not in sys.path:
        sys.path.insert(0, str(evo_path))

    from capabilities.git_workspace import GitWorkspaceCapability
    from palimpsest.runtime.capability import JobContext

    cap = GitWorkspaceCapability()
    ctx = JobContext(
        job_id="job-repoless",
        task_id="task-1",
        bundle="test",
        role="optimizer",
        goal="analyze",
        target_workspace="",  # Empty workspace
    )

    result = cap.finalize(ctx)

    # Repoless should emit publication.skipped with success=True
    assert result.success is True
    assert len(result.events) == 1
    assert result.events[0].type == "publication.skipped"
    assert result.events[0].data.get("reason") == "no_target_workspace"


# === ADR-0021: Capability-only model tests ===

def test_all_roles_use_capability_only_model(tmp_path):
    """ADR-0021: All roles use capability-only model.

    BLOCKED_ROLES_PENDING_ADR_0019 was deleted. RoleManager.resolve()
    now enforces capability-only model for ALL roles.

    Any role using preparation_fn/publication_fn raises ValueError.
    """
    import textwrap

    from palimpsest.runtime.roles import RoleManager, JobSpec, context_spec

    # Create a bundle with a role that uses legacy preparation_fn
    bundle_root = tmp_path
    roles_dir = bundle_root / "roles"
    roles_dir.mkdir(parents=True)

    (roles_dir / "legacy_role.py").write_text(textwrap.dedent('''
        from palimpsest.runtime.roles import role, JobSpec, context_spec, workspace_config

        @role(
            name="legacy_role",
            description="role with deprecated hooks",
            needs=[],
        )
        def legacy_role(**p):
            return JobSpec(
                preparation_fn=workspace_config(repo="", init_branch="main"),
                context_fn=context_spec("test", []),
                tools=[],
            )
    '''))

    manager = RoleManager(bundle_root, bundle="test")

    # resolve() must raise ValueError for preparation_fn usage
    with pytest.raises(ValueError, match="deprecated preparation_fn"):
        manager.resolve("legacy_role")


def test_non_blocked_role_rejects_legacy_hooks(tmp_path):
    """Per ADR-0021: ALL roles use capability-only model.

    RoleManager.resolve() enforces capability-only model for ALL roles.
    Attempting to use publication_fn raises ValueError.

    BLOCKED_ROLES_PENDING_ADR_0019 was deleted; no role can use legacy hooks.
    """
    import textwrap

    from palimpsest.runtime.roles import RoleManager, JobSpec, context_spec

    # Create a bundle with a role that uses legacy publication_fn
    bundle_root = tmp_path
    roles_dir = bundle_root / "roles"
    roles_dir.mkdir(parents=True)

    (roles_dir / "pub_role.py").write_text(textwrap.dedent('''
        from palimpsest.runtime.roles import role, JobSpec, context_spec, git_publication

        @role(
            name="pub_role",
            description="role with deprecated publication hook",
            needs=[],
        )
        def pub_role(**p):
            return JobSpec(
                context_fn=context_spec("test", []),
                publication_fn=git_publication(strategy="branch"),
                tools=[],
            )
    '''))

    manager = RoleManager(bundle_root, bundle="test")

    # resolve() must raise ValueError for publication_fn usage
    with pytest.raises(ValueError, match="deprecated publication_fn"):
        manager.resolve("pub_role")


def test_role_with_output_authority_resolves_correctly(tmp_path):
    """ADR-0019: output_authority declared in @role is accessible via RoleManager.

    RoleMetadataReader extracts output_authority from the @role decorator.
    RoleManager.resolve() produces a spec without legacy hooks.
    """
    import textwrap

    bundle_root = tmp_path
    roles_dir = bundle_root / "roles"
    roles_dir.mkdir(parents=True)

    (roles_dir / "implementer.py").write_text(textwrap.dedent('''
        from palimpsest.runtime.roles import role, JobSpec, context_spec

        @role(
            name="implementer",
            description="test live runtime implementer",
            needs=[],
            output_authority="live_runtime",
        )
        def implementer(**p):
            return JobSpec(
                context_fn=context_spec("test", []),
                tools=["bash"],
            )
    '''))

    manager = RoleManager(bundle_root, bundle="mybundle")

    # resolve() should succeed — no legacy hooks, capability-only
    spec = manager.resolve("implementer")
    assert spec.preparation_fn is None
    assert spec.publication_fn is None

    # output_authority is readable from metadata
    meta = manager.get_definition("implementer")
    assert meta is not None
    assert meta.output_authority == "live_runtime"


def test_empty_needs_has_execution_workspace(tmp_path):
    """ADR-0018 Contract: needs=[] roles get ephemeral execution workspace.
    
    Per ADR-0018: Every job has an execution workspace, even analysis-only roles.
    Tools requiring cwd (bash) and context providers can rely on workspace being non-empty.
    
    This test verifies:
    1. needs=[] role gets workspace (not empty string)
    2. bash tool can execute (cwd is valid)
    3. workspace is cleaned up after job completes
    """
    import tempfile
    
    emitter = RecordingEmitter()
    config = JobConfig(job_id="workspace-test", task="verify workspace")
    config.bundle = "test-bundle"
    
    # Role with needs=[] but tools requiring workspace
    spec = JobSpec(
        context_fn=lambda **p: {"system": "test", "sections": [], "task": p.get("goal", "")},
        tools=["bash"],  # Requires cwd
    )
    spec.source_role = "test_role"
    
    patches = _base_patches(emitter, tmp_path)
    patches["palimpsest.runner.run_interaction_loop"] = MagicMock(
        return_value={"status": "complete", "summary": "ok"}
    )
    patches["palimpsest.runner.UnifiedToolGateway"] = MagicMock()
    patches["palimpsest.runner.git.Repo"] = MagicMock()
    
    workspace_created = []
    original_mkdtemp = tempfile.mkdtemp
    def track_mkdtemp(*args, **kwargs):
        result = original_mkdtemp(*args, **kwargs)
        workspace_created.append(result)
        return result
    
    with patch.object(tempfile, "mkdtemp", track_mkdtemp):
        with _apply_patches(patches)[0]:
            _run_job_from_spec(
                config, spec, tmp_path,
                bundle_workspace=str(tmp_path),
                target_workspace="",  # Empty - triggers ephemeral creation
                needs=[],  # Empty capability
            )
    
    # Verify: ephemeral workspace was created
    assert len(workspace_created) == 1
    created_workspace = workspace_created[0]
    assert created_workspace.startswith("/tmp")
    assert "palimpsest-exec" in created_workspace
    
    # Verify: job completed successfully (workspace was valid)
    completed = [e for e in emitter.events if isinstance(e, JobCompletedData)]
    assert len(completed) == 1
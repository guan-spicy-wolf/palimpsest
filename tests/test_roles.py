from types import SimpleNamespace

from pathlib import Path

from palimpsest.config import JobConfig
from palimpsest.runtime.contexts import resolve_context_functions
from palimpsest.runtime.roles import RoleManager, TeamManager, role, RoleMetadata

EVO_ROOT = Path(__file__).parent.parent / "evo"  # NOTE: tests/fixtures/evo used by optimizer_role_discovery tests


def test_role_decorator_accepts_max_cost():
    """Role decorator accepts max_cost parameter (ADR-0004 D1a)."""
    @role(name="test", description="test role", max_cost=5.00)
    def test_role(**params):
        pass

    assert test_role.__role_metadata__.max_cost == 5.00


def test_role_decorator_max_cost_defaults_to_10():
    """Role decorator max_cost defaults to 10.0 when not specified."""
    @role(name="default_test", description="default test")
    def default_role(**params):
        pass

    assert default_role.__role_metadata__.max_cost == 10.0


def test_planner_role_has_conservative_max_cost():
    """Planner role has conservative max_cost per ADR-0004."""
    manager = RoleManager(EVO_ROOT)
    meta = manager.get_definition("planner")
    assert meta is not None
    assert meta.max_cost == 0.50  # Conservative default per ADR-0004


def test_implementer_role_max_cost():
    """Implementer role has appropriate max_cost."""
    manager = RoleManager(EVO_ROOT)
    meta = manager.get_definition("implementer")
    assert meta is not None
    assert meta.max_cost == 2.00  # Per ADR-0004


def test_reviewer_role_max_cost():
    """Reviewer role has appropriate max_cost."""
    manager = RoleManager(EVO_ROOT)
    meta = manager.get_definition("reviewer")
    assert meta is not None
    assert meta.max_cost == 1.00  # Per ADR-0004


def test_role_manager_loads_described_roles():
    role = RoleManager(EVO_ROOT).get_definition("planner")
    assert role.name == "planner"
    assert role.description


def test_planner_role_includes_join_context():
    spec = RoleManager(EVO_ROOT).resolve("planner", mode="join")
    context_spec = spec.context_fn(goal="goal")
    section_types = [section["type"] for section in context_spec["sections"]]
    assert "join_context" in section_types
    assert "create_pr" in spec.tools


def test_planner_initial_mode_uses_initial_context():
    spec = RoleManager(EVO_ROOT).resolve("planner", mode="initial")
    context_spec = spec.context_fn(goal="goal")
    section_types = [section["type"] for section in context_spec["sections"]]
    assert "join_context" not in section_types
    assert "file_tree" in section_types


def test_team_manager_loads_team_definition():
    team = TeamManager(EVO_ROOT).resolve("backend")
    assert team.planner_role == "planner"
    assert team.eval_role == "evaluator"
    assert "implementer" in team.roles


def test_available_roles_context_is_scoped_to_team():
    funcs = resolve_context_functions(EVO_ROOT, ["available_roles"])
    rendered = funcs["available_roles"](
        evo_root=str(EVO_ROOT),
        job_config=JobConfig(team="backend"),
    )
    assert "Team: backend" in rendered
    assert "implementer" in rendered
    assert "reviewer" in rendered


def test_available_roles_degrades_missing_role():
    funcs = resolve_context_functions(EVO_ROOT, ["available_roles"])
    original_resolve = TeamManager.resolve

    def fake_resolve(self, name):
        return SimpleNamespace(
            name="broken",
            description="Broken team",
            roles=["missing-role"],
            planner_role="planner",
            eval_role="evaluator",
        )

    TeamManager.resolve = fake_resolve
    try:
        rendered = funcs["available_roles"](
            evo_root=str(EVO_ROOT),
            job_config=JobConfig(team="broken"),
        )
    finally:
        TeamManager.resolve = original_resolve
    assert "missing-role" in rendered
    assert "[Unavailable role definition:" in rendered


def test_eval_context_includes_child_task_state_summaries():
    funcs = resolve_context_functions(EVO_ROOT, ["eval_context"])

    class FakeEmitter:
        def __init__(self, config):
            pass

        def fetch_all(self, *, type_=None, source=None, limit=100):
            if type_ == "supervisor.task.completed":
                return [
                    SimpleNamespace(
                        data={"task_id": "child-1", "summary": "done"},
                    )
                ]
            return []

        def close(self):
            return None

    original_emitter = funcs["eval_context"].__globals__["EventEmitter"]
    funcs["eval_context"].__globals__["EventEmitter"] = FakeEmitter
    try:
        rendered = funcs["eval_context"](
            job_config=JobConfig.model_validate(
                {
                    "context": {
                        "eval": {
                            "task_id": "root",
                            "goal": "goal",
                            "child_task_ids": ["child-1"],
                        }
                    }
                }
            )
        )
    finally:
        funcs["eval_context"].__globals__["EventEmitter"] = original_emitter

    assert "child-1: completed - done" in rendered


def test_join_context_includes_child_git_ref_and_semantic_summary():
    funcs = resolve_context_functions(EVO_ROOT, ["join_context"])

    class FakeEmitter:
        def __init__(self, config):
            pass

        def fetch_all(self, *, type_=None, source=None, limit=100):
            if type_ == "supervisor.job.launched":
                return [
                    SimpleNamespace(
                        data={
                            "task_id": "child-1",
                            "job_id": "job-1",
                            "repo": "https://github.com/example/repo.git",
                            "init_branch": "main",
                        },
                    )
                ]
            if type_ == "supervisor.task.completed":
                return [
                    SimpleNamespace(
                        data={
                            "task_id": "child-1",
                            "summary": "done",
                            "result": {
                                "semantic": {
                                    "verdict": "pass",
                                    "summary": "looks good",
                                    "criteria_results": [{"criterion": "tests pass", "verdict": "pass"}],
                                },
                                "structural": {"success": 1},
                                "trace": [
                                    {
                                        "job_id": "job-1",
                                        "role": "implementer",
                                        "outcome": "success",
                                        "git_ref": "palimpsest/job/demo:deadbeef",
                                        "summary": "implemented",
                                    }
                                ],
                            },
                        },
                    )
                ]
            return []

        def close(self):
            return None

    original_emitter = funcs["join_context"].__globals__["EventEmitter"]
    funcs["join_context"].__globals__["EventEmitter"] = FakeEmitter
    try:
        rendered = funcs["join_context"](
            job_config=JobConfig.model_validate(
                {
                    "context": {
                        "join": {
                            "parent_job_id": "parent",
                            "parent_task_id": "root",
                            "parent_summary": "Parent goal",
                            "child_task_ids": ["child-1"],
                        }
                    }
                }
            )
        )
    finally:
        funcs["join_context"].__globals__["EventEmitter"] = original_emitter

    assert "semantic_summary=looks good" in rendered
    assert "publication_target: repo=https://github.com/example/repo.git base_branch=main head_branch=palimpsest/job/demo" in rendered
    assert "repo=https://github.com/example/repo.git" in rendered
    assert "base_branch=main" in rendered
    assert "git_ref=palimpsest/job/demo:deadbeef" in rendered

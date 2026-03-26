from pathlib import Path

from palimpsest.config import JobConfig
from palimpsest.runtime.contexts import resolve_context_functions
from palimpsest.runtime.roles import RoleManager, TeamManager

EVO_ROOT = Path(__file__).parent.parent / "evo"


def test_role_manager_loads_described_roles():
    role = RoleManager(EVO_ROOT)._load_role("planner")
    assert role.name == "planner"
    assert role.description


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

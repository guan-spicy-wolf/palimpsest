"""Tests for two-layer role resolution (ADR-0011 D2, D7).

Resolution rules:
1. If evo/teams/<team>/roles/<name>.py exists → use it
2. Else if evo/roles/<name>.py exists → use it
3. Else → raise FileNotFoundError
"""

from pathlib import Path

import pytest

from palimpsest.runtime.roles import RoleManager


@pytest.fixture
def evo_with_layers(tmp_path: Path) -> Path:
    """Create a two-layer evo structure for testing.

    Structure:
    evo/
      roles/
        worker.py          # Global role
        planner.py         # Global role
      teams/
        factorio/
          roles/
            worker.py      # Team-specific role (shadows global)
        alpha/
          roles/
            specialist.py  # Team-specific only
    """
    evo_root = tmp_path / "evo"
    evo_root.mkdir()

    # Global roles
    roles_dir = evo_root / "roles"
    roles_dir.mkdir()

    (roles_dir / "worker.py").write_text('''
from palimpsest.runtime.roles import role, JobSpec, context_spec

@role(name="worker", description="Global worker role")
def worker(**params):
    return JobSpec(
        preparation_fn=lambda: None,
        context_fn=context_spec("global worker", []),
        publication_fn=lambda **kw: None,
    )
''')

    (roles_dir / "planner.py").write_text('''
from palimpsest.runtime.roles import role, JobSpec, context_spec

@role(name="planner", description="Global planner role")
def planner(**params):
    return JobSpec(
        preparation_fn=lambda: None,
        context_fn=context_spec("global planner", []),
        publication_fn=lambda **kw: None,
    )
''')

    # Team-specific roles
    # Factorio team has worker.py that shadows global
    factorio_roles = evo_root / "teams" / "factorio" / "roles"
    factorio_roles.mkdir(parents=True)

    (factorio_roles / "worker.py").write_text('''
from palimpsest.runtime.roles import role, JobSpec, context_spec

@role(name="worker", description="Factorio-specific worker role")
def worker(**params):
    return JobSpec(
        preparation_fn=lambda: None,
        context_fn=context_spec("factorio worker", []),
        publication_fn=lambda **kw: None,
    )
''')

    # Alpha team has specialist.py that doesn't exist globally
    alpha_roles = evo_root / "teams" / "alpha" / "roles"
    alpha_roles.mkdir(parents=True)

    (alpha_roles / "specialist.py").write_text('''
from palimpsest.runtime.roles import role, JobSpec, context_spec

@role(name="specialist", description="Alpha team specialist role")
def specialist(**params):
    return JobSpec(
        preparation_fn=lambda: None,
        context_fn=context_spec("alpha specialist", []),
        publication_fn=lambda **kw: None,
    )
''')

    return evo_root


class TestTwoLayerRoleResolution:
    """Tests for two-layer role resolution with team parameter."""

    def test_global_role_visible_to_all_teams(self, evo_with_layers: Path):
        """Global role should be visible to teams that don't shadow it."""
        # Team "beta" doesn't have its own roles directory
        manager = RoleManager(evo_with_layers, team="beta")

        # Should see global planner
        meta = manager.get_definition("planner")
        assert meta is not None
        assert meta.name == "planner"
        assert meta.description == "Global planner role"

    def test_team_specific_role_shadows_global(self, evo_with_layers: Path):
        """Team-specific role should shadow the global role."""
        # Default team (no team dir) sees global worker
        default_manager = RoleManager(evo_with_layers)
        default_meta = default_manager.get_definition("worker")
        assert default_meta is not None
        assert default_meta.description == "Global worker role"

        # Factorio team sees its own worker
        factorio_manager = RoleManager(evo_with_layers, team="factorio")
        factorio_meta = factorio_manager.get_definition("worker")
        assert factorio_meta is not None
        assert factorio_meta.description == "Factorio-specific worker role"

    def test_team_specific_role_only_visible_to_that_team(self, evo_with_layers: Path):
        """Team-specific role should only be visible to that team."""
        # Alpha team has specialist
        alpha_manager = RoleManager(evo_with_layers, team="alpha")
        alpha_meta = alpha_manager.get_definition("specialist")
        assert alpha_meta is not None
        assert alpha_meta.description == "Alpha team specialist role"

        # Default team doesn't see alpha's specialist
        default_manager = RoleManager(evo_with_layers)
        default_meta = default_manager.get_definition("specialist")
        assert default_meta is None  # Not found in global roles

        # Factorio team also doesn't see alpha's specialist
        factorio_manager = RoleManager(evo_with_layers, team="factorio")
        factorio_meta = factorio_manager.get_definition("specialist")
        assert factorio_meta is None

    def test_missing_role_raises_error_on_resolve(self, evo_with_layers: Path):
        """Resolving a missing role should raise FileNotFoundError."""
        manager = RoleManager(evo_with_layers, team="alpha")

        with pytest.raises(FileNotFoundError) as exc_info:
            manager.resolve("nonexistent")

        assert "nonexistent" in str(exc_info.value)

    def test_resolve_uses_team_specific_role(self, evo_with_layers: Path):
        """resolve() should return JobSpec from team-specific role."""
        manager = RoleManager(evo_with_layers, team="factorio")
        spec = manager.resolve("worker")

        assert spec is not None
        assert spec.source_role == "worker"
        # The context_fn should return the factorio-specific context
        context = spec.context_fn(goal="test")
        assert context["system"] == "factorio worker"

    def test_resolve_falls_back_to_global(self, evo_with_layers: Path):
        """resolve() should fall back to global role when team doesn't have it."""
        manager = RoleManager(evo_with_layers, team="factorio")
        spec = manager.resolve("planner")

        assert spec is not None
        assert spec.source_role == "planner"
        context = spec.context_fn(goal="test")
        assert context["system"] == "global planner"

    def test_list_definitions_includes_team_roles(self, evo_with_layers: Path):
        """list_definitions() should include team-specific roles."""
        alpha_manager = RoleManager(evo_with_layers, team="alpha")
        roles = alpha_manager.list_definitions()

        role_names = [r.name for r in roles]
        # Should have global roles
        assert "planner" in role_names
        assert "worker" in role_names
        # Should also have team-specific role
        assert "specialist" in role_names

    def test_default_team_parameter(self, evo_with_layers: Path):
        """RoleManager should default to 'default' team."""
        manager = RoleManager(evo_with_layers)
        assert manager._team == "default"


class TestTeamRolesDirectory:
    """Tests for team roles directory handling."""

    def test_nonexistent_team_roles_dir_falls_back_to_global(self, tmp_path: Path):
        """When team has no roles dir, should fall back to global."""
        evo_root = tmp_path / "evo"
        evo_root.mkdir()
        roles_dir = evo_root / "roles"
        roles_dir.mkdir()

        (roles_dir / "worker.py").write_text('''
from palimpsest.runtime.roles import role, JobSpec, context_spec

@role(name="worker", description="Global worker role")
def worker(**params):
    return JobSpec(
        preparation_fn=lambda: None,
        context_fn=context_spec("global worker", []),
        publication_fn=lambda **kw: None,
    )
''')

        # Team "nonexistent" has no directory
        manager = RoleManager(evo_root, team="nonexistent")
        meta = manager.get_definition("worker")

        assert meta is not None
        assert meta.description == "Global worker role"

    def test_empty_team_roles_dir_falls_back_to_global(self, tmp_path: Path):
        """When team roles dir exists but is empty, should fall back to global."""
        evo_root = tmp_path / "evo"
        evo_root.mkdir()

        # Global roles
        roles_dir = evo_root / "roles"
        roles_dir.mkdir()
        (roles_dir / "worker.py").write_text('''
from palimpsest.runtime.roles import role, JobSpec, context_spec

@role(name="worker", description="Global worker role")
def worker(**params):
    return JobSpec(
        preparation_fn=lambda: None,
        context_fn=context_spec("global worker", []),
        publication_fn=lambda **kw: None,
    )
''')

        # Empty team roles dir
        team_roles_dir = evo_root / "teams" / "empty" / "roles"
        team_roles_dir.mkdir(parents=True)

        manager = RoleManager(evo_root, team="empty")
        meta = manager.get_definition("worker")

        assert meta is not None
        assert meta.description == "Global worker role"
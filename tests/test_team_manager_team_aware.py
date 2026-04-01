"""Tests for TeamManager and available_roles team-aware resolution (Issue #2).

Per ADR-0011:
- D2: RoleManager supports two-layer resolution (team-specific shadows global)
- D7: Team membership determined by directory location, not `teams` field
"""

from pathlib import Path

from palimpsest.config import JobConfig
from palimpsest.runtime.roles import RoleManager, TeamManager

# Use test fixtures with team-specific role structure
FIXTURES_EVO_ROOT = Path(__file__).parent / "fixtures" / "evo"


class TestRoleManagerTeamAware:
    """Tests for RoleManager two-layer resolution (ADR-0011 D2)."""

    def test_list_definitions_includes_team_specific_roles(self):
        """RoleManager with team="factorio" should include factorio-specific roles."""
        manager = RoleManager(FIXTURES_EVO_ROOT, team="factorio")
        roles = manager.list_definitions()
        role_names = [r.name for r in roles]

        # Factorio team has a worker role that shadows global
        assert "worker" in role_names

    def test_get_definition_returns_team_specific_worker(self):
        """Team-specific worker shadows global worker for factorio team."""
        manager = RoleManager(FIXTURES_EVO_ROOT, team="factorio")
        worker = manager.get_definition("worker")

        assert worker is not None
        # Factorio worker has specific description
        assert "Factorio" in worker.description or "RCON" in worker.description

    def test_get_definition_global_fallback_for_missing_role(self):
        """Global roles are available as fallback when team doesn't have them."""
        # Create a manager for a team without team-specific roles
        manager = RoleManager(FIXTURES_EVO_ROOT, team="nonexistent")
        # Global worker should still be available
        worker = manager.get_definition("worker")

        assert worker is not None
        # Global worker description
        assert "global" in worker.description.lower() or "general" in worker.description.lower()

    def test_resolve_team_specific_role(self):
        """Resolve loads and executes team-specific role module."""
        manager = RoleManager(FIXTURES_EVO_ROOT, team="factorio")
        spec = manager.resolve("worker")

        # Factorio worker has factorio_tool in its tools
        assert "factorio_tool" in spec.tools
        assert "bash" in spec.tools


class TestTeamManagerTeamAware:
    """Tests for TeamManager team parameter (Issue #2)."""

    def test_init_accepts_team_parameter(self):
        """TeamManager.__init__ accepts team parameter."""
        # This test verifies the interface exists
        manager = TeamManager(FIXTURES_EVO_ROOT, team="factorio")
        assert manager._team == "factorio"

    def test_init_defaults_team_to_default(self):
        """TeamManager.__init__ defaults team to 'default'."""
        manager = TeamManager(FIXTURES_EVO_ROOT)
        assert manager._team == "default"

    def test_resolve_uses_team_aware_role_manager(self):
        """TeamManager.resolve uses team-aware RoleManager internally."""
        # TeamManager with team="factorio" should see factorio roles
        manager = TeamManager(FIXTURES_EVO_ROOT, team="factorio")

        # The internal _roles should be a team-aware RoleManager
        all_roles = manager._roles.list_definitions()
        role_names = [r.name for r in all_roles]

        # Should include factorio's worker (which shadows global)
        assert "worker" in role_names

    def test_list_teams_uses_directory_based_membership(self):
        """TeamManager.list_teams should find teams from directory structure."""
        manager = TeamManager(FIXTURES_EVO_ROOT, team="factorio")

        # list_teams should discover "factorio" from teams/factorio/roles/
        teams = manager.list_teams()
        # factorio should be discoverable even without teams field
        assert "factorio" in teams


class TestAvailableRolesTeamAware:
    """Tests for available_roles context provider team awareness (Issue #2).

    Note: These tests require TeamManager to be team-aware.
    They will fail until TeamManager is fixed.
    """

    def test_available_roles_uses_team_from_job_config(self):
        """available_roles uses team parameter from JobConfig for RoleManager."""
        # Import here to avoid module import issues
        import importlib.util

        # Load the available_roles function directly from the file
        loaders_path = Path(__file__).parent.parent / "evo" / "contexts" / "loaders.py"
        spec = importlib.util.spec_from_file_location("loaders", loaders_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        available_roles_fn = module.available_roles

        # Create JobConfig with factorio team
        job_config = JobConfig(team="factorio")

        # available_roles should use team from job_config for role resolution
        rendered = available_roles_fn(
            evo_root=str(FIXTURES_EVO_ROOT),
            job_config=job_config,
        )

        # Should show factorio team
        assert "Team: factorio" in rendered

    def test_available_roles_shows_team_specific_worker(self):
        """available_roles shows team-specific worker (factorio) not global."""
        import importlib.util

        loaders_path = Path(__file__).parent.parent / "evo" / "contexts" / "loaders.py"
        spec = importlib.util.spec_from_file_location("loaders", loaders_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        available_roles_fn = module.available_roles

        job_config = JobConfig(team="factorio")
        rendered = available_roles_fn(
            evo_root=str(FIXTURES_EVO_ROOT),
            job_config=job_config,
        )

        # Should show Factorio-specific worker description, not global
        # Factorio worker mentions RCON or Factorio
        assert "Factorio" in rendered or "RCON" in rendered

    def test_available_roles_global_team_shows_global_worker(self):
        """available_roles for default team shows global worker."""
        import importlib.util

        loaders_path = Path(__file__).parent.parent / "evo" / "contexts" / "loaders.py"
        spec = importlib.util.spec_from_file_location("loaders", loaders_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        available_roles_fn = module.available_roles

        job_config = JobConfig(team="default")
        rendered = available_roles_fn(
            evo_root=str(FIXTURES_EVO_ROOT),
            job_config=job_config,
        )

        # For default team, should show global worker
        # Global worker description mentions "global" or "general"
        assert "global" in rendered.lower() or "general" in rendered.lower()
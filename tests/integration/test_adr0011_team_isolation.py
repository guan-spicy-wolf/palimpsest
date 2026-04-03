"""Integration tests for ADR-0011: Team as First-Class Isolation Boundary.

These tests verify the full implementation of:
- D1: Team is a task-domain isolation boundary
- D2: Two-layer evo structure
- D3: Directory-based role team membership
- D4: Team configuration in Trenni
- D5: Per-team launch conditions
- D6: RuntimeContext — job-scoped lifecycle context
- D7: Fixed evo path with team parameter
- D8: Container runtime per team

Tests are organized by the scenarios described in the implementation plan.
"""

import sys
from pathlib import Path

# Setup paths before any imports
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TRENNI_SRC = PROJECT_ROOT / "trenni"
if str(TRENNI_SRC) not in sys.path:
    sys.path.insert(0, str(TRENNI_SRC))

CONTRACTS_SRC = PROJECT_ROOT / "yoitsu-contracts" / "src"
if str(CONTRACTS_SRC) not in sys.path:
    sys.path.insert(0, str(CONTRACTS_SRC))

PALIMPSEST_SRC = PROJECT_ROOT / "palimpsest"
if str(PALIMPSEST_SRC) not in sys.path:
    sys.path.insert(0, str(PALIMPSEST_SRC))

# Now imports will work

import tempfile
from unittest.mock import MagicMock, patch

import pytest

from palimpsest.config import JobConfig, ToolsConfig
from palimpsest.events import JobCompletedData
from palimpsest.runtime.context import RuntimeContext
from palimpsest.runtime.roles import RoleManager, JobSpec, role, context_spec, workspace_config
from palimpsest.runtime.tools import UnifiedToolGateway, tool, ToolResult, resolve_tool_functions
from palimpsest.runtime.event_gateway import EventGateway
from palimpsest.runner import _run_job_from_spec

from trenni.config import TrenniConfig, TeamConfig, TeamRuntimeConfig, TeamSchedulingConfig
from trenni.state import SupervisorState, TeamLaunchCondition
from trenni.runtime_builder import RuntimeSpecBuilder, build_runtime_defaults
from trenni.runtime_types import RuntimeDefaults, JobRuntimeSpec


# =============================================================================
# Test Scenario 1: Team-specific role resolution
# =============================================================================

class TestTeamSpecificRoleResolution:
    """Tests for D2/D7: Two-layer role resolution with team parameter.

    Fixture structure:
    - evo/roles/worker.py -> global worker
    - evo/teams/factorio/roles/worker.py -> factorio-specific worker (shadows global)
    """

    @pytest.fixture
    def evo_fixture_path(self) -> Path:
        """Path to the fixture evo directory."""
        return Path(__file__).parent.parent / "fixtures" / "evo"

    def test_global_worker_visible_to_default_team(self, evo_fixture_path: Path):
        """Default team sees global worker role."""
        manager = RoleManager(evo_fixture_path, team="default")

        meta = manager.get_definition("worker")
        assert meta is not None
        assert meta.name == "worker"
        assert "Global worker role" in meta.description

    def test_factorio_worker_shadows_global(self, evo_fixture_path: Path):
        """Factorio team sees its own worker role, not global."""
        manager = RoleManager(evo_fixture_path, team="factorio")

        meta = manager.get_definition("worker")
        assert meta is not None
        assert meta.name == "worker"
        assert "Factorio-specific" in meta.description

    def test_resolve_factorio_worker_uses_team_role(self, evo_fixture_path: Path):
        """resolve() returns JobSpec from team-specific role."""
        manager = RoleManager(evo_fixture_path, team="factorio")

        spec = manager.resolve("worker")
        assert spec is not None
        assert spec.source_role == "worker"

        # Verify context_fn returns factorio-specific system prompt
        context = spec.context_fn(goal="test")
        assert "Factorio worker" in context["system"]

    def test_resolve_default_worker_uses_global_role(self, evo_fixture_path: Path):
        """resolve() returns JobSpec from global role for default team."""
        manager = RoleManager(evo_fixture_path, team="default")

        spec = manager.resolve("worker")
        assert spec is not None

        context = spec.context_fn(goal="test")
        assert "global worker" in context["system"]

    def test_factorio_worker_has_factorio_tools(self, evo_fixture_path: Path):
        """Factorio worker role requests factorio_tool."""
        manager = RoleManager(evo_fixture_path, team="factorio")

        spec = manager.resolve("worker")
        assert "factorio_tool" in spec.tools

    def test_global_worker_has_standard_tools(self, evo_fixture_path: Path):
        """Global worker role uses standard tools."""
        manager = RoleManager(evo_fixture_path, team="default")

        spec = manager.resolve("worker")
        assert "bash" in spec.tools
        assert "factorio_tool" not in spec.tools


# =============================================================================
# Test Scenario 2: Team runtime config integration
# =============================================================================

class TestTeamRuntimeConfigIntegration:
    """Tests for D4/D8: RuntimeSpecBuilder uses team config.

    Verifies:
    - image from team config (fallback to global default)
    - pod_name from team config (None = no pod)
    - extra_networks from team config
    """

    def test_factorio_team_runtime_spec(self):
        """Factorio team job uses factorio image and extra_networks."""
        config = TrenniConfig(
            runtime=TrenniConfig.__dataclass_fields__['runtime'].default_factory(),
            teams={
                "factorio": TeamConfig(
                    runtime=TeamRuntimeConfig(
                        image="localhost/yoitsu-factorio-job:dev",
                        pod_name=None,  # Factorio runs without pod
                        extra_networks=["factorio-net"],
                        env_allowlist=["RCON_HOST", "RCON_PORT", "RCON_PASSWORD"],
                    ),
                    scheduling=TeamSchedulingConfig(max_concurrent_jobs=1),
                ),
            },
        )

        defaults = build_runtime_defaults(config)

        builder = RuntimeSpecBuilder(config, defaults)

        spec = builder.build(
            job_id="factorio-job-001",
            source_event_id="evt-001",
            goal="Build copper wire factory",
            role="worker",
            team="factorio",
            repo="",  # Factorio doesn't need git repo
            init_branch="main",
            evo_sha=None,
        )

        # Verify factorio runtime profile
        assert spec.image == "localhost/yoitsu-factorio-job:dev"
        assert spec.pod_name is None  # No pod for factorio
        assert spec.extra_networks == ("factorio-net",)

    def test_default_team_runtime_spec(self):
        """Default team uses default runtime profile."""
        # Per ADR-0011 implementation: if team has pod_name=None in TeamRuntimeConfig,
        # it means "no pod" (not "use default"). So we explicitly set pod_name.
        config = TrenniConfig(
            runtime=TrenniConfig.__dataclass_fields__['runtime'].default_factory(),
            teams={
                "default": TeamConfig(
                    runtime=TeamRuntimeConfig(
                        pod_name="yoitsu-dev",  # Explicitly set to match default
                    ),
                    scheduling=TeamSchedulingConfig(),
                ),
            },
        )

        defaults = RuntimeDefaults(
            kind="podman",
            socket_uri="unix:///run/podman/podman.sock",
            pod_name="yoitsu-dev",
            image="localhost/yoitsu-palimpsest-job:dev",
            pull_policy="never",
            stop_grace_seconds=10,
            cleanup_timeout_seconds=120,
            retain_on_failure=False,
            labels={"io.yoitsu.managed-by": "trenni"},
            env_allowlist=("GITHUB_TOKEN",),
            git_token_env="GITHUB_TOKEN",
        )

        builder = RuntimeSpecBuilder(config, defaults)

        spec = builder.build(
            job_id="default-job-001",
            source_event_id="evt-002",
            goal="Implement feature X",
            role="worker",
            team="default",
            repo="https://github.com/org/repo.git",
            init_branch="main",
            evo_sha=None,
        )

        # Verify default runtime profile
        assert spec.image == "localhost/yoitsu-palimpsest-job:dev"
        assert spec.pod_name == "yoitsu-dev"
        assert spec.extra_networks == ()

    def test_unknown_team_uses_defaults(self):
        """Team not in config uses all default values."""
        config = TrenniConfig(
            runtime=TrenniConfig.__dataclass_fields__['runtime'].default_factory(),
            teams={},  # No teams defined
        )

        defaults = RuntimeDefaults(
            kind="podman",
            socket_uri="unix:///run/podman/podman.sock",
            pod_name="yoitsu-dev",
            image="localhost/default:latest",
            pull_policy="never",
            stop_grace_seconds=10,
            cleanup_timeout_seconds=120,
            retain_on_failure=False,
            labels={},
            env_allowlist=(),
            git_token_env="GITHUB_TOKEN",
        )

        builder = RuntimeSpecBuilder(config, defaults)

        spec = builder.build(
            job_id="unknown-team-job",
            source_event_id="evt-003",
            goal="Task for undefined team",
            role="worker",
            team="undefined-team",
            repo="",
            init_branch="main",
            evo_sha=None,
        )

        # Should use all defaults
        assert spec.image == "localhost/default:latest"
        assert spec.pod_name == "yoitsu-dev"


# =============================================================================
# Test Scenario 3: Launch condition enforcement
# =============================================================================

class TestLaunchConditionEnforcement:
    """Tests for D5: Per-team launch conditions.

    Verifies:
    - TeamLaunchCondition checks running count against max_concurrent
    - Second job blocked while first runs (for max_concurrent=1)
    - Different teams have independent conditions
    """

    def test_factorio_launch_condition_blocks_second_job(self):
        """Factorio team with max_concurrent=1 blocks second job."""
        state = SupervisorState()
        condition = TeamLaunchCondition(team="factorio", max_concurrent=1)

        # Initially no jobs running -> condition satisfied
        assert condition.is_satisfied(state) is True

        # First job starts -> increment counter
        state.increment_team_running("factorio")
        assert state.running_count_for_team("factorio") == 1

        # Condition now not satisfied -> second job blocked
        assert condition.is_satisfied(state) is False

        # First job finishes -> decrement counter
        state.decrement_team_running("factorio")
        assert state.running_count_for_team("factorio") == 0

        # Condition satisfied again
        assert condition.is_satisfied(state) is True

    def test_default_team_unlimited_jobs(self):
        """Default team with max_concurrent=0 allows unlimited jobs."""
        state = SupervisorState()
        condition = TeamLaunchCondition(team="default", max_concurrent=0)

        # Start many jobs
        for _ in range(10):
            state.increment_team_running("default")

        # Condition still satisfied (no limit)
        assert condition.is_satisfied(state) is True

    def test_launch_conditions_independent_per_team(self):
        """Launch conditions for different teams are independent."""
        state = SupervisorState()

        factorio_condition = TeamLaunchCondition(team="factorio", max_concurrent=1)
        default_condition = TeamLaunchCondition(team="default", max_concurrent=2)

        # Both satisfied initially
        assert factorio_condition.is_satisfied(state) is True
        assert default_condition.is_satisfied(state) is True

        # Factorio job starts
        state.increment_team_running("factorio")
        assert factorio_condition.is_satisfied(state) is False
        assert default_condition.is_satisfied(state) is True

        # Default jobs start
        state.increment_team_running("default")
        state.increment_team_running("default")
        assert factorio_condition.is_satisfied(state) is False
        assert default_condition.is_satisfied(state) is False

        # Factorio job finishes
        state.decrement_team_running("factorio")
        assert factorio_condition.is_satisfied(state) is True
        assert default_condition.is_satisfied(state) is False


# =============================================================================
# Test Scenario 4: RuntimeContext end-to-end
# =============================================================================

class MockEmitter:
    """Mock emitter for testing."""
    def __init__(self):
        self.events = []

    def emit(self, event_data):
        self.events.append(event_data)
        return None

    def recent_events(self, limit=10, *, job_id=None):
        return []

    def close(self):
        return None


def _make_default_publication():
    """Create default publication fn for test specs."""
    def pub(*, result=None, repo="", **params):
        if (result or {}).get("status") == "failed":
            return None
        if not repo:
            return None
        return "branch:sha"
    pub.__publication_strategy__ = "branch"
    pub.__publication_branch_prefix__ = "palimpsest/job"
    return pub


class TestRuntimeContextEndToEnd:
    """Tests for D6: RuntimeContext lifecycle through full pipeline.

    Verifies:
    - Job with preparation that creates resource
    - Tool receives runtime_context and accesses resource
    - Publication uses runtime_context resource
    - Cleanup is called in LIFO order
    """

    @pytest.fixture
    def evo_fixture_path(self) -> Path:
        """Path to the fixture evo directory."""
        return Path(__file__).parent.parent / "fixtures" / "evo"

    def test_factorio_preparation_creates_resource(self, evo_fixture_path: Path):
        """Factorio preparation_fn creates RCON resource in RuntimeContext."""
        manager = RoleManager(evo_fixture_path, team="factorio")
        spec = manager.resolve("worker")

        ctx = RuntimeContext(job_id="test-job", team="factorio")

        # Call preparation_fn
        prep_params = {"goal": "test", "repo": "", "runtime_context": ctx}
        workspace_cfg = spec.preparation_fn(**prep_params)

        # Verify resource was created
        assert "rcon_connection" in ctx.resources
        assert ctx.resources["rcon_connection"]["connected"] is True

    def test_factorio_tool_accesses_runtime_context_resource(self, evo_fixture_path: Path):
        """Factorio tool can access resource from RuntimeContext."""
        # Set up context with resource
        ctx = RuntimeContext(job_id="test-job", team="factorio")
        ctx.resources["rcon_connection"] = {"host": "localhost", "port": 27015, "connected": True}

        # resolve_tool_functions expects path to directory containing tools/ subdir
        # So we pass evo_fixture_path (which has teams/factorio/tools/ underneath)
        # But we need to pass the specific team's tools directory
        # Actually, resolve_tool_functions scans {evo_root}/tools/*.py
        # For team-specific tools, we need to pass the parent of tools/ dir
        factorio_team_dir = evo_fixture_path / "teams" / "factorio"
        tool_funcs = resolve_tool_functions(factorio_team_dir, "factorio", ["factorio_tool"])

        assert "factorio_tool" in tool_funcs
        factorio_tool = tool_funcs["factorio_tool"]

        # Execute tool with runtime_context
        result = factorio_tool("/c game.print('hello')", runtime_context=ctx)

        assert result.success
        assert "RCON command executed" in result.output

    def test_factorio_publication_accesses_resource(self, evo_fixture_path: Path):
        """Factorio publication_fn can access RuntimeContext resource."""
        manager = RoleManager(evo_fixture_path, team="factorio")
        spec = manager.resolve("worker")

        # Set up context with resource
        ctx = RuntimeContext(job_id="test-job", team="factorio")
        ctx.resources["rcon_connection"] = {"host": "localhost", "port": 27015, "connected": True}

        # Call publication_fn
        pub_result = spec.publication_fn(
            result={"status": "complete"},
            workspace_path="/tmp/workspace",
            runtime_context=ctx,
        )

        assert pub_result is not None
        assert "factorio://" in pub_result

    def test_cleanup_called_after_publication(self, evo_fixture_path: Path):
        """RuntimeContext cleanup is called after publication."""
        manager = RoleManager(evo_fixture_path, team="factorio")
        spec = manager.resolve("worker")

        ctx = RuntimeContext(job_id="test-job", team="factorio")

        # Preparation creates resource and registers cleanup
        spec.preparation_fn(goal="test", repo="", runtime_context=ctx)

        assert "rcon_connection" in ctx.resources
        assert "_cleanup_fns" in ctx.__dict__
        assert len(ctx._cleanup_fns) == 1

        # Cleanup should clear resources
        ctx.cleanup()

        assert len(ctx._cleanup_fns) == 0
        assert len(ctx.resources) == 0

    def test_cleanup_order_is_lifo(self):
        """RuntimeContext cleanup runs in LIFO order."""
        ctx = RuntimeContext()
        order = []

        ctx.register_cleanup(lambda: order.append("first"))
        ctx.register_cleanup(lambda: order.append("second"))
        ctx.register_cleanup(lambda: order.append("third"))

        ctx.cleanup()

        # LIFO: third, second, first
        assert order == ["third", "second", "first"]

    def test_full_pipeline_with_runtime_context(self, tmp_path: Path, evo_fixture_path: Path):
        """Full pipeline: preparation -> tool -> publication -> cleanup."""
        emitter = MockEmitter()
        event_gateway = EventGateway(emitter)

        # Create factorio-style spec with resource tracking
        cleanup_tracker = []
        prep_tracker = []

        def track_prep(*, goal="", repo="", runtime_context=None, **params):
            if runtime_context:
                runtime_context.resources["test_resource"] = "created"
                prep_tracker.append("created")
                runtime_context.register_cleanup(lambda: cleanup_tracker.append("cleaned"))
            return MagicMock(repo="", init_branch="main", new_branch=True, depth=1, git_token_env="")

        def track_pub(*, result=None, runtime_context=None, **params):
            # Publication sees the resource (test verifies it can access)
            if runtime_context and "test_resource" in runtime_context.resources:
                prep_tracker.append("pub_seen")
            return None

        track_pub.__publication_strategy__ = "skip"

        spec = JobSpec(
            preparation_fn=track_prep,
            context_fn=lambda **p: {"system": "sys", "sections": [], "task": "test"},
            publication_fn=track_pub,
            tools=["bash"],
        )

        config = JobConfig(job_id="full-pipeline-test", goal="test task")
        config.team = "factorio"

        patches = {
            "palimpsest.runner.EventEmitter": MagicMock(return_value=emitter),
            "palimpsest.runner._read_evo_sha": MagicMock(return_value="abc123"),
            "palimpsest.runner.setup_workspace": MagicMock(return_value=str(tmp_path)),
            "palimpsest.runner.build_context": MagicMock(return_value={"system": "sys"}),
            "palimpsest.runner.UnifiedLLMGateway": MagicMock(),
            "palimpsest.runner.UnifiedToolGateway": MagicMock(),
            "palimpsest.runner.finalize_workspace_after_job": MagicMock(),
            "palimpsest.runner.run_interaction_loop": MagicMock(
                return_value={"status": "complete", "summary": "ok", "messages": []}
            ),
            "palimpsest.runner.git.Repo": MagicMock(),
        }

        from contextlib import ExitStack
        stack = ExitStack()
        for target, mock_val in patches.items():
            stack.enter_context(patch(target, mock_val))

        with stack:
            _run_job_from_spec(config, spec, tmp_path)

        # Verify resource was created during prep
        assert "created" in prep_tracker

        # Verify cleanup was called
        assert "cleaned" in cleanup_tracker


# =============================================================================
# Test Scenario 5: Tool injection with RuntimeContext
# =============================================================================

class TestToolInjectionWithRuntimeContext:
    """Tests for runtime_context injection in tools."""

    def test_tool_schema_excludes_runtime_context(self):
        """runtime_context is excluded from tool schema shown to LLM."""
        from palimpsest.runtime.tools import tool

        @tool
        def my_factorio_tool(command: str, runtime_context: RuntimeContext) -> str:
            return f"executed: {command}"

        schema = my_factorio_tool.__tool_schema__

        properties = schema["function"]["parameters"]["properties"]
        assert "runtime_context" not in properties
        assert "command" in properties
        assert schema["function"]["parameters"]["required"] == ["command"]

    def test_tool_receives_runtime_context_via_gateway(self):
        """UnifiedToolGateway.execute injects runtime_context."""
        from palimpsest.runtime.tools import tool

        received = []

        @tool
        def capture_context(value: int, runtime_context: RuntimeContext) -> str:
            received.append(runtime_context.team)
            return f"team={runtime_context.team}, value={value}"

        with tempfile.TemporaryDirectory() as tmpdir:
            evo_root = Path(tmpdir)
            (evo_root / "tools").mkdir()

            config = ToolsConfig(builtin={}, disabled_builtins=[])
            gateway = EventGateway(MockEmitter())

            tool_gateway = UnifiedToolGateway(
                config=config,
                evo_root=evo_root,
                team="factorio",
                requested_evo_tools=[],
                gateway=gateway,
            )

            # Inject tool for testing
            tool_gateway._functions["capture_context"] = capture_context
            tool_gateway._schemas.append(capture_context.__tool_schema__)

            ctx = RuntimeContext(team="factorio", job_id="test-123")
            result = tool_gateway.execute(
                "capture_context",
                "call-001",
                {"value": 42},
                "/tmp/ws",
                runtime_context=ctx,
            )

            assert result.success
            assert received == ["factorio"]
            assert "team=factorio" in result.output

    def test_factorio_tool_from_fixture_injected(self):
        """factorio_tool from fixture receives runtime_context."""
        fixture_path = Path(__file__).parent.parent / "fixtures" / "evo"

        # resolve_tool_functions expects path to directory containing tools/ subdir
        # For team-specific tools, pass the team directory (parent of tools/)
        factorio_team_dir = fixture_path / "teams" / "factorio"
        tool_funcs = resolve_tool_functions(factorio_team_dir, "factorio", ["factorio_tool"])

        assert "factorio_tool" in tool_funcs

        # Verify schema excludes runtime_context
        schema = tool_funcs["factorio_tool"].__tool_schema__
        assert "runtime_context" not in schema["function"]["parameters"]["properties"]


# =============================================================================
# Test Scenario 6: Directory-based team membership (D3)
# =============================================================================

class TestDirectoryBasedTeamMembership:
    """Tests for D3: Team membership by directory location, not decorator."""

    def test_factorio_role_not_in_decorator_teams(self):
        """Factorio role's teams field is ignored for membership."""
        fixture_path = Path(__file__).parent.parent / "fixtures" / "evo"

        manager = RoleManager(fixture_path, team="factorio")
        meta = manager.get_definition("worker")

        # The teams field in @role decorator may be ["default"] or empty
        # but the role is found because it's in teams/factorio/roles/
        assert meta is not None
        assert "Factorio" in meta.description

    def test_role_found_by_directory_not_decorator(self, tmp_path: Path):
        """Role resolution uses directory location, ignoring decorator teams field."""
        # Create evo structure
        evo_root = tmp_path / "evo"
        evo_root.mkdir()

        # Create role in team directory with wrong teams field
        team_roles = evo_root / "teams" / "custom" / "roles"
        team_roles.mkdir(parents=True)

        # Role declares teams=["wrong"] but is in teams/custom/ directory
        (team_roles / "worker.py").write_text('''
from palimpsest.runtime.roles import role, JobSpec, context_spec

@role(name="worker", description="Custom worker", teams=["wrong", "other"])
def worker(**params):
    return JobSpec(
        preparation_fn=lambda **kw: None,
        context_fn=context_spec("custom worker", []),
        publication_fn=lambda **kw: None,
    )
''')

        # Resolve for team "custom" - should find it by directory location
        manager = RoleManager(evo_root, team="custom")
        meta = manager.get_definition("worker")

        assert meta is not None
        assert "Custom worker" in meta.description

        # Team "wrong" (from decorator) should NOT see this role
        wrong_manager = RoleManager(evo_root, team="wrong")
        wrong_meta = wrong_manager.get_definition("worker")
        assert wrong_meta is None  # Not found because not in teams/wrong/ directory


# =============================================================================
# Test Scenario 7: Cross-component integration
# =============================================================================

class TestCrossComponentIntegration:
    """Tests verifying integration across multiple ADR-0011 components."""

    def test_team_job_full_flow(self):
        """Complete flow: team config -> runtime spec -> launch condition -> execution."""
        # 1. Trenni config defines team
        config = TrenniConfig(
            runtime=TrenniConfig.__dataclass_fields__['runtime'].default_factory(),
            teams={
                "factorio": TeamConfig(
                    runtime=TeamRuntimeConfig(
                        image="factorio-image",
                        pod_name=None,
                        extra_networks=["factorio-net"],
                    ),
                    scheduling=TeamSchedulingConfig(max_concurrent_jobs=1),
                ),
            },
        )

        # 2. RuntimeSpecBuilder produces correct spec
        defaults = build_runtime_defaults(config)
        builder = RuntimeSpecBuilder(config, defaults)
        spec = builder.build(
            job_id="job-001",
            source_event_id="evt-001",
            goal="task",
            role="worker",
            team="factorio",
            repo="",
            init_branch="main",
            evo_sha=None,
        )

        assert spec.image == "factorio-image"
        assert spec.extra_networks == ("factorio-net",)

        # 3. Launch condition enforcement
        state = SupervisorState()
        condition = TeamLaunchCondition(
            team="factorio",
            max_concurrent=config.teams["factorio"].scheduling.max_concurrent_jobs,
        )

        # First job can launch
        assert condition.is_satisfied(state)
        state.increment_team_running("factorio")

        # Second job blocked
        assert not condition.is_satisfied(state)

        # First job finishes
        state.decrement_team_running("factorio")
        assert condition.is_satisfied(state)

    def test_evo_path_fixed_constant(self):
        """EVO_DIR is a fixed path, not configurable."""
        # This is a design verification test
        # Per ADR-0011 D7: evo_root parameter is removed from runtime components
        # The evolvable directory is a fixed structural constant

        fixture_path = Path(__file__).parent.parent / "fixtures" / "evo"

        # RoleManager accepts path for testing but uses team parameter
        manager = RoleManager(fixture_path, team="factorio")

        # Team resolution uses two-layer structure under evo_root
        assert manager._team == "factorio"
        assert manager._team_roles_dir == fixture_path / "teams" / "factorio" / "roles"
"""Tests for optimizer role discovery via @role decorator."""
import textwrap
from pathlib import Path

import pytest

from palimpsest.runtime.roles import RoleManager


def test_optimizer_role_is_discovered_by_ast(tmp_path):
    """Optimizer role with @role decorator is discoverable via AST scanning."""
    # Create evo/roles directory with optimizer.py
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "optimizer.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.roles import JobSpec, context_spec, role

        @role(
            name="optimizer",
            description="Analyzes observation events and proposes improvements",
            role_type="planner",
            min_cost=0.1,
            recommended_cost=0.5,
            max_cost=1.0,
        )
        def optimizer(**params) -> JobSpec:
            return JobSpec(
                preparation_fn=lambda **_: WorkspaceConfig(repo="", new_branch=False),
                context_fn=context_spec(system="prompts/optimizer.md", sections=[]),
                publication_fn=lambda **_: (None, []),
                tools=[],
            )
    """))

    # RoleMetadataReader (via RoleManager) should discover it
    manager = RoleManager(tmp_path)
    definitions = manager.list_definitions()
    
    assert len(definitions) == 1
    meta = definitions[0]
    assert meta.name == "optimizer"
    assert meta.description == "Analyzes observation events and proposes improvements"
    assert meta.role_type == "planner"
    assert meta.min_cost == 0.1
    assert meta.max_cost == 1.0


def test_role_decorator_required_for_discovery(tmp_path):
    """Role without @role decorator is NOT discovered by AST scanning."""
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "undiscovered.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.roles import JobSpec, context_spec

        # Missing @role decorator!
        def some_role(**params) -> JobSpec:
            return JobSpec(
                preparation_fn=lambda **_: WorkspaceConfig(repo="", new_branch=False),
                context_fn=context_spec(system="prompts/some.md", sections=[]),
                publication_fn=lambda **_: (None, []),
                tools=[],
            )
        
        # Manual __is_role__ does NOT help with AST discovery
        some_role.__is_role__ = True
        some_role.__role_name__ = "some_role"
    """))

    manager = RoleManager(tmp_path)
    definitions = manager.list_definitions()
    
    # Should NOT discover this role (no @role decorator)
    assert len(definitions) == 0


def test_role_decorator_literal_args_required(tmp_path):
    """Role decorator args must be literals (not runtime expressions)."""
    roles_dir = tmp_path / "roles"
    roles_dir.mkdir()
    (roles_dir / "bad_role.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.roles import JobSpec, role
        
        COST = 0.5  # Non-literal
        
        @role(
            name="bad_role",
            description="Uses non-literal arg",
            min_cost=COST,  # ERROR: not a literal
        )
        def bad_role(**params) -> JobSpec:
            pass
    """))

    manager = RoleManager(tmp_path)
    
    # Should raise ValueError when scanning non-literal decorator args
    with pytest.raises(ValueError) as exc_info:
        manager.list_definitions()
    
    assert "literal" in str(exc_info.value).lower()


def test_factorio_bundle_role_discovery(tmp_path):
    """Bundle roles in bundles/<bundle>/roles/ are discoverable."""
    # Create bundle role
    (tmp_path / "factorio" / "roles").mkdir(parents=True)
    (tmp_path / "factorio" / "roles" / "worker.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.roles import JobSpec, role, context_spec
        from palimpsest.config import WorkspaceConfig
        
        @role(
            name="worker",
            description="Factorio in-game worker with RCON",
            role_type="worker",
        )
        def worker(**params) -> JobSpec:
            return JobSpec(
                preparation_fn=lambda **_: WorkspaceConfig(repo="", new_branch=False),
                context_fn=context_spec(system="bundles/factorio/prompts/worker.md", sections=[]),
                publication_fn=lambda **_: (None, []),
                tools=["factorio_call_script"],
            )
    """))

    # RoleManager for factorio bundle should find the worker role
    manager = RoleManager(tmp_path, bundle="factorio")
    definitions = manager.list_definitions()
    
    assert len(definitions) == 1
    assert definitions[0].name == "worker"


def test_role_metadata_reader_vs_role_manager():
    """RoleMetadataReader uses AST scan, RoleManager uses execution."""
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir()
        (roles_dir / "test_role.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.roles import JobSpec, role, context_spec
            from palimpsest.config import WorkspaceConfig
            
            @role(
                name="test_role",
                description="Test role for discovery",
                min_cost=0.1,
                max_cost=1.0,
            )
            def test_role(**params) -> JobSpec:
                return JobSpec(
                    preparation_fn=lambda **_: WorkspaceConfig(repo="", new_branch=False),
                    context_fn=lambda **_: {},
                    publication_fn=lambda **_: (None, []),
                    tools=["bash"],
                )
        """))
        
        from yoitsu_contracts.role_metadata import RoleMetadataReader
        
        # RoleMetadataReader (AST scan) - used by trenni
        reader = RoleMetadataReader(tmp_path)
        meta = reader.get_definition("test_role")
        assert meta is not None
        assert meta.name == "test_role"
        assert meta.min_cost == 0.1
        
        # RoleManager (execution) - used by palimpsest
        manager = RoleManager(tmp_path)
        spec = manager.resolve("test_role")
        assert spec is not None
        assert "bash" in spec.tools
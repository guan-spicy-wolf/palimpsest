"""Tests for role resolution with bundle-only semantics (Bundle MVP).

Per Bundle MVP: Roles are loaded from evo/<bundle>/roles/ only.
No global fallback, no team layer.
"""
from types import SimpleNamespace
from pathlib import Path

from palimpsest.config import JobConfig
from palimpsest.runtime.contexts import resolve_context_functions
from palimpsest.runtime.roles import RoleManager, role, RoleMetadata

# Use the test fixture bundle
FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "evo"


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


def test_role_manager_requires_bundle():
    """RoleManager requires a bundle parameter to find roles."""
    manager = RoleManager(FIXTURES_ROOT, bundle="factorio")
    meta = manager.get_definition("worker")
    assert meta is not None
    assert meta.name == "worker"


def test_role_manager_empty_bundle_returns_none():
    """RoleManager with empty bundle returns None for get_definition."""
    manager = RoleManager(FIXTURES_ROOT, bundle="")
    meta = manager.get_definition("worker")
    assert meta is None


def test_role_manager_nonexistent_bundle_returns_none():
    """RoleManager with nonexistent bundle returns None for get_definition."""
    manager = RoleManager(FIXTURES_ROOT, bundle="nonexistent")
    meta = manager.get_definition("worker")
    assert meta is None


def test_role_manager_resolve_returns_spec():
    """RoleManager.resolve returns a JobSpec for a valid role."""
    spec = RoleManager(FIXTURES_ROOT, bundle="factorio").resolve("worker")
    assert spec is not None
    assert "bash" in spec.tools
    assert "factorio_tool" in spec.tools


def test_role_manager_resolve_missing_role_raises():
    """RoleManager.resolve raises for missing role."""
    manager = RoleManager(FIXTURES_ROOT, bundle="factorio")
    try:
        spec = manager.resolve("nonexistent_role")
        assert False, "Should have raised"
    except FileNotFoundError:
        pass


def test_worker_role_has_factorio_tool():
    """Worker role in factorio bundle has factorio_tool."""
    manager = RoleManager(FIXTURES_ROOT, bundle="factorio")
    meta = manager.get_definition("worker")
    assert meta is not None
    spec = manager.resolve("worker")
    assert "factorio_tool" in spec.tools


def test_available_roles_context_empty_bundle():
    """available_roles context provider returns empty for empty bundle."""
    funcs = resolve_context_functions(FIXTURES_ROOT, ["available_roles"], bundle="")
    # Should return empty registry since bundle is empty
    assert funcs == {}


def test_role_metadata_has_required_fields():
    """Role metadata includes name, description, and optional max_cost."""
    @role(name="test_role", description="A test role", max_cost=3.0)
    def my_role(**params):
        pass

    assert my_role.__role_metadata__.name == "test_role"
    assert my_role.__role_metadata__.description == "A test role"
    assert my_role.__role_metadata__.max_cost == 3.0


def test_role_list_roles_returns_bundle_roles():
    """RoleManager.list_roles returns roles for the specified bundle."""
    manager = RoleManager(FIXTURES_ROOT, bundle="factorio")
    roles = manager.list_roles()
    assert "worker" in roles


def test_role_list_roles_empty_bundle_returns_empty():
    """RoleManager.list_roles returns empty list for empty bundle."""
    manager = RoleManager(FIXTURES_ROOT, bundle="")
    roles = manager.list_roles()
    assert roles == []
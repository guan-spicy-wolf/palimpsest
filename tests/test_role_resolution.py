"""Tests for bundle-based role resolution (Bundle MVP).

Resolution rules:
1. If evo/<bundle>/roles/<name>.py exists → use it
2. Else → raise FileNotFoundError

No global fallback, no team layer.
"""

from pathlib import Path

import pytest

from palimpsest.runtime.roles import RoleManager


@pytest.fixture
def evo_with_bundle(tmp_path: Path) -> Path:
    """Create a bundle-based evo structure for testing.

    Structure:
    evo/
      factorio/
        roles/
          worker.py      # Factorio bundle role
    """
    evo_root = tmp_path / "evo"
    evo_root.mkdir()

    # Factorio bundle roles
    factorio_roles = evo_root / "factorio" / "roles"
    factorio_roles.mkdir(parents=True)

    (factorio_roles / "worker.py").write_text('''
from palimpsest.runtime.roles import role, JobSpec, context_spec

@role(name="worker", description="Factorio worker role")
def worker(**params):
    return JobSpec(
        preparation_fn=lambda: None,
        context_fn=context_spec("factorio worker", []),
        publication_fn=lambda **kw: None,
    )
''')

    return evo_root


class TestBundleRoleResolution:
    """Tests for bundle-only role resolution."""

    def test_bundle_role_is_discovered(self, evo_with_bundle: Path):
        """Bundle role should be discoverable."""
        manager = RoleManager(evo_with_bundle, bundle="factorio")
        meta = manager.get_definition("worker")
        
        assert meta is not None
        assert meta.name == "worker"
        assert meta.description == "Factorio worker role"

    def test_missing_role_returns_none(self, evo_with_bundle: Path):
        """Missing role should return None from get_definition."""
        manager = RoleManager(evo_with_bundle, bundle="factorio")
        meta = manager.get_definition("nonexistent")
        
        assert meta is None

    def test_missing_role_raises_on_resolve(self, evo_with_bundle: Path):
        """Resolving a missing role should raise FileNotFoundError."""
        manager = RoleManager(evo_with_bundle, bundle="factorio")
        
        with pytest.raises(FileNotFoundError) as exc_info:
            manager.resolve("nonexistent")
        
        assert "nonexistent" in str(exc_info.value)

    def test_resolve_returns_jobspec(self, evo_with_bundle: Path):
        """resolve() should return JobSpec from bundle role."""
        manager = RoleManager(evo_with_bundle, bundle="factorio")
        spec = manager.resolve("worker")
        
        assert spec is not None
        assert spec.source_role == "worker"
        context = spec.context_fn(goal="test")
        assert context["system"] == "factorio worker"

    def test_list_definitions_shows_bundle_roles(self, evo_with_bundle: Path):
        """list_definitions() should show bundle roles."""
        manager = RoleManager(evo_with_bundle, bundle="factorio")
        roles = manager.list_definitions()
        
        role_names = [r.name for r in roles]
        assert "worker" in role_names

    def test_empty_bundle_returns_empty_list(self, tmp_path: Path):
        """Bundle with no roles should return empty list."""
        evo_root = tmp_path / "evo"
        evo_root.mkdir()
        (evo_root / "empty" / "roles").mkdir(parents=True)
        
        manager = RoleManager(evo_root, bundle="empty")
        roles = manager.list_definitions()
        
        assert roles == []

    def test_no_bundle_parameter_returns_empty(self, evo_with_bundle: Path):
        """RoleManager without bundle parameter returns empty results."""
        manager = RoleManager(evo_with_bundle)
        
        # No bundle specified, no roles found
        roles = manager.list_definitions()
        assert roles == []
        
        meta = manager.get_definition("worker")
        assert meta is None
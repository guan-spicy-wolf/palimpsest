"""Tests for bundle workspace role resolution (ADR-0015).

Resolution rules:
1. If bundle_workspace/roles/<name>.py exists → use it
2. Else → raise FileNotFoundError

No global fallback, no team layer.
"""

from pathlib import Path

import pytest

from palimpsest.runtime.roles import RoleManager


@pytest.fixture
def bundle_workspace_with_roles(tmp_path: Path) -> Path:
    """Create a bundle workspace structure for testing.

    Per ADR-0015: bundle_workspace is bundle repo root.
    Structure:
    bundle_workspace/
      roles/
        worker.py      # Bundle role
    """
    bundle_workspace = tmp_path / "bundle"
    bundle_workspace.mkdir()

    # Roles directly in bundle_workspace/roles/
    roles_dir = bundle_workspace / "roles"
    roles_dir.mkdir(parents=True)

    (roles_dir / "worker.py").write_text('''
from palimpsest.runtime.roles import role, JobSpec, context_spec

@role(name="worker", description="Factorio worker role")
def worker(**params):
    return JobSpec(
        preparation_fn=lambda: None,
        context_fn=context_spec("factorio worker", []),
        publication_fn=lambda **kw: None,
    )
''')

    return bundle_workspace


class TestBundleRoleResolution:
    """Tests for bundle workspace role resolution."""

    def test_bundle_role_is_discovered(self, bundle_workspace_with_roles: Path):
        """Bundle role should be discoverable."""
        manager = RoleManager(bundle_workspace_with_roles)
        meta = manager.get_definition("worker")

        assert meta is not None
        assert meta.name == "worker"
        assert meta.description == "Factorio worker role"

    def test_missing_role_returns_none(self, bundle_workspace_with_roles: Path):
        """Missing role should return None from get_definition."""
        manager = RoleManager(bundle_workspace_with_roles)
        meta = manager.get_definition("nonexistent")

        assert meta is None

    def test_missing_role_raises_on_resolve(self, bundle_workspace_with_roles: Path):
        """Resolving a missing role should raise FileNotFoundError."""
        manager = RoleManager(bundle_workspace_with_roles)

        with pytest.raises(FileNotFoundError) as exc_info:
            manager.resolve("nonexistent")

        assert "nonexistent" in str(exc_info.value)

    def test_resolve_returns_jobspec(self, bundle_workspace_with_roles: Path):
        """resolve() should return JobSpec from bundle role."""
        manager = RoleManager(bundle_workspace_with_roles)
        spec = manager.resolve("worker")

        assert spec is not None
        assert spec.source_role == "worker"
        context = spec.context_fn(goal="test")
        assert context["system"] == "factorio worker"

    def test_list_definitions_shows_bundle_roles(self, bundle_workspace_with_roles: Path):
        """list_definitions() should show bundle roles."""
        manager = RoleManager(bundle_workspace_with_roles)
        roles = manager.list_definitions()

        role_names = [r.name for r in roles]
        assert "worker" in role_names

    def test_empty_workspace_returns_empty_list(self, tmp_path: Path):
        """Bundle workspace with no roles should return empty list."""
        bundle_workspace = tmp_path / "bundle"
        bundle_workspace.mkdir()
        # No roles directory

        manager = RoleManager(bundle_workspace)
        roles = manager.list_definitions()

        assert roles == []

    def test_no_roles_returns_empty(self, bundle_workspace_with_roles: Path):
        """RoleManager with no roles returns empty results."""
        empty_workspace = bundle_workspace_with_roles.parent / "empty"
        empty_workspace.mkdir()

        manager = RoleManager(empty_workspace)

        # No roles found
        roles = manager.list_definitions()
        assert roles == []

        meta = manager.get_definition("worker")
        assert meta is None
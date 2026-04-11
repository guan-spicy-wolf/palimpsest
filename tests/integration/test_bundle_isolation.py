"""Integration tests for bundle workspace role resolution (ADR-0015).

These tests verify the bundle workspace role resolution.
"""

import sys
from pathlib import Path

# Setup paths before any imports
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PALIMPSEST_SRC = PROJECT_ROOT / "palimpsest"
if str(PALIMPSEST_SRC) not in sys.path:
    sys.path.insert(0, str(PALIMPSEST_SRC))

import pytest

from palimpsest.runtime.roles import RoleManager


class TestBundleRoleResolution:
    """Tests for bundle workspace role resolution."""

    @pytest.fixture
    def bundle_fixture_path(self) -> Path:
        """Path to the fixture bundle workspace directory (factorio bundle root)."""
        # Per ADR-0015: bundle_workspace is bundle repo root (contains roles/ directly)
        return Path(__file__).parent.parent / "fixtures" / "evo" / "factorio"

    def test_factorio_worker_is_discovered(self, bundle_fixture_path: Path):
        """Factorio bundle worker role is discoverable."""
        manager = RoleManager(bundle_fixture_path)

        meta = manager.get_definition("worker")
        assert meta is not None
        assert meta.name == "worker"
        assert "Factorio-specific" in meta.description

    def test_resolve_factorio_worker_returns_jobspec(self, bundle_fixture_path: Path):
        """resolve() returns JobSpec from bundle role."""
        manager = RoleManager(bundle_fixture_path)

        spec = manager.resolve("worker")
        assert spec is not None
        assert spec.source_role == "worker"

    def test_missing_role_returns_none(self, bundle_fixture_path: Path):
        """Missing role returns None from get_definition."""
        manager = RoleManager(bundle_fixture_path)
        
        meta = manager.get_definition("nonexistent")
        assert meta is None

    def test_missing_role_raises_on_resolve(self, bundle_fixture_path: Path):
        """Resolving a missing role raises FileNotFoundError."""
        manager = RoleManager(bundle_fixture_path)
        
        with pytest.raises(FileNotFoundError):
            manager.resolve("nonexistent")

    def test_no_roles_returns_empty(self, bundle_fixture_path: Path):
        """RoleManager with no roles directory returns empty results."""
        # Use parent directory which has no roles/
        empty_path = bundle_fixture_path.parent
        manager = RoleManager(empty_path)
        
        roles = manager.list_definitions()
        assert roles == []
        
        meta = manager.get_definition("worker")
        assert meta is None

    def test_empty_roles_returns_empty(self, tmp_path: Path):
        """Bundle workspace with empty roles directory returns empty list."""
        roles_dir = tmp_path / "roles"
        roles_dir.mkdir(parents=True)
        # No .py files
        
        manager = RoleManager(tmp_path)
        
        roles = manager.list_definitions()
        assert roles == []
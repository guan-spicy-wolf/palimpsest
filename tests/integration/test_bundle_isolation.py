"""Integration tests for Bundle MVP: bundle-based role resolution.

These tests verify the bundle-only role resolution without global fallback.
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
    """Tests for bundle-only role resolution."""

    @pytest.fixture
    def evo_fixture_path(self) -> Path:
        """Path to the fixture evo directory with factorio bundle."""
        return Path(__file__).parent.parent / "fixtures" / "evo"

    def test_factorio_worker_is_discovered(self, evo_fixture_path: Path):
        """Factorio bundle worker role is discoverable."""
        manager = RoleManager(evo_fixture_path, bundle="factorio")

        meta = manager.get_definition("worker")
        assert meta is not None
        assert meta.name == "worker"
        assert "Factorio-specific" in meta.description

    def test_resolve_factorio_worker_returns_jobspec(self, evo_fixture_path: Path):
        """resolve() returns JobSpec from bundle role."""
        manager = RoleManager(evo_fixture_path, bundle="factorio")

        spec = manager.resolve("worker")
        assert spec is not None
        assert spec.source_role == "worker"

    def test_missing_role_returns_none(self, evo_fixture_path: Path):
        """Missing role returns None from get_definition."""
        manager = RoleManager(evo_fixture_path, bundle="factorio")
        
        meta = manager.get_definition("nonexistent")
        assert meta is None

    def test_missing_role_raises_on_resolve(self, evo_fixture_path: Path):
        """Resolving a missing role raises FileNotFoundError."""
        manager = RoleManager(evo_fixture_path, bundle="factorio")
        
        with pytest.raises(FileNotFoundError):
            manager.resolve("nonexistent")

    def test_no_bundle_returns_empty(self, evo_fixture_path: Path):
        """RoleManager without bundle returns empty results."""
        manager = RoleManager(evo_fixture_path)
        
        roles = manager.list_definitions()
        assert roles == []
        
        meta = manager.get_definition("worker")
        assert meta is None

    def test_nonexistent_bundle_returns_empty(self, evo_fixture_path: Path):
        """Nonexistent bundle returns empty role list."""
        manager = RoleManager(evo_fixture_path, bundle="nonexistent")
        
        roles = manager.list_definitions()
        assert roles == []
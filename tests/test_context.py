"""Tests for RuntimeContext."""

from palimpsest.runtime.context import RuntimeContext


def test_runtime_context_has_bundle_field():
    """RuntimeContext accepts bundle parameter."""
    ctx = RuntimeContext(bundle="factorio")
    assert ctx.bundle == "factorio"


def test_runtime_context_bundle_defaults_empty():
    """RuntimeContext bundle defaults to empty string (must be explicit)."""
    ctx = RuntimeContext()
    assert ctx.bundle == ""
"""Tests for RuntimeContext."""

from palimpsest.runtime.context import RuntimeContext


def test_runtime_context_has_team_field():
    """RuntimeContext accepts team parameter."""
    ctx = RuntimeContext(team="factorio")
    assert ctx.team == "factorio"


def test_runtime_context_team_defaults_to_default():
    """RuntimeContext team defaults to 'default'."""
    ctx = RuntimeContext()
    assert ctx.team == "default"
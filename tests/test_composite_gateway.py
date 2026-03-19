from unittest.mock import MagicMock
from palimpsest.gateway.tools import (
    CompositeToolGateway,
    ToolResult,
    find_duplicate_tool_names,
)


def _make_gateway(names: list[str]) -> MagicMock:
    gw = MagicMock()
    gw.schema.return_value = [
        {"type": "function", "function": {"name": n}} for n in names
    ]
    gw.execute.return_value = ToolResult(success=True, output="ok")
    return gw


def test_composite_dispatches_to_correct_gateway():
    gw_a = _make_gateway(["a"])
    gw_b = _make_gateway(["b"])
    composite = CompositeToolGateway([gw_a, gw_b])

    composite.execute("b", "call-1", {}, "/tmp")
    gw_b.execute.assert_called_once()
    gw_a.execute.assert_not_called()


def test_composite_schema_merges_all():
    gw_a = _make_gateway(["a"])
    gw_b = _make_gateway(["b", "c"])
    composite = CompositeToolGateway([gw_a, gw_b])

    names = [s["function"]["name"] for s in composite.schema()]
    assert names == ["a", "b", "c"]


def test_composite_unknown_tool():
    composite = CompositeToolGateway([_make_gateway(["a"])])
    result = composite.execute("nonexistent", "x", {}, "/tmp")
    assert not result.success


def test_duplicate_tool_names_are_detected():
    gw_a = _make_gateway(["a"])
    gw_b = _make_gateway(["a"])
    assert find_duplicate_tool_names([gw_a, gw_b]) == ["a"]

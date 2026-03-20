from unittest.mock import MagicMock
from palimpsest.runtime.tools import (
    UnifiedToolGateway,
    ToolResult,
    find_duplicate_tool_names,
)
from palimpsest.runtime.interfaces import ToolProvider, ToolSpec


class FakeProvider(ToolProvider):
    def __init__(self, name: str):
        self._name = name

    def tools(self):
        return [ToolSpec(name=self._name, description="fake", parameters={})]

    def execute(self, name, args, workspace):
        return ToolResult(success=True, output=f"ok from {name}")


def test_unified_dispatches_to_correct_provider():
    pa = FakeProvider("a")
    pb = FakeProvider("b")
    gw = UnifiedToolGateway({"a": pa, "b": pb}, MagicMock())

    result = gw.execute("b", "call-1", {}, "/tmp")
    assert result.success
    assert "ok from b" in result.output


def test_unified_schema_merges_all():
    pa = FakeProvider("a")
    pb = FakeProvider("b")
    gw = UnifiedToolGateway({"a": pa, "b": pb}, MagicMock())

    names = [s["function"]["name"] for s in gw.schema()]
    assert names == ["a", "b"]


def test_unified_unknown_tool():
    pa = FakeProvider("a")
    gw = UnifiedToolGateway({"a": pa}, MagicMock())
    result = gw.execute("nonexistent", "x", {}, "/tmp")
    assert not result.success


def test_duplicate_tool_names_are_detected():
    pa = FakeProvider("a")
    pb = FakeProvider("a")
    assert find_duplicate_tool_names({"a": pa}, {"a": pb}) == ["a"]


def test_no_duplicates_when_disjoint():
    pa = FakeProvider("a")
    pb = FakeProvider("b")
    assert find_duplicate_tool_names({"a": pa}, {"b": pb}) == []

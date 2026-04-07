import textwrap
from pathlib import Path
from unittest.mock import MagicMock

from palimpsest.runtime.tools import (
    UnifiedToolGateway,
    ToolResult,
    find_duplicate_tool_names,
    resolve_tool_functions,
)
from palimpsest.config import ToolsConfig


def _make_evo(tmp_path, tools: dict[str, str]):
    """Helper: create evo tool files from {name: body} dict."""
    tools_dir = tmp_path / "default" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    for name, body in tools.items():
        (tools_dir / f"{name}.py").write_text(textwrap.dedent(body))


def test_unified_dispatches_to_correct_tool(tmp_path):
    _make_evo(tmp_path, {
        "ab": """\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def a() -> ToolResult:
                \"\"\"Tool a.\"\"\"
                return ToolResult(success=True, output="ok from a")

            @tool
            def b() -> ToolResult:
                \"\"\"Tool b.\"\"\"
                return ToolResult(success=True, output="ok from b")
        """,
    })
    config = ToolsConfig(disabled_builtins=["bash", "spawn"])
    gw = UnifiedToolGateway(config, tmp_path, "default", ["a", "b"], MagicMock())

    result = gw.execute("b", "call-1", {}, "/tmp")
    assert result.success
    assert "ok from b" in result.output


def test_unified_schema_merges_all(tmp_path):
    _make_evo(tmp_path, {
        "ab": """\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def a() -> ToolResult:
                \"\"\"Tool a.\"\"\"
                return ToolResult(success=True, output="a")

            @tool
            def b() -> ToolResult:
                \"\"\"Tool b.\"\"\"
                return ToolResult(success=True, output="b")
        """,
    })
    config = ToolsConfig(disabled_builtins=["bash", "spawn"])
    gw = UnifiedToolGateway(config, tmp_path, "default", ["a", "b"], MagicMock())

    names = [s["function"]["name"] for s in gw.schema()]
    assert "a" in names
    assert "b" in names


def test_unified_unknown_tool(tmp_path):
    _make_evo(tmp_path, {
        "a": """\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def a() -> ToolResult:
                \"\"\"Tool a.\"\"\"
                return ToolResult(success=True, output="a")
        """,
    })
    config = ToolsConfig(disabled_builtins=["bash", "spawn"])
    gw = UnifiedToolGateway(config, tmp_path, "default", ["a"], MagicMock())
    result = gw.execute("nonexistent", "x", {}, "/tmp")
    assert not result.success


def test_duplicate_tool_names_are_detected():
    assert find_duplicate_tool_names({"a": lambda: None}, {"a": lambda: None}) == ["a"]


def test_no_duplicates_when_disjoint():
    assert find_duplicate_tool_names({"a": lambda: None}, {"b": lambda: None}) == []

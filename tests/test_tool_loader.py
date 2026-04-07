import textwrap
from pathlib import Path
from unittest.mock import MagicMock

from palimpsest.runtime.tools import (
    UnifiedToolGateway,
    ToolResult,
    resolve_tool_functions,
)
from palimpsest.config import ToolsConfig


def test_resolve_tool_functions_discovers_decorated(tmp_path):
    tools_dir = tmp_path / "default" / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "echo.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.tools import tool, ToolResult

        @tool
        def echo(msg: str) -> ToolResult:
            \"\"\"Echo back the message.\"\"\"
            return ToolResult(success=True, output=msg)
    """))
    funcs = resolve_tool_functions(tmp_path, "default", ["echo"])
    assert "echo" in funcs
    result = funcs["echo"](msg="hello")
    assert result.output == "hello"


def test_unified_gateway_with_evo_tools(tmp_path):
    tools_dir = tmp_path / "default" / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "echo.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.tools import tool, ToolResult

        @tool
        def echo(msg: str) -> ToolResult:
            \"\"\"Echo back.\"\"\"
            return ToolResult(success=True, output="ok")
    """))

    config = ToolsConfig(disabled_builtins=["bash", "spawn"])
    gateway = MagicMock()
    gw = UnifiedToolGateway(config, tmp_path, "default", ["echo"], gateway)
    schemas = gw.schema()
    names = [schema["function"]["name"] for schema in schemas]
    assert names == ["echo"]

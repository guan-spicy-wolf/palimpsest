import textwrap
from pathlib import Path
from unittest.mock import MagicMock

from palimpsest.gateway.tool_loader import resolve_tool_providers, EvoToolGateway
from palimpsest.gateway.tools import ToolResult


def test_resolve_tool_providers(tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "__init__.py").write_text("")
    (tools_dir / "echo.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.interfaces import ToolProvider, ToolSpec
        from palimpsest.gateway.tools import ToolResult
        class EchoProvider(ToolProvider):
            def tools(self):
                return [ToolSpec(name="echo", description="Echo back", parameters={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]})]
            def execute(self, name, args, workspace):
                return ToolResult(success=True, output=args.get("msg", ""))
    """))
    providers = resolve_tool_providers(tmp_path, ["echo"])
    assert "echo" in providers


def test_evo_tool_gateway_schema(tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "__init__.py").write_text("")
    (tools_dir / "echo.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.interfaces import ToolProvider, ToolSpec
        from palimpsest.gateway.tools import ToolResult
        class EchoProvider(ToolProvider):
            def tools(self):
                return [ToolSpec(name="echo", description="Echo", parameters={"type": "object", "properties": {}})]
            def execute(self, name, args, workspace):
                return ToolResult(success=True, output="ok")
    """))
    providers = resolve_tool_providers(tmp_path, ["echo"])
    gw = EvoToolGateway(providers, MagicMock(), "job-1")
    schemas = gw.schema()
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "echo"

"""Tests for bundle-only tool resolution (Bundle MVP).

Per Bundle MVP: Tools are loaded from evo/<bundle>/tools/ only.
No global fallback, no team layer.
"""
import textwrap
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from palimpsest.runtime.tools import (
    UnifiedToolGateway,
    ToolResult,
    resolve_tool_functions,
)
from palimpsest.config import ToolsConfig


class TestResolveToolFunctionsBundleOnly:
    """Tests for resolve_tool_functions with bundle-only resolution."""

    def test_finds_bundle_tools(self, tmp_path: Path) -> None:
        """Bundle tools in evo/<bundle>/tools/ are discovered."""
        tools_dir = tmp_path / "factorio" / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "bundle_tool.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def bundle_tool(msg: str) -> ToolResult:
                \"\"\"A bundle-specific tool.\"\"\"
                return ToolResult(success=True, output=f"bundle: {msg}")
        """))

        funcs = resolve_tool_functions(tmp_path, "factorio", ["bundle_tool"])
        assert "bundle_tool" in funcs
        result = funcs["bundle_tool"](msg="hello")
        assert result.output == "bundle: hello"

    def test_empty_bundle_returns_empty(self, tmp_path: Path) -> None:
        """Empty bundle parameter returns empty dict."""
        tools_dir = tmp_path / "factorio" / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "tool.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def tool_fn(msg: str) -> ToolResult:
                return ToolResult(success=True, output=msg)
        """))

        funcs = resolve_tool_functions(tmp_path, "", ["tool_fn"])
        assert funcs == {}

    def test_nonexistent_bundle_returns_empty(self, tmp_path: Path) -> None:
        """Nonexistent bundle returns empty dict."""
        tools_dir = tmp_path / "factorio" / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "tool.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def tool_fn(msg: str) -> ToolResult:
                return ToolResult(success=True, output=msg)
        """))

        funcs = resolve_tool_functions(tmp_path, "nonexistent", ["tool_fn"])
        assert funcs == {}

    def test_returns_empty_for_missing_tools(self, tmp_path: Path) -> None:
        """Returns empty dict when no requested tools are found."""
        tools_dir = tmp_path / "factorio" / "tools"
        tools_dir.mkdir(parents=True)

        funcs = resolve_tool_functions(tmp_path, "factorio", ["nonexistent"])
        assert funcs == {}

    def test_ignores_underscore_prefixed_files(self, tmp_path: Path) -> None:
        """Files starting with underscore are ignored."""
        tools_dir = tmp_path / "factorio" / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "_private.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def private_tool(msg: str) -> ToolResult:
                return ToolResult(success=True, output=msg)
        """))
        (tools_dir / "public.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def public_tool(msg: str) -> ToolResult:
                return ToolResult(success=True, output=msg)
        """))

        funcs = resolve_tool_functions(tmp_path, "factorio", ["private_tool", "public_tool"])
        assert "private_tool" not in funcs  # In _private.py, should be ignored
        assert "public_tool" in funcs

    def test_multiple_tools_in_bundle(self, tmp_path: Path) -> None:
        """Multiple tools in bundle are all discovered."""
        tools_dir = tmp_path / "factorio" / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "tool_a.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def tool_a(msg: str) -> ToolResult:
                return ToolResult(success=True, output=f"a: {msg}")
        """))
        (tools_dir / "tool_b.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def tool_b(msg: str) -> ToolResult:
                return ToolResult(success=True, output=f"b: {msg}")
        """))

        funcs = resolve_tool_functions(tmp_path, "factorio", ["tool_a", "tool_b"])
        assert "tool_a" in funcs
        assert "tool_b" in funcs
        assert funcs["tool_a"](msg="x").output == "a: x"
        assert funcs["tool_b"](msg="y").output == "b: y"


class TestUnifiedToolGatewayWithBundle:
    """Tests for UnifiedToolGateway with bundle parameter."""

    def test_gateway_loads_bundle_tools(self, tmp_path: Path) -> None:
        """UnifiedToolGateway loads bundle-specific tools."""
        tools_dir = tmp_path / "engineering" / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "deploy.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def deploy(env: str) -> ToolResult:
                \"\"\"Deploy to environment.\"\"\"
                return ToolResult(success=True, output=f"deployed to {env}")
        """))

        config = ToolsConfig(disabled_builtins=["bash", "spawn", "create_pr"])
        gateway = MagicMock()
        gw = UnifiedToolGateway(
            config,
            tmp_path,
            "engineering",  # bundle parameter
            ["deploy"],
            gateway,
        )
        schemas = gw.schema()
        names = [schema["function"]["name"] for schema in schemas]
        assert "deploy" in names

    def test_gateway_empty_bundle_no_tools(self, tmp_path: Path) -> None:
        """UnifiedToolGateway with empty bundle has no evo tools."""
        tools_dir = tmp_path / "factorio" / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "tool.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def tool_fn(msg: str) -> ToolResult:
                return ToolResult(success=True, output=msg)
        """))

        config = ToolsConfig(disabled_builtins=["bash", "spawn", "create_pr"])
        gateway = MagicMock()
        gw = UnifiedToolGateway(
            config,
            tmp_path,
            "",  # empty bundle
            ["tool_fn"],
            gateway,
        )
        schemas = gw.schema()
        names = [schema["function"]["name"] for schema in schemas]
        assert "tool_fn" not in names  # No evo tools loaded
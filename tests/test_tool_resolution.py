"""Tests for bundle-only tool resolution (ADR-0015).

Per ADR-0015: Tools are loaded from bundle_workspace/tools/ directly.
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


class TestResolveToolFunctionsBundleWorkspace:
    """Tests for resolve_tool_functions with bundle_workspace resolution."""

    def test_finds_bundle_tools(self, tmp_path: Path) -> None:
        """Bundle tools in bundle_workspace/tools/ are discovered."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "bundle_tool.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def bundle_tool(msg: str) -> ToolResult:
                \"\"\"A bundle-specific tool.\"\"\"
                return ToolResult(success=True, output=f"bundle: {msg}")
        """))

        funcs = resolve_tool_functions(tmp_path, ["bundle_tool"])
        assert "bundle_tool" in funcs
        result = funcs["bundle_tool"](msg="hello")
        assert result.output == "bundle: hello"

    def test_empty_workspace_returns_empty(self, tmp_path: Path) -> None:
        """Empty workspace returns empty dict when no tools found."""
        # No tools directory
        funcs = resolve_tool_functions(tmp_path, ["tool_fn"])
        assert funcs == {}

    def test_returns_empty_for_missing_tools(self, tmp_path: Path) -> None:
        """Returns empty dict when no requested tools are found."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir(parents=True)
        # No .py files

        funcs = resolve_tool_functions(tmp_path, ["nonexistent"])
        assert funcs == {}

    def test_ignores_underscore_prefixed_files(self, tmp_path: Path) -> None:
        """Files starting with underscore are ignored."""
        tools_dir = tmp_path / "tools"
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

        funcs = resolve_tool_functions(tmp_path, ["private_tool", "public_tool"])
        assert "private_tool" not in funcs  # In _private.py, should be ignored
        assert "public_tool" in funcs

    def test_multiple_tools_in_bundle(self, tmp_path: Path) -> None:
        """Multiple tools in one file are discovered."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "multi.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def first(msg: str) -> ToolResult:
                return ToolResult(success=True, output=f"first: {msg}")

            @tool
            def second(msg: str) -> ToolResult:
                return ToolResult(success=True, output=f"second: {msg}")
        """))

        funcs = resolve_tool_functions(tmp_path, ["first", "second"])
        assert "first" in funcs
        assert "second" in funcs
        assert funcs["first"](msg="x").output == "first: x"
        assert funcs["second"](msg="y").output == "second: y"


class TestUnifiedToolGatewayWithBundle:
    """Tests for UnifiedToolGateway loading from bundle_workspace."""

    def test_gateway_loads_bundle_tools(self, tmp_path: Path) -> None:
        """Gateway loads tools from bundle_workspace/tools/."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "custom.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def custom_tool(msg: str) -> ToolResult:
                return ToolResult(success=True, output=f"custom: {msg}")
        """))

        config = ToolsConfig(disabled_builtins=["bash", "spawn", "create_pr"])
        gateway = MagicMock()
        gw = UnifiedToolGateway(config, tmp_path, ["custom_tool"], gateway)
        schemas = gw.schema()
        names = [schema["function"]["name"] for schema in schemas]
        assert names == ["custom_tool"]
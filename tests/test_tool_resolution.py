"""Tests for two-layer tool resolution (global + team-specific).

Per ADR-0011 D2: team-specific tools shadow global tools of the same name.
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


class TestResolveToolFunctionsTwoLayer:
    """Tests for resolve_tool_functions with team parameter."""

    def test_finds_global_tools(self, tmp_path: Path) -> None:
        """Global tools in evo/tools/ are discovered."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "global_tool.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def global_tool(msg: str) -> ToolResult:
                \"\"\"A global tool.\"\"\"
                return ToolResult(success=True, output=msg)
        """))

        funcs = resolve_tool_functions(tmp_path, "default", ["global_tool"])
        assert "global_tool" in funcs
        result = funcs["global_tool"](msg="hello")
        assert result.output == "hello"

    def test_finds_team_specific_tools(self, tmp_path: Path) -> None:
        """Team-specific tools in evo/teams/<team>/tools/ are discovered."""
        # Create team-specific tools directory
        team_tools_dir = tmp_path / "teams" / "alpha" / "tools"
        team_tools_dir.mkdir(parents=True)
        (team_tools_dir / "team_tool.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def team_tool(msg: str) -> ToolResult:
                \"\"\"A team-specific tool.\"\"\"
                return ToolResult(success=True, output=f"team: {msg}")
        """))

        funcs = resolve_tool_functions(tmp_path, "alpha", ["team_tool"])
        assert "team_tool" in funcs
        result = funcs["team_tool"](msg="hello")
        assert result.output == "team: hello"

    def test_team_tool_shadows_global_tool(self, tmp_path: Path) -> None:
        """Team-specific tool shadows global tool of the same name (ADR-0011 D2)."""
        # Create global tool
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "echo.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def echo(msg: str) -> ToolResult:
                \"\"\"Global echo.\"\"\"
                return ToolResult(success=True, output=f"global: {msg}")
        """))

        # Create team-specific tool with same name
        team_tools_dir = tmp_path / "teams" / "alpha" / "tools"
        team_tools_dir.mkdir(parents=True)
        (team_tools_dir / "echo.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def echo(msg: str) -> ToolResult:
                \"\"\"Team-specific echo.\"\"\"
                return ToolResult(success=True, output=f"team-alpha: {msg}")
        """))

        funcs = resolve_tool_functions(tmp_path, "alpha", ["echo"])
        assert "echo" in funcs
        # Should be team-specific, not global
        result = funcs["echo"](msg="hello")
        assert result.output == "team-alpha: hello"

    def test_global_and_team_tools_both_available(self, tmp_path: Path) -> None:
        """Both global and team tools are available when they have different names."""
        # Create global tool
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "global_tool.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def global_tool(msg: str) -> ToolResult:
                \"\"\"A global tool.\"\"\"
                return ToolResult(success=True, output=f"global: {msg}")
        """))

        # Create team-specific tool with different name
        team_tools_dir = tmp_path / "teams" / "beta" / "tools"
        team_tools_dir.mkdir(parents=True)
        (team_tools_dir / "team_tool.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def team_tool(msg: str) -> ToolResult:
                \"\"\"A team-specific tool.\"\"\"
                return ToolResult(success=True, output=f"team-beta: {msg}")
        """))

        funcs = resolve_tool_functions(tmp_path, "beta", ["global_tool", "team_tool"])
        assert "global_tool" in funcs
        assert "team_tool" in funcs
        assert funcs["global_tool"](msg="x").output == "global: x"
        assert funcs["team_tool"](msg="y").output == "team-beta: y"

    def test_no_team_tools_dir_falls_back_to_global(self, tmp_path: Path) -> None:
        """If team has no tools directory, falls back to global tools."""
        # Create global tool
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "fallback.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def fallback(msg: str) -> ToolResult:
                \"\"\"A fallback tool.\"\"\"
                return ToolResult(success=True, output=f"fallback: {msg}")
        """))

        # No team tools directory for team "gamma"
        funcs = resolve_tool_functions(tmp_path, "gamma", ["fallback"])
        assert "fallback" in funcs
        result = funcs["fallback"](msg="test")
        assert result.output == "fallback: test"

    def test_no_global_tools_dir_still_finds_team_tools(self, tmp_path: Path) -> None:
        """If no global tools directory, team tools still work."""
        # No global tools directory
        # Create team-specific tools
        team_tools_dir = tmp_path / "teams" / "delta" / "tools"
        team_tools_dir.mkdir(parents=True)
        (team_tools_dir / "only_team.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def only_team(msg: str) -> ToolResult:
                \"\"\"Only in team.\"\"\"
                return ToolResult(success=True, output=f"team-only: {msg}")
        """))

        funcs = resolve_tool_functions(tmp_path, "delta", ["only_team"])
        assert "only_team" in funcs
        result = funcs["only_team"](msg="test")
        assert result.output == "team-only: test"

    def test_returns_empty_for_missing_tools(self, tmp_path: Path) -> None:
        """Returns empty dict when no requested tools are found."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()

        funcs = resolve_tool_functions(tmp_path, "default", ["nonexistent"])
        assert funcs == {}

    def test_ignores_underscore_prefixed_files(self, tmp_path: Path) -> None:
        """Files starting with underscore are ignored."""
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
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

        funcs = resolve_tool_functions(tmp_path, "default", ["private_tool", "public_tool"])
        assert "private_tool" not in funcs  # In _private.py, should be ignored
        assert "public_tool" in funcs


class TestUnifiedToolGatewayWithTeam:
    """Tests for UnifiedToolGateway with team parameter."""

    def test_gateway_loads_team_tools(self, tmp_path: Path) -> None:
        """UnifiedToolGateway loads team-specific tools."""
        # Create team-specific tool
        team_tools_dir = tmp_path / "teams" / "engineering" / "tools"
        team_tools_dir.mkdir(parents=True)
        (team_tools_dir / "deploy.py").write_text(textwrap.dedent("""\
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
            "engineering",  # team parameter
            ["deploy"],
            gateway,
        )
        schemas = gw.schema()
        names = [schema["function"]["name"] for schema in schemas]
        assert "deploy" in names

    def test_gateway_team_tool_shadows_global(self, tmp_path: Path) -> None:
        """Team-specific tool shadows global in UnifiedToolGateway."""
        # Create global tool
        tools_dir = tmp_path / "tools"
        tools_dir.mkdir()
        (tools_dir / "build.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def build(target: str) -> ToolResult:
                \"\"\"Global build.\"\"\"
                return ToolResult(success=True, output=f"global build: {target}")
        """))

        # Create team-specific tool with same name
        team_tools_dir = tmp_path / "teams" / "special" / "tools"
        team_tools_dir.mkdir(parents=True)
        (team_tools_dir / "build.py").write_text(textwrap.dedent("""\
            from palimpsest.runtime.tools import tool, ToolResult

            @tool
            def build(target: str) -> ToolResult:
                \"\"\"Team-specific build.\"\"\"
                return ToolResult(success=True, output=f"team build: {target}")
        """))

        config = ToolsConfig(disabled_builtins=["bash", "spawn", "create_pr"])
        gateway = MagicMock()
        gw = UnifiedToolGateway(
            config,
            tmp_path,
            "special",  # team parameter
            ["build"],
            gateway,
        )

        # Execute should use team-specific version
        result = gw.execute("build", "call-1", {"target": "prod"}, "/workspace")
        assert result.success
        assert "team build" in result.output
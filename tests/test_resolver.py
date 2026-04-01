import sys
import textwrap
from pathlib import Path

from palimpsest.runtime.tools import ToolResult, resolve_tool_functions


def test_resolve_discovers_decorated_tools(tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "greet.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.tools import tool, ToolResult

        @tool
        def greet() -> ToolResult:
            \"\"\"Say hi.\"\"\"
            return ToolResult(success=True, output="hello")
    """))
    result = resolve_tool_functions(tmp_path, "default", ["greet"])
    assert "greet" in result
    assert result["greet"]().output == "hello"


def test_resolve_no_sys_modules_leak(tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "leak_check.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.tools import tool, ToolResult

        @tool
        def leak() -> ToolResult:
            \"\"\"Test leak.\"\"\"
            return ToolResult(success=True, output="ok")
    """))
    before = set(sys.modules.keys())
    resolve_tool_functions(tmp_path, "default", ["leak"])
    after = set(sys.modules.keys())
    new_modules = after - before
    assert not any("leak_check" in m for m in new_modules)


def test_resolve_filters_to_requested_only(tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "multi.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.tools import tool, ToolResult

        @tool
        def a() -> ToolResult:
            \"\"\"Tool a.\"\"\"
            return ToolResult(success=True, output="a")

        @tool
        def b() -> ToolResult:
            \"\"\"Tool b.\"\"\"
            return ToolResult(success=True, output="b")
    """))
    result = resolve_tool_functions(tmp_path, "default", ["a"])
    assert "a" in result
    assert "b" not in result


def test_resolve_warns_missing(tmp_path):
    import io
    from loguru import logger

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()

    log_sink = io.StringIO()
    sink_id = logger.add(log_sink, format="{message}", level="WARNING")
    try:
        resolve_tool_functions(tmp_path, "default", ["nonexistent"])
        log_output = log_sink.getvalue()
        assert "nonexistent" in log_output
    finally:
        logger.remove(sink_id)

import sys
import textwrap
from pathlib import Path
from palimpsest.runtime.interfaces import ToolProvider, ToolSpec
from palimpsest.gateway.tools import ToolResult


def test_resolve_discovers_subclasses(tmp_path):
    from palimpsest.runtime.resolver import resolve_providers
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "__init__.py").write_text("")
    (tools_dir / "greet.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.interfaces import ToolProvider, ToolSpec
        from palimpsest.gateway.tools import ToolResult
        class GreetProvider(ToolProvider):
            def tools(self):
                return [ToolSpec(name="greet", description="Say hi", parameters={})]
            def execute(self, name, args, workspace):
                return ToolResult(success=True, output="hello")
    """))
    result = resolve_providers(
        scan_dir=tools_dir,
        base_class=ToolProvider,
        key_fn=lambda inst: [s.name for s in inst.tools()],
        requested=["greet"],
    )
    assert "greet" in result
    assert result["greet"].execute("greet", {}, "/tmp").output == "hello"


def test_resolve_no_sys_modules_leak(tmp_path):
    from palimpsest.runtime.resolver import resolve_providers
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "__init__.py").write_text("")
    (tools_dir / "leak_check.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.interfaces import ToolProvider, ToolSpec
        from palimpsest.gateway.tools import ToolResult
        class LeakProvider(ToolProvider):
            def tools(self):
                return [ToolSpec(name="leak", description="x", parameters={})]
            def execute(self, name, args, workspace):
                return ToolResult(success=True, output="ok")
    """))
    before = set(sys.modules.keys())
    resolve_providers(
        scan_dir=tools_dir,
        base_class=ToolProvider,
        key_fn=lambda inst: [s.name for s in inst.tools()],
        requested=["leak"],
    )
    after = set(sys.modules.keys())
    new_modules = after - before
    assert not any("leak_check" in m for m in new_modules)


def test_resolve_filters_to_requested_only(tmp_path):
    from palimpsest.runtime.resolver import resolve_providers
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "__init__.py").write_text("")
    (tools_dir / "multi.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.interfaces import ToolProvider, ToolSpec
        from palimpsest.gateway.tools import ToolResult
        class MultiProvider(ToolProvider):
            def tools(self):
                return [
                    ToolSpec(name="a", description="a", parameters={}),
                    ToolSpec(name="b", description="b", parameters={}),
                ]
            def execute(self, name, args, workspace):
                return ToolResult(success=True, output=name)
    """))
    result = resolve_providers(
        scan_dir=tools_dir,
        base_class=ToolProvider,
        key_fn=lambda inst: [s.name for s in inst.tools()],
        requested=["a"],
    )
    assert "a" in result
    assert "b" not in result


def test_resolve_warns_missing(tmp_path):
    import io
    from loguru import logger
    from palimpsest.runtime.resolver import resolve_providers

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "__init__.py").write_text("")

    log_sink = io.StringIO()
    sink_id = logger.add(log_sink, format="{message}", level="WARNING")
    try:
        resolve_providers(
            scan_dir=tools_dir,
            base_class=ToolProvider,
            key_fn=lambda inst: [s.name for s in inst.tools()],
            requested=["nonexistent"],
        )
        log_output = log_sink.getvalue()
        assert "nonexistent" in log_output
    finally:
        logger.remove(sink_id)

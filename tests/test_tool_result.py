from palimpsest.runtime.tools import ToolResult

def test_tool_result_has_terminal_field():
    r = ToolResult(success=True, output="done", terminal=True)
    assert r.terminal is True

def test_tool_result_terminal_defaults_false():
    r = ToolResult(success=True, output="ok")
    assert r.terminal is False

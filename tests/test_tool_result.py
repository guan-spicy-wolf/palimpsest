from palimpsest.runtime.tools import ToolResult

def test_tool_result_has_success_and_output():
    r = ToolResult(success=True, output="done")
    assert r.success is True
    assert r.output == "done"

def test_tool_result_has_no_terminal_field():
    r = ToolResult(success=True, output="ok")
    assert not hasattr(r, "terminal")

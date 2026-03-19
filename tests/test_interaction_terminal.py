from unittest.mock import MagicMock
from palimpsest.gateway.tools import ToolResult
from palimpsest.stages.interaction import run_interaction_loop


class FakeToolCall:
    """Simple stand-in for a tool call object (avoids MagicMock 'name' special handling)."""
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.name = name
        self.arguments = arguments


class FakeLLM:
    def __init__(self, tool_calls_per_turn):
        self._tool_calls = tool_calls_per_turn
        self.call_count = 0

    def call(self, messages, tools_schema):
        self.call_count += 1
        tcs = self._tool_calls
        tool_calls = [FakeToolCall(f"tc{i}", tc[0], tc[1]) for i, tc in enumerate(tcs)]
        raw_message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": f"tc{i}", "type": "function", "function": {"name": tc[0], "arguments": "{}"}}
                for i, tc in enumerate(tcs)
            ],
        }
        return MagicMock(text=None, tool_calls=tool_calls, raw_message=raw_message)


class FakeTools:
    def schema(self):
        return []

    def execute(self, name, call_id, args, workspace):
        if name == "task_complete":
            return ToolResult(success=True, output="Task complete.", terminal=True)
        return ToolResult(success=True, output="ok")


def test_interaction_loop_stops_on_terminal():
    llm = FakeLLM([("task_complete", {"summary": "done", "status": "success"})])
    tools = FakeTools()
    context = {"system": "test agent", "task": "do nothing"}
    result = run_interaction_loop("job-1", context, "/tmp", llm, tools, max_iterations=10)
    assert llm.call_count == 1
    assert result["status"] == "success"


def test_interaction_loop_terminal_mid_batch():
    llm = FakeLLM([("bash", {"command": "echo hi"}), ("task_complete", {"summary": "done", "status": "success"})])
    tools = FakeTools()
    context = {"system": "test agent", "task": "do something then complete"}
    result = run_interaction_loop("job-1", context, "/tmp", llm, tools, max_iterations=10)
    assert llm.call_count == 1
    assert result["status"] == "success"

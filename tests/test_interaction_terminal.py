from unittest.mock import MagicMock
from palimpsest.runtime.tools import ToolResult
from palimpsest.stages.interaction import run_interaction_loop


class FakeToolCall:
    """Simple stand-in for a tool call object (avoids MagicMock 'name' special handling)."""
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.name = name
        self.arguments = arguments


class FakeLLM:
    def __init__(self, turns):
        self._turns = turns
        self.call_count = 0

    def call(self, messages, tools_schema):
        index = min(self.call_count, len(self._turns) - 1)
        self.call_count += 1
        text, tcs = self._turns[index]
        tool_calls = [FakeToolCall(f"tc{i}", tc[0], tc[1]) for i, tc in enumerate(tcs)]
        raw_message = {
            "role": "assistant",
            "content": text,
            "tool_calls": [
                {"id": f"tc{i}", "type": "function", "function": {"name": tc[0], "arguments": "{}"}}
                for i, tc in enumerate(tcs)
            ],
        }
        return MagicMock(text=text, tool_calls=tool_calls, raw_message=raw_message)


class FakeTools:
    def schema(self):
        return []

    def execute(self, name, call_id, args, workspace):
        if name == "task_complete":
            return ToolResult(success=True, output="Task complete.", terminal=True)
        return ToolResult(success=True, output="ok")


def test_interaction_loop_stops_on_terminal():
    llm = FakeLLM([(None, [("task_complete", {"summary": "done"})])])
    tools = FakeTools()
    context = {"system": "test agent", "task": "do nothing"}
    result = run_interaction_loop("job-1", context, "/tmp", llm, tools, max_iterations=10)
    assert llm.call_count == 1
    assert "summary" in result


def test_interaction_loop_terminal_mid_batch():
    llm = FakeLLM(
        [(None, [("bash", {"command": "echo hi"}), ("task_complete", {"summary": "done"})])]
    )
    tools = FakeTools()
    context = {"system": "test agent", "task": "do something then complete"}
    result = run_interaction_loop("job-1", context, "/tmp", llm, tools, max_iterations=10)
    assert llm.call_count == 1
    assert "summary" in result


def test_interaction_loop_repompts_then_marks_in_progress():
    llm = FakeLLM([
        ("I think I am done.", []),
        ("Still not calling task_complete.", []),
    ])
    tools = FakeTools()
    context = {"system": "test agent", "task": "do nothing"}
    result = run_interaction_loop("job-1", context, "/tmp", llm, tools, max_iterations=10)
    assert llm.call_count == 2
    assert result["summary"] == "Still not calling task_complete."


def test_interaction_loop_can_resume_with_user_prompt():
    llm = FakeLLM([
        (None, [("task_complete", {"summary": "fixed"})]),
    ])
    tools = FakeTools()
    context = {"system": "test agent", "task": "do nothing"}
    prior_messages = [{"role": "user", "content": "initial task"}]
    result = run_interaction_loop(
        "job-1",
        context,
        "/tmp",
        llm,
        tools,
        max_iterations=10,
        messages=prior_messages,
        user_prompt="Please fix publication issues and complete.",
    )
    assert llm.call_count == 1
    assert "summary" in result
    assert any(
        message["role"] == "user" and "publication issues" in message["content"]
        for message in result["messages"]
    )


def test_non_task_complete_terminal_is_ignored():
    class NonTaskTerminalTools(FakeTools):
        def execute(self, name, call_id, args, workspace):
            if name == "bash":
                return ToolResult(success=True, output="ok", terminal=True)
            return super().execute(name, call_id, args, workspace)

    llm = FakeLLM([
        (None, [("bash", {"command": "echo hi"})]),
        ("No explicit completion.", []),
        ("Still no explicit completion.", []),
    ])
    tools = NonTaskTerminalTools()
    context = {"system": "test agent", "task": "do nothing"}
    result = run_interaction_loop("job-1", context, "/tmp", llm, tools, max_iterations=10)
    assert "summary" in result

import json
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
    def __init__(self, turns, *, max_iterations=50, max_iterations_hard=0):
        self._turns = turns
        self.call_count = 0
        self.max_iterations = max_iterations
        self.max_iterations_hard = max_iterations_hard

    def budget_exhausted(self):
        if self.max_iterations_hard > 0 and self.call_count >= self.max_iterations_hard:
            return "max_iterations_hard"
        return None

    def budget_remaining(self):
        remaining = max(0, self.max_iterations - self.call_count)
        return {
            "iterations": {
                "used": self.call_count,
                "limit": self.max_iterations,
                "remaining": remaining,
                "limited": True,
            },
            "iterations_hard": {
                "used": self.call_count,
                "limit": self.max_iterations_hard or None,
                "remaining": (max(0, self.max_iterations_hard - self.call_count) if self.max_iterations_hard else None),
                "limited": bool(self.max_iterations_hard),
            },
            "input_tokens": {"used": 0, "limit": None, "remaining": None, "limited": False},
            "output_tokens": {"used": 0, "limit": None, "remaining": None, "limited": False},
            "cost": {"used": 0.0, "limit": None, "remaining": None, "limited": False},
        }

    def call(self, messages, tools_schema):
        index = min(self.call_count, len(self._turns) - 1)
        self.call_count += 1
        text, tcs = self._turns[index]
        tool_calls = [FakeToolCall(f"tc{i}", tc[0], tc[1]) for i, tc in enumerate(tcs)]
        raw_message = {
            "role": "assistant",
            "content": text,
            "tool_calls": [
                {
                    "id": f"tc{i}",
                    "type": "function",
                    "function": {"name": tc[0], "arguments": json.dumps(tc[1])},
                }
                for i, tc in enumerate(tcs)
            ],
        }
        return MagicMock(text=text, tool_calls=tool_calls, raw_message=raw_message)


class FakeTools:
    def __init__(self):
        self.calls = []

    def schema(self):
        return []

    def execute(self, name, call_id, args, workspace):
        self.calls.append((name, args))
        return ToolResult(success=True, output="ok")


def test_interaction_loop_confirms_idle_and_uses_first_summary():
    llm = FakeLLM([
        ("I think I am done.", []),
        ("This confirmation text should not replace the first summary.", []),
    ])
    tools = FakeTools()
    context = {"system": "test agent", "task": "do nothing"}
    result = run_interaction_loop("job-1", context, "/tmp", llm, tools)
    assert llm.call_count == 2
    assert result["status"] == "complete"
    assert result["summary"] == "I think I am done."


def test_interaction_loop_resets_idle_state_when_tool_calls_resume():
    llm = FakeLLM(
        [
            ("Maybe finished soon.", []),
            (None, [("bash", {"command": "echo hi"})]),
            ("Now the work is actually done.", []),
            ("Ignored confirmation follow-up.", []),
        ]
    )
    tools = FakeTools()
    context = {"system": "test agent", "task": "do something then stop"}
    result = run_interaction_loop("job-1", context, "/tmp", llm, tools)
    assert llm.call_count == 4
    assert result["summary"] == "Now the work is actually done."
    assert tools.calls == [("bash", {"command": "echo hi"})]


def test_interaction_loop_can_resume_with_user_prompt():
    llm = FakeLLM([
        (None, [("bash", {"command": "echo hi"})]),
        ("Fixed publication issues.", []),
        ("Ignored follow-up.", []),
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
        messages=prior_messages,
        user_prompt="Please fix publication issues and continue if needed.",
    )
    assert llm.call_count == 3
    assert result["summary"] == "Fixed publication issues."
    assert any(
        message["role"] == "user" and "publication issues" in message["content"]
        for message in result["messages"]
    )


def test_interaction_loop_budget_exhaustion_returns_partial_code():
    llm = FakeLLM(
        [
            ("Budget nearly exhausted but summary is available.", []),
        ],
        max_iterations_hard=1,
    )
    tools = FakeTools()
    context = {"system": "test agent", "task": "wrap up quickly"}
    result = run_interaction_loop("job-1", context, "/tmp", llm, tools)
    assert result["status"] == "partial"
    assert result["code"] == "budget_exhausted"
    assert result["budget_dim"] == "max_iterations_hard"
    assert result["summary"] == "Budget nearly exhausted but summary is available."

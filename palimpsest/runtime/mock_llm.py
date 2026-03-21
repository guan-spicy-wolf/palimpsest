"""Mock LLM gateway for testing without API keys.

Returns predetermined responses based on task content.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from loguru import logger

from palimpsest.config import LLMConfig
from palimpsest.events import LLMRequestData, LLMResponseData
from palimpsest.runtime.event_gateway import EventGateway


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall]
    finish_reason: str
    input_tokens: int
    output_tokens: int
    raw_message: dict


class MockLLMGateway:
    """Mock LLM that returns tool calls based on task patterns."""

    def __init__(self, config: LLMConfig, emitter: EventGateway) -> None:
        self.config = config
        self.emitter = emitter
        self.iteration = 0

    def call(self, messages: list[dict], tools_schema: list[dict]) -> LLMResponse:
        """Return mock response based on conversation context."""
        self.iteration += 1
        
        # Emit request event
        self.emitter.emit(
            "agent.llm.request",
            LLMRequestData(
                job_id="mock",
                model=self.config.model,
                messages_count=len(messages),
                tools_count=len(tools_schema),
                iteration=self.iteration,
            ),
        )

        # Extract task from messages
        task_text = ""
        for msg in messages:
            if msg.get("role") == "user":
                task_text = msg.get("content", "")
                break

        # Generate mock tool calls based on task patterns
        tool_calls = self._generate_tool_calls(task_text)
        
        response = LLMResponse(
            text=None if tool_calls else "Task completed successfully.",
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
            input_tokens=len(json.dumps(messages)),
            output_tokens=100,
            raw_message={"mock": True},
        )

        # Emit response event
        self.emitter.emit(
            "agent.llm.response",
            LLMResponseData(
                job_id="mock",
                model=self.config.model,
                finish_reason=response.finish_reason,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                duration_ms=100,
            ),
        )

        return response

    def _generate_tool_calls(self, task_text: str) -> list[ToolCall]:
        """Generate tool calls based on task content."""
        task_lower = task_text.lower()
        calls = []

        # Pattern matching for common tasks
        if "read" in task_lower or "review" in task_lower:
            calls.append(ToolCall(
                id=f"call_{uuid.uuid4().hex[:8]}",
                name="read_file",
                arguments={"path": "README.md"},
            ))

        if "error handling" in task_lower:
            calls.append(ToolCall(
                id=f"call_{uuid.uuid4().hex[:8]}",
                name="read_file",
                arguments={"path": "palimpsest/runner.py"},
            ))
            calls.append(ToolCall(
                id=f"call_{uuid.uuid4().hex[:8]}",
                name="task_complete",
                arguments={"summary": "Reviewed runner.py error handling. Current implementation has basic try-except blocks. Recommend adding specific exception types and recovery mechanisms."},
            ))

        if "type hint" in task_lower:
            calls.append(ToolCall(
                id=f"call_{uuid.uuid4().hex[:8]}",
                name="read_file",
                arguments={"path": "palimpsest/config.py"},
            ))
            calls.append(ToolCall(
                id=f"call_{uuid.uuid4().hex[:8]}",
                name="task_complete",
                arguments={"summary": "Added type hints to config.py. All dataclasses now have proper type annotations."},
            ))

        if "test" in task_lower:
            calls.append(ToolCall(
                id=f"call_{uuid.uuid4().hex[:8]}",
                name="task_complete",
                arguments={"summary": "Created unit tests following existing patterns."},
            ))

        if "tool" in task_lower and "add" in task_lower:
            calls.append(ToolCall(
                id=f"call_{uuid.uuid4().hex[:8]}",
                name="write_file",
                arguments={
                    "path": "evo/tools/file_ops_extended.py",
                    "content": "# Extended file operations tools\n\ndef move_file(source: str, destination: str) -> dict:\n    '''Move file from source to destination.'''\n    import shutil\n    shutil.move(source, destination)\n    return {'success': True}\n\ndef copy_file(source: str, destination: str) -> dict:\n    '''Copy file from source to destination.'''\n    import shutil\n    shutil.copy2(source, destination)\n    return {'success': True}\n\ndef delete_file(path: str) -> dict:\n    '''Delete file at path.'''\n    import os\n    os.remove(path)\n    return {'success': True}\n"
                },
            ))
            calls.append(ToolCall(
                id=f"call_{uuid.uuid4().hex[:8]}",
                name="task_complete",
                arguments={"summary": "Added move_file, copy_file, delete_file tools to evo/tools/file_ops_extended.py"},
            ))

        # Default: complete task
        if not calls:
            calls.append(ToolCall(
                id=f"call_{uuid.uuid4().hex[:8]}",
                name="task_complete",
                arguments={"summary": f"Completed task: {task_text[:50]}..."},
            ))

        return calls

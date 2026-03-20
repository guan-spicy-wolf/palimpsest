"""Event definitions for the Palimpsest Agent.

All events inherit from ``BaseEvent`` and define an explicit ``event_type``
class variable. They are Pydantic models to ensure cost-free JSON serialization.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel


class BaseEvent(BaseModel):
    """Base model for all events. Provides standard envelope and routing name."""
    event_type: ClassVar[str]
    job_id: str = ""


class LLMRequestData(BaseEvent):
    event_type: ClassVar[str] = "agent.llm.request"
    model: str
    messages_count: int
    tools_count: int
    iteration: int


class LLMResponseData(BaseEvent):
    event_type: ClassVar[str] = "agent.llm.response"
    model: str
    finish_reason: str
    input_tokens: int
    output_tokens: int
    duration_ms: int


class ToolExecData(BaseEvent):
    event_type: ClassVar[str] = "agent.tool.exec"
    tool_name: str
    tool_call_id: str
    arguments_preview: str


class ToolResultData(BaseEvent):
    event_type: ClassVar[str] = "agent.tool.result"
    tool_name: str
    tool_call_id: str
    success: bool
    duration_ms: int
    output_preview: str


class JobStartedData(BaseEvent):
    event_type: ClassVar[str] = "job.started"
    workspace_path: str
    evo_sha: str = ""
    base_sha: str = ""


class JobCompletedData(BaseEvent):
    event_type: ClassVar[str] = "job.completed"
    status: str
    git_ref: str | None = None
    summary: str


class JobFailedData(BaseEvent):
    event_type: ClassVar[str] = "job.failed"
    error: str
    traceback: str | None = None
    code: str = ""


class RuntimeIssueData(BaseEvent):
    event_type: ClassVar[str] = "job.runtime.issue"
    stage: str
    fatal: bool = False
    code: str = ""  # machine-readable issue code (e.g. "duplicate_tool_name")
    names: list[str] = []  # duplicate_tool_name: conflicting tool names
    violations: list[str] = []  # publication_guardrail: blocked files/reasons
    error: str = ""  # cleanup_failed: error description


class StageTransitionData(BaseEvent):
    event_type: ClassVar[str] = "job.stage.transition"
    from_stage: str
    to_stage: str


class SpawnRequestData(BaseEvent):
    event_type: ClassVar[str] = "job.spawn.request"
    """Emitted when the agent requests child task orchestration.

    The runtime does NOT execute child tasks. It publishes this event
    so that the external Supervisor can pick it up and handle fork-join.
    """
    tasks: list[dict]
    wait_for: str = "all_complete"

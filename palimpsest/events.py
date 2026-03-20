from __future__ import annotations

from pydantic import BaseModel


class LLMRequestData(BaseModel):
    job_id: str
    model: str
    messages_count: int
    tools_count: int
    iteration: int


class LLMResponseData(BaseModel):
    job_id: str
    model: str
    finish_reason: str
    input_tokens: int
    output_tokens: int
    duration_ms: int


class ToolExecData(BaseModel):
    job_id: str
    tool_name: str
    tool_call_id: str
    arguments_preview: str


class ToolResultData(BaseModel):
    job_id: str
    tool_name: str
    tool_call_id: str
    success: bool
    duration_ms: int
    output_preview: str


class JobStartedData(BaseModel):
    job_id: str
    workspace_path: str
    evo_sha: str = ""
    base_sha: str = ""


class JobCompletedData(BaseModel):
    job_id: str
    status: str
    git_ref: str | None = None
    summary: str


class JobFailedData(BaseModel):
    job_id: str
    error: str
    traceback: str | None = None
    code: str = ""


class RuntimeIssueData(BaseModel):
    job_id: str
    stage: str
    message: str
    fatal: bool = False
    code: str = ""  # machine-readable issue code (e.g. "duplicate_tool_name")


class StageTransitionData(BaseModel):
    job_id: str
    from_stage: str
    to_stage: str


class SpawnRequestData(BaseModel):
    """Emitted when the agent requests child task orchestration.

    The runtime does NOT execute child tasks.  It publishes this event
    so that the external Supervisor can pick it up and handle fork-join.
    """

    job_id: str
    tasks: list[dict]
    wait_for: str = "all_complete"


EVENT_TYPES: dict[type, str] = {
    LLMRequestData: "agent.llm.request",
    LLMResponseData: "agent.llm.response",
    ToolExecData: "agent.tool.exec",
    ToolResultData: "agent.tool.result",
    JobStartedData: "job.started",
    JobCompletedData: "job.completed",
    JobFailedData: "job.failed",
    RuntimeIssueData: "job.runtime.issue",
    StageTransitionData: "job.stage.transition",
    SpawnRequestData: "job.spawn.request",
}

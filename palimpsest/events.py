from yoitsu_contracts.events import (
    BaseEvent,
    EvalSpec,
    JobCancelledData,
    JobFailedData,
    JobStartedData,
    LLMRequestData,
    LLMResponseData,
    RuntimeIssueData,
    SpawnJobSpecData,
    SpawnRequestData,
    SpawnTaskData,
    StageTransitionData,
    SupervisorCheckpointData,
    SupervisorJobEnqueuedData,
    SupervisorJobLaunchedData,
    TaskEvaluatingData,
    TaskEvalFailedData,
    TaskResult,
    ToolExecData,
    ToolResultData,
    TriggerData,
    TaskCreatedData,
    TaskCompletedData,
    TaskFailedData,
    TaskPartialData,
    TaskCancelledData,
)
from pydantic import Field


class JobCompletedData(BaseEvent):
    """Extended JobCompletedData with structured results and completion artifact.
    
    This provides a normalized completion artifact that parent jobs and human
    reviewers can use to understand job outcomes.
    """
    event_type: str = "agent.job.completed"
    git_ref: str | None = None
    summary: str = ""
    status: str = "complete"
    code: str = ""
    # Structured completion artifact
    artifact_path: str | None = None
    publication_mode: str = "branch_only"  # "branch_only" | "pr_draft" | "approval_required"
    trust_level: str = "automated"  # "automated" | "review_suggested" | "human_required"
    requires_review: bool = False
    files_changed: list[dict] = Field(default_factory=list)


__all__ = [
    "BaseEvent",
    "EvalSpec",
    "JobCancelledData",
    "JobCompletedData",
    "JobFailedData",
    "JobStartedData",
    "LLMRequestData",
    "LLMResponseData",
    "RuntimeIssueData",
    "SpawnJobSpecData",
    "SpawnRequestData",
    "SpawnTaskData",
    "StageTransitionData",
    "SupervisorCheckpointData",
    "SupervisorJobEnqueuedData",
    "SupervisorJobLaunchedData",
    "TaskEvaluatingData",
    "TaskEvalFailedData",
    "TaskResult",
    "ToolExecData",
    "ToolResultData",
    "TriggerData",
    "TaskCreatedData",
    "TaskCompletedData",
    "TaskFailedData",
    "TaskPartialData",
    "TaskCancelledData",
]

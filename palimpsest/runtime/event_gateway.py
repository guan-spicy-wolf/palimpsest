"""Transparent event gateway — sits between Agent and EventStore.

The gateway automatically captures Runtime-level events (LLM calls, tool
executions, job lifecycle) without the Agent's knowledge.  Business-level
events (like spawn requests) flow through stable Tool interfaces.

All event emission MUST go through this gateway.  No code outside the
gateway is allowed to access the underlying ``EventEmitter`` directly.
"""

from __future__ import annotations

from palimpsest.emitter import EventEmitter
from palimpsest.events import (
    JobCompletedData,
    JobFailedData,
    JobStartedData,
    LLMRequestData,
    LLMResponseData,
    StageTransitionData,
    ToolExecData,
    ToolResultData,
)


class EventGateway:
    """Transparent event gateway wrapping the EventEmitter.

    All event emission is centralised here so that the Agent sandbox
    has no direct access to the underlying emitter.  The gateway exposes
    typed ``emit_*`` helpers that the Runtime calls at each lifecycle
    boundary.
    """

    def __init__(self, emitter: EventEmitter):
        self._emitter = emitter

    # -- Runtime-level events (fully transparent to Agent) --

    def emit_llm_request(self, data: LLMRequestData) -> None:
        self._emitter.emit(data)

    def emit_llm_response(self, data: LLMResponseData) -> None:
        self._emitter.emit(data)

    def emit_tool_exec(self, data: ToolExecData) -> None:
        self._emitter.emit(data)

    def emit_tool_result(self, data: ToolResultData) -> None:
        self._emitter.emit(data)

    def emit_job_started(self, data: JobStartedData) -> None:
        self._emitter.emit(data)

    def emit_job_completed(self, data: JobCompletedData) -> None:
        self._emitter.emit(data)

    def emit_job_failed(self, data: JobFailedData) -> None:
        self._emitter.emit(data)

    def emit_stage_transition(
        self, job_id: str, from_stage: str, to_stage: str
    ) -> None:
        """Emit a stage transition event through the gateway."""
        self._emitter.emit(
            StageTransitionData(
                job_id=job_id, from_stage=from_stage, to_stage=to_stage
            )
        )

    # -- Context queries (read-only, scoped to a specific job) --

    def recent_events(
        self, limit: int = 10, *, job_id: str | None = None
    ) -> list[dict]:
        """Return recent events, optionally filtered by job_id."""
        return self._emitter.recent_events(limit, job_id=job_id)

    def close(self) -> None:
        self._emitter.close()

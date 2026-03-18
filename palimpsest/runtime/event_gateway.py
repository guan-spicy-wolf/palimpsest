"""Transparent event gateway — sits between Agent and EventStore.

The gateway automatically captures Runtime-level events (LLM calls, tool
executions, job lifecycle) without the Agent's knowledge.  Business-level
events (like spawn requests) flow through stable Tool interfaces.

The Agent code never directly touches the event emission mechanism.
"""

from __future__ import annotations

from palimpsest.emitter import EventEmitter
from palimpsest.events import (
    EVENT_TYPES,
    JobCompletedData,
    JobFailedData,
    JobStartedData,
    LLMRequestData,
    LLMResponseData,
    ToolExecData,
    ToolResultData,
    VersionAdvancedData,
    VersionRolledBackData,
)
from pydantic import BaseModel


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

    # -- Version management events --

    def emit_version_advanced(self, data: VersionAdvancedData) -> None:
        self._emitter.emit(data)

    def emit_version_rolled_back(self, data: VersionRolledBackData) -> None:
        self._emitter.emit(data)

    # -- Context queries (read-only access for context building) --

    def recent_events(self, limit: int = 10) -> list[dict]:
        return self._emitter.recent_events(limit)

    def close(self) -> None:
        self._emitter.close()

"""Transparent event gateway — sits between Runtime and EventStore.

All event emission MUST go through this gateway.
The gateway automatically injects ambient contextual fields (e.g. ``job_id``)
into pure ``BaseEvent`` objects and relays them without participating in business logic.
"""

from __future__ import annotations

from typing import Any

from palimpsest.emitter import EventEmitter
from palimpsest.events import BaseEvent
from yoitsu_contracts.config import EventData


class EventGateway:
    """Transparent event gateway wrapping the EventEmitter.

    This implements a strict CQRS (Command-side) architecture boundary, isolating
    history queries from generation logic and forbidding the independent evo/ tools
    from directly emitting unauthorized system events.
    """

    def __init__(self, emitter: EventEmitter, job_id: str = "", task_id: str = ""):
        self.__emitter = emitter
        self._job_id = job_id
        self._task_id = task_id

    def emit(self, event: BaseEvent) -> None:
        """Inject contextual ids and forward event securely to emitter."""
        event.job_id = self._job_id
        if not getattr(event, "task_id", ""):
            event.task_id = self._task_id
        self.__emitter.emit(event)

    def emit_data(self, event_data: EventData) -> None:
        """Convert EventData (from capability) to a dynamic event and emit.
        
        ADR-0016: Capability returns EventData with type and data.
        Gateway injects job_id and task_id into data before emitting.
        """
        data = dict(event_data.data)
        data.setdefault("job_id", self._job_id)
        data.setdefault("task_id", self._task_id)
        self.__emitter.emit_raw(event_data.type, data)

    def close(self) -> None:
        """Gracefully shutdown the underlying emitter client."""
        self.__emitter.close()

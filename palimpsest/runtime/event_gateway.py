"""Transparent event gateway — sits between Runtime and EventStore.

All event emission MUST go through this gateway.
The gateway automatically injects ambient contextual fields (e.g. ``job_id``)
into pure ``BaseEvent`` objects and relays them without participating in business logic.
"""

from __future__ import annotations

from palimpsest.emitter import EventEmitter
from palimpsest.events import BaseEvent


class EventGateway:
    """Transparent event gateway wrapping the EventEmitter.

    This implements a strict CQRS (Command-side) architecture boundary, isolating
    history queries from generation logic and forbidding the independent evo/ tools
    from directly emitting unauthorized system events.
    """

    def __init__(self, emitter: EventEmitter, job_id: str = ""):
        self.__emitter = emitter
        self._job_id = job_id

    def emit(self, event: BaseEvent) -> None:
        """Inject contextual job_id and forward event securely to emitter."""
        event.job_id = self._job_id
        self.__emitter.emit(event)

    def close(self) -> None:
        """Gracefully shutdown the underlying emitter client."""
        self.__emitter.close()

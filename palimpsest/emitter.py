from __future__ import annotations

import os

import httpx
from loguru import logger
from pydantic import BaseModel

from palimpsest.config import EventStoreConfig
from palimpsest.events import BaseEvent


class EventEmitter:
    """Synchronous HTTP client for Pasloe EventStore."""

    def __init__(self, config: EventStoreConfig):
        self._config = config
        self._noop = not config.url

        if self._noop:
            self._client = None
            logger.debug("EventStore not configured, emitter in no-op mode")
            return

        headers = {}
        api_key = os.environ.get(config.api_key_env, "")
        if api_key:
            headers["X-API-Key"] = api_key

        self._client = httpx.Client(base_url=config.url, headers=headers, timeout=10.0)

    def emit(self, event_data: BaseModel) -> dict | None:
        """Post event to Pasloe. Fire-and-forget, logs on error."""
        if self._noop:
            return None

        if not hasattr(event_data, "event_type"):
            logger.warning(f"Event missing event_type: {type(event_data)}")
            return None

        event_type = event_data.event_type
        payload = {
            "source_id": self._config.source_id,
            "type": event_type,
            "data": event_data.model_dump(),
        }

        try:
            response = self._client.post("/events", json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.warning(f"Failed to emit event {event_type}: {exc}")
            return None


    def close(self):
        if self._client:
            self._client.close()

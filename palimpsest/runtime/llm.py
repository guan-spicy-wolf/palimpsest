"""LLM gateway — manages communication with the language model.

Part of the Runtime (skeleton).  Event emission is handled transparently
through the EventGateway; the Agent never sees these events being sent.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

import litellm
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


class LLMGateway(ABC):
    """Abstract LLM gateway."""

    @abstractmethod
    def call(self, messages: list[dict], tools_schema: list[dict]) -> LLMResponse:
        pass


# Maximum retries for transient LLM errors (rate limits, network failures)
_MAX_LLM_RETRIES = 3
_LLM_RETRY_BASE_DELAY = 2  # seconds, exponential backoff: 2, 4, 8


class LiteLLMGateway(LLMGateway):
    """LLM gateway using litellm with transparent event capture and retry."""

    def __init__(self, config: LLMConfig, gateway: EventGateway):
        self._config = config
        self._gateway = gateway
        self._iteration = 0
        self._api_key = os.environ.get(config.api_key_env, "")

    def call(self, messages: list[dict], tools_schema: list[dict]) -> LLMResponse:
        self._iteration += 1

        # Transparent event: LLM request
        self._gateway.emit_llm_request(
            LLMRequestData(
                model=self._config.model,
                messages_count=len(messages),
                tools_count=len(tools_schema),
                iteration=self._iteration,
            )
        )

        kwargs = {
            "model": self._config.model,
            "messages": messages,
            "tools": tools_schema,
            "tool_choice": "auto",
            "temperature": self._config.temperature,
        }

        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._config.api_base:
            kwargs["api_base"] = self._config.api_base

        start = time.monotonic_ns()
        response = self._call_with_retry(kwargs)

        duration_ms = (time.monotonic_ns() - start) // 1_000_000

        choice = response.choices[0]
        msg = choice.message

        # Transparent event: LLM response
        self._gateway.emit_llm_response(
            LLMResponseData(
                model=self._config.model,
                finish_reason=choice.finish_reason or "unknown",
                input_tokens=getattr(response.usage, "prompt_tokens", 0) or 0,
                output_tokens=getattr(response.usage, "completion_tokens", 0) or 0,
                duration_ms=duration_ms,
            )
        )

        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    args = json.loads(args)
                tool_calls.append(
                    ToolCall(
                        id=tc.id or str(uuid.uuid4()),
                        name=tc.function.name,
                        arguments=args,
                    )
                )

        raw_message = msg.model_dump() if hasattr(msg, "model_dump") else dict(msg)

        return LLMResponse(
            text=msg.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "unknown",
            input_tokens=getattr(response.usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(response.usage, "completion_tokens", 0) or 0,
            raw_message=raw_message,
        )

    # Transient error types that warrant a retry.
    _RETRYABLE = (
        litellm.RateLimitError,
        litellm.APIConnectionError,
        litellm.ServiceUnavailableError,
    )

    def _call_with_retry(self, kwargs: dict):
        """Call litellm.completion with exponential backoff on transient errors."""
        last_exc: Exception | None = None
        for attempt in range(_MAX_LLM_RETRIES + 1):
            try:
                return litellm.completion(**kwargs)
            except self._RETRYABLE as exc:
                last_exc = exc
                if attempt == _MAX_LLM_RETRIES:
                    break
                delay = _LLM_RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    f"LLM transient error (attempt {attempt + 1}/{_MAX_LLM_RETRIES + 1}), "
                    f"retrying in {delay}s: {exc}"
                )
                time.sleep(delay)
            except Exception as exc:
                logger.error(f"LLM call failed (non-retryable): {exc}")
                raise

        logger.error(f"LLM call failed after {_MAX_LLM_RETRIES + 1} attempts: {last_exc}")
        raise last_exc

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


class LiteLLMGateway(LLMGateway):
    """LLM gateway using litellm with transparent event capture."""

    def __init__(self, config: LLMConfig, gateway: EventGateway, job_id: str):
        self._config = config
        self._gateway = gateway
        self._job_id = job_id
        self._iteration = 0
        self._api_key = os.environ.get(config.api_key_env, "")

    def call(self, messages: list[dict], tools_schema: list[dict]) -> LLMResponse:
        self._iteration += 1

        # Transparent event: LLM request
        self._gateway.emit_llm_request(
            LLMRequestData(
                job_id=self._job_id,
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
        try:
            response = litellm.completion(**kwargs)
        except Exception as exc:
            logger.error(f"LLM call failed: {exc}")
            raise

        duration_ms = (time.monotonic_ns() - start) // 1_000_000

        choice = response.choices[0]
        msg = choice.message

        # Transparent event: LLM response
        self._gateway.emit_llm_response(
            LLMResponseData(
                job_id=self._job_id,
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

"""LLM gateway — native wrappers for OpenAI and Anthropic.

Part of the Runtime (skeleton). Replaces litellm with a unified gateway 
that transparently routes between the official `openai` and `anthropic` SDKs, 
providing absolute control over tool formats and caching primitives.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from loguru import logger

from palimpsest.config import LLMConfig
from palimpsest.events import LLMRequestData, LLMResponseData
from palimpsest.runtime.event_gateway import EventGateway
from palimpsest.runtime.retry_utils import retry_with_exponential_backoff


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

    def budget_exhausted(self) -> str | None:
        return None

    def budget_remaining(self) -> dict[str, dict[str, int | float | bool | None]]:
        return {}


class UnifiedLLMGateway(LLMGateway):
    """Unified Native LLM Gateway routing between OpenAI and Anthropic SDKs.
    
    Falls back to MockLLMGateway if no API key is available.
    """

    _MODEL_PRICING_PER_MILLION: tuple[tuple[str, tuple[float, float]], ...] = (
        ("claude-sonnet-4-6", (3.0, 15.0)),
        ("claude-3-7-sonnet", (3.0, 15.0)),
        ("claude-3-5-sonnet", (3.0, 15.0)),
        ("gpt-5-mini", (0.25, 2.0)),
        ("gpt-5", (1.25, 10.0)),
        ("gpt-4.1-mini", (0.40, 1.60)),
        ("gpt-4.1", (2.0, 8.0)),
        ("gpt-4o-mini", (0.15, 0.60)),
        ("gpt-4o", (2.5, 10.0)),
    )

    def __init__(self, config: LLMConfig, gateway: EventGateway):
        self._config = config
        self._gateway = gateway
        self._api_key = os.environ.get(config.api_key_env, "")
        self.total_iterations = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        self._pricing = self._lookup_model_pricing(config.model)
        self._cost_tracking_state = self._compute_cost_tracking_state(config, self._pricing)
        provider_default_env = "ANTHROPIC_API_KEY" if config.model.startswith("claude-") else "OPENAI_API_KEY"
        self._provider_api_key = self._api_key or os.environ.get(provider_default_env, "")

        if self._cost_tracking_state == "degraded":
            logger.warning(
                f"Cost budget configured for model {self._config.model!r}, "
                "but pricing is unknown; token-cost tracking is degraded"
            )
        
        # Check if we should use mock mode
        if not self._provider_api_key:
            logger.warning("No API key found, falling back to MockLLMGateway")
            self._mock_gateway = self._create_mock_gateway()
        else:
            self._mock_gateway = None

    def _create_mock_gateway(self):
        """Create a mock LLM gateway for testing."""
        from palimpsest.runtime.mock_llm import MockLLMGateway
        return MockLLMGateway(self._config)

    def call(self, messages: list[dict], tools_schema: list[dict]) -> LLMResponse:
        next_iteration = self.total_iterations + 1

        self._gateway.emit(
            LLMRequestData(
                model=self._config.model,
                messages_count=len(messages),
                tools_count=len(tools_schema),
                iteration=next_iteration,
            )
        )

        start = time.monotonic_ns()

        if self._mock_gateway:
            mock_response = self._mock_gateway.call(messages, tools_schema)
            response = LLMResponse(
                text=mock_response.text,
                tool_calls=[
                    ToolCall(id=tc.id, name=tc.name, arguments=tc.arguments)
                    for tc in mock_response.tool_calls
                ],
                finish_reason=mock_response.finish_reason,
                input_tokens=mock_response.input_tokens,
                output_tokens=mock_response.output_tokens,
                raw_message=mock_response.raw_message,
            )
        elif self._config.model.startswith("claude-"):
            response = self._call_anthropic(messages, tools_schema)
        else:
            response = self._call_openai(messages, tools_schema)

        duration_ms = (time.monotonic_ns() - start) // 1_000_000
        self._record_usage(response)

        self._gateway.emit(
            LLMResponseData(
                model=self._config.model,
                finish_reason=response.finish_reason,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                duration_ms=duration_ms,
            )
        )

        return response

    def budget_exhausted(self) -> str | None:
        """Check if any system backstop is exhausted.
        
        Per ADR-0004 D1, D7: cost is NOT an enforcement dimension.
        Only iterations_hard, input_tokens, output_tokens trigger termination.
        Cost tracking remains active for observation but not enforcement.
        """
        if self._config.max_iterations_hard > 0 and self.total_iterations >= self._config.max_iterations_hard:
            return "max_iterations_hard"
        if (
            self._config.max_total_input_tokens > 0
            and self.total_input_tokens >= self._config.max_total_input_tokens
        ):
            return "input_tokens"
        if (
            self._config.max_total_output_tokens > 0
            and self.total_output_tokens >= self._config.max_total_output_tokens
        ):
            return "output_tokens"
        # NOTE: cost is intentionally NOT checked here per ADR-0004
        # Cost tracking remains active for observation but not enforcement
        return None

    def budget_remaining(self) -> dict[str, dict[str, int | float | bool | None]]:
        return {
            "iterations": self._budget_state(self.total_iterations, self._config.max_iterations),
            "iterations_hard": self._budget_state(
                self.total_iterations,
                self._config.max_iterations_hard,
            ),
            "input_tokens": self._budget_state(
                self.total_input_tokens, self._config.max_total_input_tokens
            ),
            "output_tokens": self._budget_state(
                self.total_output_tokens, self._config.max_total_output_tokens
            ),
            "cost": self._budget_state(
                self.total_cost,
                self._config.max_total_cost,
                enabled=self._cost_budget_enabled(),
            ),
        }

    def cost_tracking_state(self) -> str:
        return self._cost_tracking_state

    def cost_tracking_degraded(self) -> bool:
        return self._cost_tracking_state == "degraded"

    def _record_usage(self, response: LLMResponse) -> None:
        self.total_iterations += 1
        self.total_input_tokens += max(0, response.input_tokens)
        self.total_output_tokens += max(0, response.output_tokens)
        cost_estimate = self._estimate_cost(response.input_tokens, response.output_tokens)
        if cost_estimate is not None:
            self.total_cost += cost_estimate
        self.total_cost += self._iteration_penalty_cost(self.total_iterations)

    @classmethod
    def _lookup_model_pricing(cls, model: str) -> tuple[float, float] | None:
        normalized = model.strip().lower()
        normalized = normalized.split("/", 1)[-1]
        for prefix, pricing in cls._MODEL_PRICING_PER_MILLION:
            if normalized.startswith(prefix):
                return pricing
        return None

    @classmethod
    def _compute_cost_tracking_state(
        cls,
        config: LLMConfig,
        pricing: tuple[float, float] | None,
    ) -> str:
        if config.max_total_cost <= 0:
            return "disabled"
        if pricing is None:
            return "degraded"
        return "active"

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> float | None:
        if self._pricing is None:
            return None
        input_rate, output_rate = self._pricing
        return (
            (max(0, input_tokens) / 1_000_000.0) * input_rate
            + (max(0, output_tokens) / 1_000_000.0) * output_rate
        )

    def _cost_budget_enabled(self) -> bool:
        return self._cost_tracking_state != "disabled"

    def _iteration_penalty_cost(self, iteration: int) -> float:
        threshold = max(0, self._config.max_iterations)
        penalty = max(0.0, self._config.iteration_penalty_cost)
        if threshold <= 0 or penalty <= 0.0 or iteration <= threshold:
            return 0.0
        return penalty

    @staticmethod
    def _budget_state(
        used: int | float,
        limit: int | float,
        *,
        enabled: bool | None = None,
    ) -> dict[str, int | float | bool | None]:
        limited = enabled if enabled is not None else limit > 0
        remaining: int | float | None = None
        if limited:
            remaining = max(0, limit - used)
        return {
            "used": used,
            "limit": limit if limited else None,
            "remaining": remaining,
            "limited": limited,
        }

    def _call_openai(self, messages: list[dict], tools_schema: list[dict]) -> LLMResponse:
        try:
            import openai
        except ImportError:
            raise ImportError("openai package is required. Run: uv add openai")

        client = openai.OpenAI(
            api_key=self._provider_api_key,
            base_url=self._config.api_base if self._config.api_base else None,
        )

        # Ensure tools match exact OpenAI spec (strip extra top-level keys if any)
        oai_tools = []
        for t in tools_schema:
            oai_tools.append({
                "type": "function",
                "function": {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "parameters": t["function"].get("parameters", {}),
                }
            })

        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "temperature": self._config.temperature,
        }
        
        # Add optional generation parameters
        if self._config.max_tokens:
            kwargs["max_tokens"] = self._config.max_tokens
        if self._config.top_p is not None:
            kwargs["top_p"] = self._config.top_p
        if self._config.frequency_penalty is not None:
            kwargs["frequency_penalty"] = self._config.frequency_penalty
        if self._config.presence_penalty is not None:
            kwargs["presence_penalty"] = self._config.presence_penalty
        
        if oai_tools:
            kwargs["tools"] = oai_tools
            kwargs["tool_choice"] = "auto"

        # Execute with retry
        raw_response = retry_with_exponential_backoff(
            client.chat.completions.create,
            max_retries=self._config.max_retries,
            initial_delay=self._config.retry_initial_delay,
            max_delay=self._config.retry_max_delay,
            backoff_factor=self._config.retry_backoff_factor,
            **kwargs
        )

        choice = raw_response.choices[0]
        msg = choice.message

        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
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
            input_tokens=getattr(raw_response.usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(raw_response.usage, "completion_tokens", 0) or 0,
            raw_message=raw_message,
        )

    def _call_anthropic(self, messages: list[dict], tools_schema: list[dict]) -> LLMResponse:
        try:
            import anthropic
        except ImportError:
            raise ImportError("anthropic package is required. Run: uv add anthropic")

        client = anthropic.Anthropic(
            api_key=self._provider_api_key,
            base_url=self._config.api_base if self._config.api_base else None,
        )

        # 1. Translate messages to Anthropic format
        system_text = ""
        anth_messages = []
        
        for m in messages:
            role = m["role"]
            if role == "system":
                system_text += m["content"] + "\n\n"
            elif role == "user":
                anth_messages.append({"role": "user", "content": m["content"]})
            elif role == "assistant":
                content = []
                if m.get("content"):
                    content.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls", []):
                    args = tc["function"]["arguments"]
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    content.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "input": args
                    })
                if content:
                    anth_messages.append({"role": "assistant", "content": content})
            elif role == "tool":
                # OpenAI uses 'tool' role for results. Anthropic uses 'user' containing a tool_result block.
                # In palimpsest, interactions currently only provide string `m["content"]`.
                content_val = m["content"]
                if not isinstance(content_val, list):
                    content_val = [{"type": "text", "text": str(content_val)}]
                
                anth_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id", ""),
                        "content": content_val
                    }]
                })

        # 2. Compress consecutive messages of the same role (Anthropic strict requirement)
        compressed = []
        for am in anth_messages:
            if not compressed:
                compressed.append(am)
                continue
            last = compressed[-1]
            if last["role"] == am["role"]:
                c1 = last["content"] if isinstance(last["content"], list) else [{"type": "text", "text": last["content"]}]
                c2 = am["content"] if isinstance(am["content"], list) else [{"type": "text", "text": am["content"]}]
                last["content"] = c1 + c2
            else:
                compressed.append(am)

        # 3. Translate tool schemas with optional cache_control
        anth_tools = []
        for t in tools_schema:
            tool_def = {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"].get("parameters", {}),
            }
            # Add cache_control if enabled
            if self._config.anthropic_cache_tools:
                tool_def["cache_control"] = {"type": "ephemeral"}
            anth_tools.append(tool_def)

        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "messages": compressed,
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,  # Required by Anthropic API
        }
        
        system_text = system_text.strip()
        if system_text:
            system_content: Any = system_text
            # Add cache_control to system message if enabled
            if self._config.anthropic_cache_system:
                system_content = [
                    {
                        "type": "text",
                        "text": system_text,
                        "cache_control": {"type": "ephemeral"}
                    }
                ]
            kwargs["system"] = system_content
            
        if anth_tools:
            kwargs["tools"] = anth_tools
            # Anthropic tool_choice
            kwargs["tool_choice"] = {"type": "auto"}

        # Execute with retry
        raw_response = retry_with_exponential_backoff(
            client.messages.create,
            max_retries=self._config.max_retries,
            initial_delay=self._config.retry_initial_delay,
            max_delay=self._config.retry_max_delay,
            backoff_factor=self._config.retry_backoff_factor,
            **kwargs
        )

        # Transform response back to generic format
        text = ""
        tool_calls = []
        raw_message = {"role": "assistant", "content": None, "tool_calls": []}
        
        for block in raw_response.content:
            if block.type == "text":
                text += block.text
            elif block.type == "tool_use":
                tc = {
                    "id": block.id,
                    "type": "function",
                    "function": {
                        "name": block.name,
                        "arguments": json.dumps(block.input)
                    }
                }
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))
                raw_message["tool_calls"].append(tc)
                
        raw_message["content"] = text if text else None
        
        return LLMResponse(
            text=text if text else None,
            tool_calls=tool_calls,
            finish_reason=raw_response.stop_reason or "unknown",
            input_tokens=getattr(raw_response.usage, "input_tokens", 0) or 0,
            output_tokens=getattr(raw_response.usage, "output_tokens", 0) or 0,
            raw_message=raw_message,
        )

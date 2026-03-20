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


class UnifiedLLMGateway(LLMGateway):
    """Unified Native LLM Gateway routing between OpenAI and Anthropic SDKs."""

    def __init__(self, config: LLMConfig, gateway: EventGateway):
        self._config = config
        self._gateway = gateway
        self._iteration = 0
        self._api_key = os.environ.get(config.api_key_env, "")

    def call(self, messages: list[dict], tools_schema: list[dict]) -> LLMResponse:
        self._iteration += 1

        self._gateway.emit(
            LLMRequestData(
                model=self._config.model,
                messages_count=len(messages),
                tools_count=len(tools_schema),
                iteration=self._iteration,
            )
        )

        start = time.monotonic_ns()

        if self._config.model.startswith("claude-"):
            response = self._call_anthropic(messages, tools_schema)
        else:
            response = self._call_openai(messages, tools_schema)

        duration_ms = (time.monotonic_ns() - start) // 1_000_000

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

    def _call_openai(self, messages: list[dict], tools_schema: list[dict]) -> LLMResponse:
        try:
            import openai
        except ImportError:
            raise ImportError("openai package is required. Run: uv add openai")

        client = openai.OpenAI(
            api_key=self._api_key or os.environ.get("OPENAI_API_KEY"),
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
        if oai_tools:
            kwargs["tools"] = oai_tools
            kwargs["tool_choice"] = "auto"

        raw_response = client.chat.completions.create(**kwargs)

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
            api_key=self._api_key or os.environ.get("ANTHROPIC_API_KEY"),
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

        # 3. Translate tool schemas
        anth_tools = []
        for t in tools_schema:
            anth_tools.append({
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"].get("parameters", {}),
            })

        kwargs: dict[str, Any] = {
            "model": self._config.model,
            "messages": compressed,
            "temperature": self._config.temperature,
            "max_tokens": 4096, # Required by Anthropic API
        }
        
        system_text = system_text.strip()
        if system_text:
            kwargs["system"] = system_text
            
        if anth_tools:
            kwargs["tools"] = anth_tools
            # Anthropic tool_choice
            kwargs["tool_choice"] = {"type": "auto"}

        raw_response = client.messages.create(**kwargs)

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

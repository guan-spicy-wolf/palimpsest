"""Tool gateway — unified tool execution with transparent event capture.

Part of the Runtime (skeleton).  All tools (builtin and evo) flow through
the same ``UnifiedToolGateway`` which wraps ``ToolProvider`` instances
with transparent event emission.

``bash`` and ``spawn`` are runtime-embedded builtin tools implemented as
a ``ToolProvider``.  They are added by default unless explicitly disabled.
"""

from __future__ import annotations

import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from loguru import logger

from palimpsest.config import ToolsConfig
from palimpsest.events import SpawnRequestData, ToolExecData, ToolResultData
from palimpsest.runtime.event_gateway import EventGateway
from palimpsest.runtime.interfaces import ToolProvider, ToolSpec


@dataclass
class ToolResult:
    success: bool
    output: str
    terminal: bool = False


class ToolGateway(ABC):
    """Abstract tool gateway."""

    @abstractmethod
    def execute(self, name: str, call_id: str, args: dict, workspace: str) -> ToolResult:
        pass

    @abstractmethod
    def schema(self) -> list[dict]:
        pass


# ---------------------------------------------------------------------------
# Builtin tool schemas (used by BuiltinToolProvider)
# ---------------------------------------------------------------------------

BUILTIN_TOOL_SCHEMAS = {
    "bash": {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a bash command in the workspace directory. Returns stdout+stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The bash command to run"},
                },
                "required": ["command"],
            },
        },
    },
    "spawn": {
        "type": "function",
        "function": {
            "name": "spawn",
            "description": (
                "Request the Supervisor to spawn child tasks.  This emits a "
                "spawn-request event; the Supervisor handles the actual "
                "fork-join orchestration externally."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "description": "List of child tasks to spawn",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {
                                    "type": "string",
                                    "description": "Role name for the child task (default: 'default')",
                                },
                                "task": {
                                    "type": "string",
                                    "description": "Task description for the child",
                                },
                                "repo": {
                                    "type": "string",
                                    "description": "Target repo URL (optional, defaults to current)",
                                },
                            },
                            "required": ["task"],
                        },
                    },
                    "wait_for": {
                        "type": "string",
                        "enum": ["all_complete", "any_failed"],
                        "description": "Trigger condition for resuming the parent (default: all_complete)",
                    },
                },
                "required": ["tasks"],
            },
        },
    },
}


class BuiltinToolProvider(ToolProvider):
    """Runtime-embedded tools (bash, spawn) implemented as a ToolProvider.

    ``bash`` executes commands locally.  ``spawn`` emits a spawn-request
    event — it does NOT run child tasks.
    """

    def __init__(self, config: ToolsConfig, gateway: EventGateway):
        self._config = config
        self._gateway = gateway
        self._disabled = set(config.disabled_builtins)

    def tools(self) -> list[ToolSpec]:
        specs = []
        for name, schema in BUILTIN_TOOL_SCHEMAS.items():
            if name not in self._disabled:
                fn = schema["function"]
                specs.append(ToolSpec(
                    name=fn["name"],
                    description=fn["description"],
                    parameters=fn["parameters"],
                ))
        return specs

    def as_provider_dict(self) -> dict[str, "BuiltinToolProvider"]:
        """Return a dict mapping each tool name to this provider (for merging)."""
        return {spec.name: self for spec in self.tools()}

    def execute(self, name: str, args: dict, workspace: str) -> ToolResult:
        if name in self._disabled:
            return ToolResult(success=False, output=f"Tool '{name}' is disabled")
        try:
            if name == "bash":
                return self._bash(args, workspace)
            elif name == "spawn":
                return self._spawn(args)
            else:
                return ToolResult(success=False, output=f"Unknown builtin tool: {name}")
        except Exception as exc:
            logger.error(f"Tool {name} failed: {exc}")
            return ToolResult(success=False, output=f"Tool error: {exc}")

    def _bash(self, args: dict, workspace: str) -> ToolResult:
        cfg = self._config.builtin.get("bash", {})
        timeout = cfg.get("timeout", 60)
        output_limit = cfg.get("output_limit", 4096)

        try:
            result = subprocess.run(
                args["command"],
                shell=True,
                capture_output=True,
                text=True,
                cwd=workspace,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, output=f"Command timed out ({timeout}s)")

        output = (result.stdout or "") + (result.stderr or "")
        return ToolResult(success=result.returncode == 0, output=output[:output_limit])

    def _spawn(self, args: dict) -> ToolResult:
        """Emit a spawn-request event. The Supervisor handles the rest."""
        tasks = args.get("tasks", [])
        if not tasks:
            return ToolResult(success=False, output="No tasks provided to spawn")

        wait_for = args.get("wait_for", "all_complete")

        self._gateway.emit_spawn_request(
            SpawnRequestData(
                tasks=tasks,
                wait_for=wait_for,
            )
        )

        return ToolResult(
            success=True,
            output=(
                f"Spawn request emitted for {len(tasks)} child task(s) "
                f"(wait_for={wait_for}). The Supervisor will handle orchestration."
            ),
        )


# ---------------------------------------------------------------------------
# Unified tool gateway — single execution path for all tools
# ---------------------------------------------------------------------------

class UnifiedToolGateway(ToolGateway):
    """Wraps ToolProvider instances into a single gateway with event capture.

    All tools (builtin and evo) flow through this gateway. Event wrapping
    (ToolExecData/ToolResultData) is handled once, here.
    """

    def __init__(
        self,
        providers: dict[str, ToolProvider],
        gateway: EventGateway,
    ):
        self._providers = providers
        self._gateway = gateway
        # Pre-build schema list
        self._schemas: list[dict] = []
        seen: set[str] = set()
        for name, provider in providers.items():
            if name in seen:
                continue
            for spec in provider.tools():
                if spec.name == name:
                    self._schemas.append({
                        "type": "function",
                        "function": {
                            "name": spec.name,
                            "description": spec.description,
                            "parameters": spec.parameters,
                        },
                    })
                    seen.add(name)

    def schema(self) -> list[dict]:
        return self._schemas

    def execute(self, name: str, call_id: str, args: dict, workspace: str) -> ToolResult:
        provider = self._providers.get(name)
        if not provider:
            return ToolResult(success=False, output=f"Unknown tool: {name}")

        self._gateway.emit_tool_exec(
            ToolExecData(
                tool_name=name,
                tool_call_id=call_id,
                arguments_preview=str(args)[:256],
            )
        )

        start = time.monotonic_ns()
        try:
            result = provider.execute(name, args, workspace)
        except Exception as exc:
            logger.error(f"Tool {name} raised: {exc}")
            result = ToolResult(success=False, output=f"Tool error: {exc}")

        duration_ms = (time.monotonic_ns() - start) // 1_000_000

        self._gateway.emit_tool_result(
            ToolResultData(
                tool_name=name,
                tool_call_id=call_id,
                success=result.success,
                duration_ms=duration_ms,
                output_preview=result.output[:256],
            )
        )

        return result


def find_duplicate_tool_names(providers: dict[str, ToolProvider], *more: dict[str, ToolProvider]) -> list[str]:
    """Return duplicate tool names across provider dicts."""
    all_dicts = [providers] + list(more)
    seen: set[str] = set()
    duplicates: set[str] = set()
    for d in all_dicts:
        for name in d:
            if name in seen:
                duplicates.add(name)
            else:
                seen.add(name)
    return sorted(duplicates)

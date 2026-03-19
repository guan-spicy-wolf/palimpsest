"""Built-in tool gateway — runtime-embedded tools.

Part of the Runtime (skeleton).  Tool execution events are captured
transparently through the EventGateway.

``bash`` is embedded in the runtime.  ``spawn`` emits a spawn-request
event for the external Supervisor — the runtime does NOT execute child
tasks itself.  All other tools are defined in the evolvable repository
and loaded by the tool resolver.
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


class CompositeToolGateway(ToolGateway):
    """Composes multiple tool gateways into a single interface."""

    def __init__(self, gateways: list[ToolGateway]):
        self._gateways = gateways
        self._dispatch: dict[str, ToolGateway] = {}
        for gw in gateways:
            for s in gw.schema():
                name = s["function"]["name"]
                self._dispatch.setdefault(name, gw)

    def schema(self) -> list[dict]:
        schemas = []
        for gw in self._gateways:
            schemas.extend(gw.schema())
        return schemas

    def execute(self, name: str, call_id: str, args: dict, workspace: str) -> ToolResult:
        gw = self._dispatch.get(name)
        if gw:
            return gw.execute(name, call_id, args, workspace)
        return ToolResult(success=False, output=f"Unknown tool: {name}")


def find_duplicate_tool_names(gateways: list[ToolGateway]) -> list[str]:
    """Return duplicate tool names across gateways."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for gw in gateways:
        for schema in gw.schema():
            name = schema["function"]["name"]
            if name in seen:
                duplicates.add(name)
            else:
                seen.add(name)
    return sorted(duplicates)


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


class BuiltinToolGateway(ToolGateway):
    """Runtime-embedded tools with transparent event capture.

    ``bash`` executes commands locally.  ``spawn`` emits a spawn-request
    event — it does NOT run child tasks.
    """

    def __init__(
        self,
        config: ToolsConfig,
        gateway: EventGateway,
        job_id: str,
    ):
        self._config = config
        self._gateway = gateway
        self._job_id = job_id
        self._disabled = set(config.disabled_builtins)

    def schema(self) -> list[dict]:
        return [
            s for name, s in BUILTIN_TOOL_SCHEMAS.items()
            if name not in self._disabled
        ]

    def execute(self, name: str, call_id: str, args: dict, workspace: str) -> ToolResult:
        if name in self._disabled:
            return ToolResult(success=False, output=f"Tool '{name}' is disabled")

        self._gateway.emit_tool_exec(
            ToolExecData(
                job_id=self._job_id,
                tool_name=name,
                tool_call_id=call_id,
                arguments_preview=str(args)[:256],
            )
        )

        start = time.monotonic_ns()
        result = self._dispatch(name, args, workspace)
        duration_ms = (time.monotonic_ns() - start) // 1_000_000

        self._gateway.emit_tool_result(
            ToolResultData(
                job_id=self._job_id,
                tool_name=name,
                tool_call_id=call_id,
                success=result.success,
                duration_ms=duration_ms,
                output_preview=result.output[:256],
            )
        )

        return result

    def _dispatch(self, name: str, args: dict, workspace: str) -> ToolResult:
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
                job_id=self._job_id,
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

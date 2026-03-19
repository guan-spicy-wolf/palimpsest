"""Built-in tool gateway — runtime-embedded tools (bash, spawn).

Part of the Runtime (skeleton).  Tool execution events are captured
transparently through the EventGateway.

Only ``bash`` and ``spawn`` are embedded in the runtime.  All other
tools (read_file, write_file, list_files, …) are defined as YAML
files in the evolvable repository and loaded by ``YamlToolLoader``.
"""

from __future__ import annotations

import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from loguru import logger

from palimpsest.config import ToolsConfig
from palimpsest.events import ToolExecData, ToolResultData
from palimpsest.runtime.event_gateway import EventGateway

from typing import Callable

# Type alias for the spawn callback injected by the Supervisor.
# Signature: (parent_job_id, tasks, wait_for) -> list[dict]
SpawnCallback = Callable[..., list[dict]]


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
    """Composes multiple tool gateways into a single interface.

    Schemas are merged from all sub-gateways.
    """

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
                "Spawn child tasks via Supervisor fork-join. Each child runs as "
                "an independent Job with its own Role. The parent is suspended "
                "until the wait condition is met."
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


# Maximum retries for transient tool failures (subprocess timeout, I/O errors)
_MAX_TOOL_RETRIES = 2


class BuiltinToolGateway(ToolGateway):
    """Runtime-embedded tools (bash, spawn) with transparent event capture.

    All other tools are loaded from evo via YamlToolLoader and composed
    externally by the runner.
    """

    def __init__(
        self,
        config: ToolsConfig,
        gateway: EventGateway,
        job_id: str,
        spawn_callback: SpawnCallback | None = None,
    ):
        self._config = config
        self._gateway = gateway
        self._job_id = job_id
        self._spawn_callback = spawn_callback
        self._disabled = set(config.disabled_builtins)

    def schema(self) -> list[dict]:
        return [
            s for name, s in BUILTIN_TOOL_SCHEMAS.items()
            if name not in self._disabled
        ]

    def execute(self, name: str, call_id: str, args: dict, workspace: str) -> ToolResult:
        if name in self._disabled:
            return ToolResult(success=False, output=f"Tool '{name}' is disabled")

        # Transparent event: tool execution start
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

        # Transparent event: tool execution result
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
                return self._spawn(args, workspace)
            else:
                return ToolResult(success=False, output=f"Unknown builtin tool: {name}")
        except Exception as exc:
            logger.error(f"Tool {name} failed: {exc}")
            return ToolResult(success=False, output=f"Tool error: {exc}")

    def _bash(self, args: dict, workspace: str) -> ToolResult:
        cfg = self._config.builtin.get("bash", {})
        timeout = cfg.get("timeout", 60)
        output_limit = cfg.get("output_limit", 4096)

        # Retry logic for transient failures
        max_attempts = _MAX_TOOL_RETRIES + 1
        last_result: ToolResult | None = None

        for attempt in range(max_attempts):
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
                last_result = ToolResult(success=False, output=f"Command timed out ({timeout}s)")
                if attempt < max_attempts - 1:
                    delay = 2 ** attempt
                    logger.warning(f"bash timed out (attempt {attempt + 1}), retrying in {delay}s")
                    time.sleep(delay)
                    continue
                return last_result

            output = (result.stdout or "") + (result.stderr or "")
            tool_result = ToolResult(success=result.returncode == 0, output=output[:output_limit])
            if tool_result.success or attempt == max_attempts - 1:
                return tool_result

            last_result = tool_result
            delay = 2 ** attempt
            logger.warning(
                f"bash failed (attempt {attempt + 1}/{max_attempts}), "
                f"retrying in {delay}s: {tool_result.output[:120]}"
            )
            time.sleep(delay)

        return last_result or ToolResult(success=False, output="Retry exhausted")

    def _spawn(self, args: dict, workspace: str) -> ToolResult:
        """Spawn child tasks via the Supervisor callback."""
        tasks = args.get("tasks", [])
        if not tasks:
            return ToolResult(success=False, output="No tasks provided to spawn")

        wait_for = args.get("wait_for", "all_complete")

        if self._spawn_callback is None:
            return ToolResult(
                success=False,
                output="Spawn not available: no supervisor configured for this job",
            )

        try:
            child_results = self._spawn_callback(
                parent_job_id=self._job_id,
                tasks=tasks,
                wait_for=wait_for,
            )
            summaries = []
            for i, cr in enumerate(child_results):
                status = cr.get("status", "unknown")
                summary = cr.get("summary", "")[:200]
                summaries.append(f"  [{i+1}] {status}: {summary}")

            return ToolResult(
                success=all(cr.get("status") == "success" for cr in child_results),
                output=f"Spawned {len(tasks)} child tasks ({wait_for}):\n" + "\n".join(summaries),
            )
        except Exception as exc:
            logger.error(f"Spawn failed: {exc}")
            return ToolResult(success=False, output=f"Spawn error: {exc}")

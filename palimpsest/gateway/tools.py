"""Built-in tool gateway — executes tools within the Runtime sandbox.

Part of the Runtime (skeleton).  Tool execution events are captured
transparently through the EventGateway.  The Agent only sees tool
results, never the event emission.

Permission enforcement is applied via the PermissionLayer on all
file-system operations (read_file, write_file).
"""

from __future__ import annotations

import os
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from palimpsest.config import ToolsConfig
from palimpsest.events import ToolExecData, ToolResultData
from palimpsest.runtime.event_gateway import EventGateway
from palimpsest.runtime.permissions import PermissionLayer

from typing import Callable

# Type alias for the spawn callback injected by the Supervisor.
# Signature: (parent_job_id, tasks, wait_for) -> list[dict]
SpawnCallback = Callable[..., list[dict]]


@dataclass
class ToolResult:
    success: bool
    output: str
    is_terminal: bool = False
    terminal_data: dict | None = None


class ToolGateway(ABC):
    """Abstract tool gateway."""

    @abstractmethod
    def execute(self, name: str, call_id: str, args: dict, workspace: str) -> ToolResult:
        pass

    @abstractmethod
    def schema(self) -> list[dict]:
        pass


TOOL_SCHEMAS = [
    {
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
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to workspace root"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file in the workspace (overwrites if exists).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to workspace root"},
                    "content": {"type": "string", "description": "File content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a directory within the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path relative to workspace root (default: .)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_complete",
            "description": "Signal that the task is complete. Call this when done.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of what was accomplished",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["success", "partial"],
                        "description": "Whether the task was fully completed or only partially",
                    },
                },
                "required": ["summary", "status"],
            },
        },
    },
    {
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
]


# Maximum retries for transient tool failures (subprocess timeout, I/O errors)
_MAX_TOOL_RETRIES = 2
_RETRYABLE_TOOLS = {"bash"}


class BuiltinToolGateway(ToolGateway):
    """Built-in tools with permission enforcement and retry support.

    Event emission is transparent — the Agent only receives ToolResult objects.
    """

    def __init__(
        self,
        config: ToolsConfig,
        gateway: EventGateway,
        job_id: str,
        permissions: PermissionLayer | None = None,
        spawn_callback: SpawnCallback | None = None,
    ):
        self._config = config
        self._gateway = gateway
        self._job_id = job_id
        self._permissions = permissions
        self._spawn_callback = spawn_callback

    def schema(self) -> list[dict]:
        return TOOL_SCHEMAS

    def execute(self, name: str, call_id: str, args: dict, workspace: str) -> ToolResult:
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
        result = self._execute_with_retry(name, args, workspace)
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

    def _execute_with_retry(self, name: str, args: dict, workspace: str) -> ToolResult:
        """Execute a tool, retrying transient failures for eligible tools."""
        max_attempts = _MAX_TOOL_RETRIES + 1 if name in _RETRYABLE_TOOLS else 1
        last_result: ToolResult | None = None

        for attempt in range(max_attempts):
            result = self._dispatch(name, args, workspace)
            if result.success or attempt == max_attempts - 1:
                return result
            last_result = result
            delay = 2 ** attempt
            logger.warning(
                f"Tool {name} failed (attempt {attempt + 1}/{max_attempts}), "
                f"retrying in {delay}s: {result.output[:120]}"
            )
            time.sleep(delay)

        return last_result or ToolResult(success=False, output="Retry exhausted")

    def _dispatch(self, name: str, args: dict, workspace: str) -> ToolResult:
        try:
            if name == "bash":
                return self._bash(args, workspace)
            elif name == "read_file":
                return self._read_file(args, workspace)
            elif name == "write_file":
                return self._write_file(args, workspace)
            elif name == "list_files":
                return self._list_files(args, workspace)
            elif name == "task_complete":
                return self._task_complete(args, workspace)
            elif name == "spawn":
                return self._spawn(args, workspace)
            else:
                return ToolResult(success=False, output=f"Unknown tool: {name}")
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

    def _read_file(self, args: dict, workspace: str) -> ToolResult:
        cfg = self._config.builtin.get("read_file", {})
        output_limit = cfg.get("output_limit", 8192)

        fpath = _safe_path(workspace, args["path"])

        if self._permissions and not self._permissions.check_read(fpath):
            return ToolResult(
                success=False,
                output=f"Permission denied: cannot read {args['path']} (locked tier)",
            )

        content = Path(fpath).read_text(errors="replace")
        return ToolResult(success=True, output=content[:output_limit])

    def _write_file(self, args: dict, workspace: str) -> ToolResult:
        fpath = _safe_path(workspace, args["path"])

        if self._permissions and not self._permissions.check_modify(fpath):
            return ToolResult(
                success=False,
                output=f"Permission denied: cannot modify {args['path']} (not in free tier)",
            )

        Path(fpath).parent.mkdir(parents=True, exist_ok=True)
        Path(fpath).write_text(args["content"])
        return ToolResult(success=True, output=f"Written {len(args['content'])} chars to {args['path']}")

    def _list_files(self, args: dict, workspace: str) -> ToolResult:
        rel_path = args.get("path", ".")
        dpath = _safe_path(workspace, rel_path)
        if not os.path.isdir(dpath):
            return ToolResult(success=False, output=f"Not a directory: {rel_path}")
        entries = sorted(os.listdir(dpath))
        return ToolResult(success=True, output="\n".join(entries))

    def _task_complete(self, args: dict, workspace: str) -> ToolResult:
        return ToolResult(
            success=True,
            output="Task marked as complete.",
            is_terminal=True,
            terminal_data={"status": args.get("status", "success"), "summary": args.get("summary", "")},
        )

    def _spawn(self, args: dict, workspace: str) -> ToolResult:
        """Spawn child tasks via the Supervisor callback.

        The spawn tool is a *stable* interface — the Agent can use it
        but cannot modify its implementation.  The actual orchestration
        is handled by the Runtime's spawn callback.
        """
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
                is_terminal=False,
            )
        except Exception as exc:
            logger.error(f"Spawn failed: {exc}")
            return ToolResult(success=False, output=f"Spawn error: {exc}")


def _safe_path(workspace: str, rel_path: str) -> str:
    """Prevent path traversal using strict path containment.

    Uses ``Path.relative_to()`` instead of string-prefix comparison to
    avoid adjacent-directory bypass (e.g. ``/workspace-evil`` matching
    ``/workspace``).
    """
    real_workspace = Path(os.path.realpath(workspace))
    abs_path = Path(os.path.realpath(os.path.join(workspace, rel_path)))
    try:
        abs_path.relative_to(real_workspace)
    except ValueError:
        raise ValueError(f"Path traversal attempt: {rel_path}")
    return str(abs_path)

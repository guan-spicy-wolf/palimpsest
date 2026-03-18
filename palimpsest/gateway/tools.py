"""Built-in tool gateway — executes tools within the Runtime sandbox.

Part of the Runtime (skeleton).  Tool execution events are captured
transparently through the EventGateway.  The Agent only sees tool
results, never the event emission.
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
]


class BuiltinToolGateway(ToolGateway):
    """Built-in tools: bash, read_file, write_file, list_files, task_complete.

    Event emission is transparent — the Agent only receives ToolResult objects.
    """

    def __init__(self, config: ToolsConfig, gateway: EventGateway, job_id: str):
        self._config = config
        self._gateway = gateway
        self._job_id = job_id

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
            elif name == "read_file":
                return self._read_file(args, workspace)
            elif name == "write_file":
                return self._write_file(args, workspace)
            elif name == "list_files":
                return self._list_files(args, workspace)
            elif name == "task_complete":
                return self._task_complete(args, workspace)
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
        content = Path(fpath).read_text(errors="replace")
        return ToolResult(success=True, output=content[:output_limit])

    def _write_file(self, args: dict, workspace: str) -> ToolResult:
        fpath = _safe_path(workspace, args["path"])
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


def _safe_path(workspace: str, rel_path: str) -> str:
    """Prevent path traversal."""
    abs_path = os.path.realpath(os.path.join(workspace, rel_path))
    if not abs_path.startswith(os.path.realpath(workspace)):
        raise ValueError(f"Path traversal attempt: {rel_path}")
    return abs_path

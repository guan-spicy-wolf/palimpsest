"""Tool gateway — unified tool execution with transparent event capture.

Part of the Runtime (skeleton). All tools (builtin and evo) flow through
the same ``UnifiedToolGateway`` which wraps pure functions with transparent
event emission.
"""

from __future__ import annotations

import importlib.util
import inspect
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, get_type_hints

import git
from loguru import logger

from palimpsest.config import ToolsConfig
from palimpsest.events import SpawnRequestData, SpawnTaskData, ToolExecData, ToolResultData
from palimpsest.runtime.event_gateway import EventGateway


@dataclass
class ToolResult:
    success: bool
    output: str
    terminal: bool = False


# ---------------------------------------------------------------------------
# Introspection & @tool decorator
# ---------------------------------------------------------------------------

def _python_type_to_json_type(py_type: Any) -> str:
    if py_type == str: return "string"
    if py_type == int: return "integer"
    if py_type == float: return "number"
    if py_type == bool: return "boolean"
    if py_type == list: return "array"
    if py_type == dict: return "object"
    return "string"


def _function_to_schema(func: Callable) -> dict:
    """Generate JSON schema from function signature and docstring."""
    sig = inspect.signature(func)
    hints = get_type_hints(func)
    
    doc = inspect.getdoc(func) or ""
    description = doc.split("\n\n")[0].strip() if doc else func.__name__

    properties = {}
    required = []

    # Exclude injected runtime dependencies from schema
    injected_args = {"workspace", "gateway", "evo_root"}

    for name, param in sig.parameters.items():
        if name in injected_args:
            continue
            
        py_type = hints.get(name, str)
        json_type = _python_type_to_json_type(py_type)
        
        prop = {"type": json_type}
        # In a more advanced implementation we could parse the Args: section of docstring
        # for param descriptions. For now, we omit individual param descriptions.
        properties[name] = prop
        
        if param.default == inspect.Parameter.empty:
            required.append(name)

    return {
        "type": "function",
        "function": {
            "name": func.__name__,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def tool(func: Callable) -> Callable:
    """Decorator to mark a function as a tool and generate its schema."""
    func.__is_tool__ = True
    func.__tool_schema__ = _function_to_schema(func)
    return func


# ---------------------------------------------------------------------------
# Built-in tools
# ---------------------------------------------------------------------------

@tool
def bash(command: str, workspace: str, config: ToolsConfig | None = None) -> ToolResult:
    """Run a bash command in the workspace directory. Returns stdout+stderr."""
    # Get timeout and output limit from config or use defaults
    if config and "bash" in config.builtin:
        tool_config = config.builtin["bash"]
        timeout = tool_config.get("timeout", 60)
        output_limit = tool_config.get("output_limit", 4096)
    else:
        timeout = 60
        output_limit = 4096
        
    try:
        result = subprocess.run(
            command,
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


@tool
def task_complete(summary: str, status: str = "success") -> ToolResult:
    """Signal that the task is complete. Always call this when done.

    Args:
        summary: Brief summary of what was accomplished.
        status: 'success' if fully complete, 'partial' if only partially done.
    """
    return ToolResult(success=True, output=f"[{status}] {summary}", terminal=True)


def _infer_spawn_job_defaults(workspace: str, evo_sha: str) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "repo": "",
        "init_branch": "",
        "role": "default",
        "evo_sha": evo_sha,
        "llm": {},
        "workspace": {},
        "publication": {},
    }
    try:
        repo = git.Repo(workspace)
    except Exception:
        return defaults

    if repo.remotes:
        defaults["repo"] = repo.remotes[0].url
    try:
        defaults["init_branch"] = repo.active_branch.name
    except Exception:
        defaults["init_branch"] = ""
    return defaults


def _normalize_spawn_task(task: dict[str, Any], *, workspace: str, evo_sha: str) -> SpawnTaskData:
    if not isinstance(task, dict):
        raise ValueError("Each spawn task must be an object")

    prompt = str(task.get("prompt") or task.get("task") or "").strip()
    if not prompt:
        raise ValueError("Each spawn task requires a non-empty prompt")

    defaults = _infer_spawn_job_defaults(workspace, evo_sha)
    job_spec = dict(task.get("job_spec") or {})

    role = task.get("role")
    if not role and task.get("role_file"):
        role_file = str(task["role_file"])
        role = role_file.removeprefix("roles/").removesuffix(".py")

    if task.get("repo") and not job_spec.get("repo"):
        job_spec["repo"] = task["repo"]
    if task.get("init_branch") and not job_spec.get("init_branch"):
        job_spec["init_branch"] = task["init_branch"]
    if task.get("branch") and not job_spec.get("init_branch"):
        job_spec["init_branch"] = task["branch"]
    if role and not job_spec.get("role"):
        job_spec["role"] = role
    if task.get("evo_sha") and not job_spec.get("evo_sha"):
        job_spec["evo_sha"] = task["evo_sha"]
    if task.get("role_sha") and not job_spec.get("evo_sha"):
        job_spec["evo_sha"] = task["role_sha"]

    for key in ("llm", "workspace", "publication"):
        if isinstance(task.get(key), dict) and not job_spec.get(key):
            job_spec[key] = dict(task[key])

    normalized_job_spec = {
        "repo": job_spec.get("repo") or defaults["repo"],
        "init_branch": job_spec.get("init_branch") or defaults["init_branch"],
        "role": job_spec.get("role") or defaults["role"],
        "evo_sha": job_spec.get("evo_sha") or defaults["evo_sha"],
        "llm": dict(job_spec.get("llm") or defaults["llm"]),
        "workspace": dict(job_spec.get("workspace") or defaults["workspace"]),
        "publication": dict(job_spec.get("publication") or defaults["publication"]),
    }

    return SpawnTaskData(prompt=prompt, job_spec=normalized_job_spec)


@tool
def spawn(
    tasks: list,
    workspace: str,
    gateway: EventGateway,
    evo_root: str,
    wait_for: str = "all_complete",
) -> ToolResult:
    """Request the Supervisor to spawn child tasks.
    
    tasks: List of child tasks to spawn.
    wait_for: Trigger condition ('all_complete' or 'any_failed').
    """
    if not tasks:
        return ToolResult(success=False, output="No tasks provided to spawn")

    try:
        evo_sha = git.Repo(Path(evo_root)).head.commit.hexsha
    except Exception:
        evo_sha = ""

    normalized_tasks: list[SpawnTaskData] = []
    try:
        for task in tasks:
            normalized_tasks.append(
                _normalize_spawn_task(task, workspace=workspace, evo_sha=evo_sha)
            )
    except ValueError as exc:
        return ToolResult(success=False, output=str(exc))

    gateway.emit(
        SpawnRequestData(
            tasks=normalized_tasks,
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
# Tool Loader
# ---------------------------------------------------------------------------

def _load_tool_functions(py_path: Path) -> dict[str, Callable]:
    """Load a .py file in isolated scope and extract @tool functions."""
    module_name = f"_evo_tools_{py_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, py_path)
    if spec is None or spec.loader is None:
        return {}

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        logger.error(f"Failed to load tools from {py_path}: {exc}")
        return {}

    funcs = {}
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if callable(attr) and getattr(attr, "__is_tool__", False):
            funcs[attr.__name__] = attr
    return funcs


def resolve_tool_functions(
    evo_root: str | Path,
    requested: list[str],
) -> dict[str, Callable]:
    """Scan evo/tools/*.py and return requested @tool functions."""
    scan_dir = Path(evo_root) / "tools"
    if not scan_dir.is_dir():
        logger.warning(f"Tool directory not found: {scan_dir}")
        return {}

    requested_set = set(requested)
    result: dict[str, Callable] = {}

    for py_file in sorted(scan_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
            
        funcs = _load_tool_functions(py_file)
        for name, func in funcs.items():
            if name in requested_set:
                result[name] = func

    missing = requested_set - set(result.keys())
    if missing:
        logger.warning(f"Tools not found in {scan_dir}: {missing}")

    return result


def find_duplicate_tool_names(*dicts: dict[str, Callable]) -> list[str]:
    """Return duplicate tool names across provider dicts."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for d in dicts:
        for name in d:
            if name in seen:
                duplicates.add(name)
            else:
                seen.add(name)
    return sorted(duplicates)


# ---------------------------------------------------------------------------
# Unified Tool Gateway
# ---------------------------------------------------------------------------

class UnifiedToolGateway:
    """Wraps pure tool functions into a single gateway with event capture."""

    def __init__(
        self,
        config: ToolsConfig,
        evo_root: Path,
        requested_evo_tools: list[str],
        gateway: EventGateway,
    ):
        self._gateway = gateway
        self._config = config
        self._evo_root = evo_root
        
        # Load builtins
        disabled = set(config.disabled_builtins)
        self._functions: dict[str, Callable] = {}
        
        if "bash" not in disabled:
            # Wrap bash with config injection
            def bash_with_config(command: str, workspace: str) -> ToolResult:
                return bash(command, workspace, config=self._config)
            bash_with_config.__tool_schema__ = bash.__tool_schema__
            bash_with_config.__is_tool__ = True
            self._functions["bash"] = bash_with_config
        if "spawn" not in disabled:
            self._functions["spawn"] = spawn
        # task_complete is always available — it's a runtime control signal
        self._functions["task_complete"] = task_complete

        # Load evo tools
        evo_funcs = resolve_tool_functions(evo_root, requested_evo_tools)
        
        dups = find_duplicate_tool_names(self._functions, evo_funcs)
        if dups:
            raise ValueError("Duplicate tool names configured: " + ", ".join(dups))
            
        self._functions.update(evo_funcs)
        
        # Pre-build schemas
        self._schemas = [func.__tool_schema__ for func in self._functions.values()]

    def schema(self) -> list[dict]:
        return self._schemas

    def execute(self, name: str, call_id: str, args: dict, workspace: str) -> ToolResult:
        func = self._functions.get(name)
        if not func:
            return ToolResult(success=False, output=f"Unknown tool: {name}")

        self._gateway.emit(
            ToolExecData(
                tool_name=name,
                tool_call_id=call_id,
                arguments_preview=str(args)[:256],
            )
        )

        start = time.monotonic_ns()
        try:
            # Inject runtime dependencies if the tool requested them
            sig = inspect.signature(func)
            kwargs = dict(args)
            if "workspace" in sig.parameters:
                kwargs["workspace"] = workspace
            if "gateway" in sig.parameters and getattr(func, "__module__", "").startswith("palimpsest.runtime"):
                kwargs["gateway"] = self._gateway
            if "evo_root" in sig.parameters:
                kwargs["evo_root"] = str(self._evo_root)

            result = func(**kwargs)
            
            # Allow pure functions to return strings directly instead of ToolResult
            if not isinstance(result, ToolResult):
                result = ToolResult(success=True, output=str(result))
                
        except Exception as exc:
            logger.error(f"Tool {name} raised: {exc}")
            result = ToolResult(success=False, output=f"Tool error: {exc}")

        duration_ms = (time.monotonic_ns() - start) // 1_000_000

        self._gateway.emit(
            ToolResultData(
                tool_name=name,
                tool_call_id=call_id,
                success=result.success,
                duration_ms=duration_ms,
                output_preview=result.output[:256],
            )
        )

        return result

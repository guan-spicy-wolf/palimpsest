"""Tool gateway — unified tool execution with transparent event capture.

Part of the Runtime (skeleton). All tools (builtin and evo) flow through
the same ``UnifiedToolGateway`` which wraps pure functions with transparent
event emission.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, get_type_hints
from urllib.parse import urlparse

import git
import httpx
from loguru import logger

from palimpsest.config import ToolsConfig
from palimpsest.events import EvalSpec, SpawnRequestData, SpawnTaskData, ToolExecData, ToolResultData
from palimpsest.runtime.event_gateway import EventGateway

BUILTIN_TOOL_NAMES = {"bash", "spawn", "create_pr"}


@dataclass
class ToolResult:
    success: bool
    output: str


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
    injected_args = {"workspace", "gateway", "evo_root", "evo_sha"}

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

    prompt = str(task.get("prompt") or task.get("goal") or task.get("task") or "").strip()
    if not prompt:
        raise ValueError("Each spawn task requires a non-empty goal/prompt")

    defaults = _infer_spawn_job_defaults(workspace, evo_sha)

    role = task.get("role") or task.get("role_fn")
    if not role and task.get("role_file"):
        role_file = str(task["role_file"])
        role = role_file.removeprefix("roles/").removesuffix(".py")

    budget = task.get("budget")

    eval_spec = task.get("eval_spec")
    normalized_eval_spec = EvalSpec.model_validate(eval_spec) if isinstance(eval_spec, dict) else None
    return SpawnTaskData(
        prompt=prompt,
        goal=prompt,
        role=str(role or defaults["role"]),
        budget=float(budget) if isinstance(budget, (int, float)) else 0.0,
        sha=task.get("sha") or task.get("evo_sha") or task.get("role_sha") or defaults["evo_sha"] or None,
        params={
            **({"repo": task["repo"]} if task.get("repo") else ({ "repo": defaults["repo"] } if defaults["repo"] else {})),
            **({"branch": task["branch"]} if task.get("branch") else {}),
            **({"init_branch": task["init_branch"]} if task.get("init_branch") else {}),
            **({k: v for k, v in task.items() if k not in {"prompt", "goal", "task", "role", "role_fn", "role_file", "budget", "eval_spec", "sha", "evo_sha", "role_sha"}}),
        },
        eval_spec=normalized_eval_spec,
    )


_SPAWN_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "spawn",
        "description": "Request the Supervisor to spawn child tasks. Each child runs in an isolated git clone; the runtime auto-commits and pushes on success.",
        "parameters": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": "List of child tasks to spawn.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "prompt": {
                                "type": "string",
                                "description": "Concrete task description for the child agent.",
                            },
                            "role": {
                                "type": "string",
                                "description": "Team role to assign (e.g. 'implementer', 'reviewer').",
                            },
                            "budget": {
                                "type": "number",
                                "description": "Cost budget for the child job.",
                            },
                            "repo": {
                                "type": "string",
                                "description": "Git repository URL for the child workspace.",
                            },
                            "init_branch": {
                                "type": "string",
                                "description": "Branch to clone from.",
                            },
                            "eval_spec": {
                                "type": "object",
                                "description": "Evaluation specification for the child task.",
                                "properties": {
                                    "deliverables": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "Tangible outputs expected from the task.",
                                    },
                                    "criteria": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "How the task should be verified.",
                                    },
                                },
                            },
                        },
                        "required": ["prompt", "role"],
                    },
                },
                "wait_for": {
                    "type": "string",
                    "description": "Join condition: 'all_complete' (default) or 'any_success'.",
                },
                "on_fail": {
                    "type": "string",
                    "description": "Failure policy: 'continue' (default) or 'cancel_siblings'.",
                },
            },
            "required": ["tasks"],
        },
    },
}


@tool
def spawn(
    tasks: list,
    workspace: str,
    gateway: EventGateway,
    evo_root: str,
    evo_sha: str = "",
    wait_for: str = "all_complete",
    on_fail: str = "continue",
) -> ToolResult:
    """Request the Supervisor to spawn child tasks. Each child runs in an isolated git clone; the runtime auto-commits and pushes on success."""
    if not tasks:
        return ToolResult(success=False, output="No tasks provided to spawn")

    if not evo_sha:
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
            on_fail=on_fail,
        )
    )

    return ToolResult(
        success=True,
        output=(
            f"Spawn request emitted for {len(tasks)} child task(s) "
            f"(wait_for={wait_for}, on_fail={on_fail}). "
            "The Supervisor will handle orchestration."
        ),
    )

# Override the auto-generated schema with the hand-crafted one that includes
# items definitions for the tasks array, so models know the expected shape.
spawn.__tool_schema__ = _SPAWN_SCHEMA


def _github_repo_slug(repo: str) -> tuple[str, str]:
    text = (repo or "").strip()
    if not text:
        raise ValueError("repo is required")

    path = ""
    if text.startswith("git@github.com:"):
        path = text.split(":", 1)[1]
    else:
        parsed = urlparse(text)
        host = (parsed.hostname or "").lower()
        if host != "github.com":
            raise ValueError(f"Unsupported repository host: {host or text}")
        path = parsed.path.lstrip("/")

    path = path.removesuffix(".git").strip("/")
    match = re.fullmatch(r"([^/]+)/([^/]+)", path)
    if not match:
        raise ValueError(f"Could not parse GitHub repository slug from: {repo}")
    return match.group(1), match.group(2)


def _github_token(git_token_env: str = "") -> tuple[str, str]:
    candidates = []
    if git_token_env:
        candidates.append(git_token_env)
    candidates.extend(["GITHUB_TOKEN", "GH_TOKEN"])

    seen: set[str] = set()
    for name in candidates:
        if not name or name in seen:
            continue
        seen.add(name)
        value = os.environ.get(name, "").strip()
        if value:
            return value, name
    raise ValueError("No GitHub token found. Set git_token_env, GITHUB_TOKEN, or GH_TOKEN.")


_CREATE_PR_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "create_pr",
        "description": (
            "Create a GitHub pull request from an existing branch. "
            "Use this in planner join mode after a child task passed eval."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "GitHub repository URL, such as https://github.com/org/repo or git@github.com:org/repo.git.",
                },
                "head_branch": {
                    "type": "string",
                    "description": "Existing branch containing the work to review.",
                },
                "base_branch": {
                    "type": "string",
                    "description": "Target branch for the pull request.",
                },
                "title": {
                    "type": "string",
                    "description": "Pull request title.",
                },
                "body": {
                    "type": "string",
                    "description": "Pull request body in Markdown.",
                },
                "git_token_env": {
                    "type": "string",
                    "description": "Optional environment variable name holding the GitHub token.",
                },
            },
            "required": ["repo", "head_branch", "base_branch", "title", "body"],
        },
    },
}


@tool
def create_pr(
    repo: str,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
    git_token_env: str = "",
) -> ToolResult:
    """Create a GitHub pull request from an existing branch."""
    head_branch = (head_branch or "").strip()
    base_branch = (base_branch or "").strip()
    title = (title or "").strip()
    if not head_branch:
        return ToolResult(success=False, output="head_branch is required")
    if not base_branch:
        return ToolResult(success=False, output="base_branch is required")
    if not title:
        return ToolResult(success=False, output="title is required")

    try:
        owner, repo_name = _github_repo_slug(repo)
        token, token_source = _github_token(git_token_env)
    except ValueError as exc:
        return ToolResult(success=False, output=str(exc))

    try:
        response = httpx.post(
            f"https://api.github.com/repos/{owner}/{repo_name}/pulls",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "title": title,
                "body": body,
                "head": head_branch,
                "base": base_branch,
            },
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        return ToolResult(success=False, output=f"GitHub PR create failed: {exc}")

    if response.is_success:
        payload = response.json()
        return ToolResult(
            success=True,
            output=json.dumps(
                {
                    "pr_url": payload.get("html_url", ""),
                    "number": payload.get("number"),
                    "repo": f"{owner}/{repo_name}",
                    "head_branch": head_branch,
                    "base_branch": base_branch,
                    "token_env": token_source,
                },
                ensure_ascii=True,
            ),
        )

    detail = ""
    try:
        payload = response.json()
        detail = str(payload.get("message") or payload)
    except Exception:
        detail = response.text.strip()
    return ToolResult(
        success=False,
        output=f"GitHub PR create failed ({response.status_code}): {detail or 'unknown error'}",
    )


create_pr.__tool_schema__ = _CREATE_PR_SCHEMA


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
        evo_sha: str = "",
        tool_timeout_seconds: float = 300.0,
    ):
        self._gateway = gateway
        self._config = config
        self._evo_root = evo_root
        self._evo_sha = evo_sha
        self._tool_timeout_seconds = tool_timeout_seconds
        
        # Load builtins — only include builtins that appear in the role's
        # requested tool list (or all if no evo tools are requested, for
        # backwards compatibility).
        disabled = set(config.disabled_builtins)
        requested = set(requested_evo_tools)
        self._functions: dict[str, Callable] = {}

        if "bash" not in disabled and ("bash" in requested or not requested):
            # Wrap bash with config injection
            def bash_with_config(command: str, workspace: str) -> ToolResult:
                if "bash" not in self._config.builtin:
                    self._config.builtin["bash"] = {}
                self._config.builtin["bash"].setdefault("timeout", self._tool_timeout_seconds)
                return bash(command, workspace, config=self._config)
            bash_with_config.__tool_schema__ = bash.__tool_schema__
            bash_with_config.__is_tool__ = True
            self._functions["bash"] = bash_with_config
        if "spawn" not in disabled and ("spawn" in requested or not requested):
            self._functions["spawn"] = spawn
        if "create_pr" not in disabled and ("create_pr" in requested or not requested):
            self._functions["create_pr"] = create_pr
        # Load evo tools
        requested_evo = [name for name in requested_evo_tools if name not in BUILTIN_TOOL_NAMES]
        evo_funcs = resolve_tool_functions(evo_root, requested_evo)
        
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
            if "evo_sha" in sig.parameters:
                kwargs["evo_sha"] = self._evo_sha

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

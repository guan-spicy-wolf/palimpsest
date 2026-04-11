"""Tool gateway - unified tool execution with transparent event capture.

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
from palimpsest.runtime.context import RuntimeContext
from palimpsest.runtime.event_gateway import EventGateway
from yoitsu_contracts.artifact import ArtifactBinding

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
    injected_args = {"workspace", "gateway", "evo_root", "evo_sha", "runtime_context"}

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



def _normalize_spawn_task(task: dict[str, Any], *, workspace: str, evo_sha: str) -> SpawnTaskData:
    """Normalize spawn task to canonical SpawnTaskData.

    Only canonical fields are accepted:
    - goal (required, min_length=1)
    - role (required, min_length=1)
    - budget
    - repo
    - init_branch
    - bundle
    - params (role-internal flags only)
    - eval_spec
    - sha

    Legacy fields (prompt, task, repo_url, branch, params.repo, etc.) are rejected.
    repo defaults to the workspace's origin URL if not specified.
    """
    if not isinstance(task, dict):
        raise ValueError("Each spawn task must be an object")

    # Check for forbidden legacy keys first
    forbidden = {"prompt", "task", "repo_url", "branch", "role_fn", "role_file", "role_sha", "evo_sha"}
    found = forbidden & set(task.keys())
    if found:
        raise ValueError(f"Legacy field(s) not allowed: {found}. Use canonical fields (goal, repo, init_branch, role).")

    goal = task.get("goal")
    role = task.get("role")

    # Validate required fields
    if goal is None or (isinstance(goal, str) and not goal.strip()):
        raise ValueError("Each spawn task requires a non-empty goal")
    if role is None or (isinstance(role, str) and not role.strip()):
        raise ValueError("Each spawn task requires a non-empty role")

    goal = str(goal).strip()
    role = str(role).strip()

    budget = task.get("budget")
    repo = task.get("repo")
    init_branch = task.get("init_branch")
    bundle = task.get("bundle")
    sha = task.get("sha")
    params = task.get("params")

    eval_spec = task.get("eval_spec")
    normalized_eval_spec = EvalSpec.model_validate(eval_spec) if isinstance(eval_spec, dict) else None

    # ADR-0013: input_artifacts
    input_artifacts_data = task.get("input_artifacts", [])
    input_artifacts = [
        ArtifactBinding.model_validate(b) for b in input_artifacts_data
    ] if input_artifacts_data else []

    # params must be a dict of role-internal flags only
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("params must be a dict of role-internal flags")
    # Validate params doesn't contain task semantics
    forbidden_params = {"goal", "budget", "repo", "repo_url", "branch", "init_branch", "task", "prompt"}
    params_violations = forbidden_params & set(params.keys())
    if params_violations:
        raise ValueError(f"params contains forbidden task semantics: {params_violations}")

    # Infer repo from workspace if not provided
    if not repo:
        try:
            ws_repo = git.Repo(workspace)
            if ws_repo.remotes:
                repo = ws_repo.remotes[0].url
        except Exception:
            pass

    # Infer init_branch from workspace if not provided
    if not init_branch:
        try:
            ws_repo = git.Repo(workspace)
            init_branch = ws_repo.active_branch.name
        except Exception:
            pass

    # Infer sha from evo_sha if not provided
    if not sha and evo_sha:
        sha = evo_sha

    return SpawnTaskData(
        goal=goal,
        role=role,
        budget=float(budget) if isinstance(budget, (int, float)) else 0.0,
        repo=str(repo or ""),
        init_branch=str(init_branch or ""),
    bundle=str(bundle or "").strip(),
        sha=sha or None,
        params=params,
        eval_spec=normalized_eval_spec,
        input_artifacts=input_artifacts,  # ADR-0013
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
                            "goal": {
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
                            "bundle": {
                                "type": "string",
                                "description": "Bundle for artifact loading. Omit to inherit from parent.",
                            },
                            "sha": {
                                "type": "string",
                                "description": "Git SHA to pin evo version.",
                            },
                            "params": {
                                "type": "object",
                                "description": "Role-internal behavior flags (e.g., mode=join). Must not contain task semantics.",
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
                            "input_artifacts": {
                                "type": "array",
                                "description": "Artifacts to materialize in child workspace (ADR-0013).",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "ref": {"type": "object"},
                                        "relation": {"type": "string"},
                                        "path": {"type": "string"},
                                        "metadata": {"type": "object"},
                                    },
                                },
                            },
                        },
                        "required": ["goal", "role"],
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
    """Create a GitHub pull request from an existing branch.

    Uses the unified GitHubClient for consistent API access.
    """
    from .github_client import GitHubClient, GitHubError

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
        client = GitHubClient(token_env=git_token_env or "GITHUB_TOKEN")
        owner, repo_name = client.parse_repo_slug(repo)
    except ValueError as exc:
        return ToolResult(success=False, output=str(exc))

    try:
        pr = client.create_pr(
            owner=owner,
            repo=repo_name,
            head_branch=head_branch,
            base_branch=base_branch,
            title=title,
            body=body,
        )
        return ToolResult(
            success=True,
            output=json.dumps(
                {
                    "pr_url": pr.html_url,
                    "number": pr.number,
                    "repo": f"{owner}/{repo_name}",
                    "head_branch": head_branch,
                    "base_branch": base_branch,
                },
                ensure_ascii=True,
            ),
        )
    except GitHubError as exc:
        return ToolResult(success=False, output=str(exc))


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
    bundle_workspace: str | Path,
    requested: list[str],
) -> dict[str, Callable]:
    """Scan bundle_workspace/tools/ for requested @tool functions.

    Per ADR-0015: Looks in bundle_workspace/tools/ directly.

    Args:
        bundle_workspace: Bundle repo root directory
        requested: List of tool names to resolve

    Returns:
        Dict mapping tool names to their callable functions.
    """
    tools_dir = Path(bundle_workspace) / "tools"
    requested_set = set(requested)
    result: dict[str, Callable] = {}

    if tools_dir.is_dir():
        for py_file in sorted(tools_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            funcs = _load_tool_functions(py_file)
            for name, func in funcs.items():
                if name in requested_set:
                    result[name] = func

    missing = requested_set - set(result.keys())
    if missing:
        logger.warning(f"Tools not found: {missing}")

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
        bundle_workspace: Path,
        requested_evo_tools: list[str],
        gateway: EventGateway,
        bundle_sha: str = "",
        tool_timeout_seconds: float = 300.0,
    ):
        self._gateway = gateway
        self._config = config
        self._bundle_workspace = bundle_workspace
        self._bundle_sha = bundle_sha
        self._tool_timeout_seconds = tool_timeout_seconds

        # Load builtins - only include builtins that appear in the role's
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
        # Load bundle tools from bundle_workspace
        requested_bundle = [name for name in requested_evo_tools if name not in BUILTIN_TOOL_NAMES]
        bundle_funcs = resolve_tool_functions(bundle_workspace, requested_bundle)

        dups = find_duplicate_tool_names(self._functions, bundle_funcs)
        if dups:
            raise ValueError("Duplicate tool names configured: " + ", ".join(dups))

        self._functions.update(bundle_funcs)

        # Pre-build schemas
        self._schemas = [func.__tool_schema__ for func in self._functions.values()]

    def schema(self) -> list[dict]:
        return self._schemas

    def execute(
        self,
        name: str,
        call_id: str,
        args: dict,
        workspace: str,
        runtime_context: RuntimeContext | None = None,
    ) -> ToolResult:
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
            if "bundle_workspace" in sig.parameters:
                kwargs["bundle_workspace"] = str(self._bundle_workspace)
            if "bundle_sha" in sig.parameters:
                kwargs["bundle_sha"] = self._bundle_sha
            # Backward compat: support old evo_root/evo_sha signatures
            if "evo_root" in sig.parameters and "bundle_workspace" not in kwargs:
                kwargs["evo_root"] = str(self._bundle_workspace)
            if "evo_sha" in sig.parameters and "bundle_sha" not in kwargs:
                kwargs["evo_sha"] = self._bundle_sha
            if "runtime_context" in sig.parameters and runtime_context is not None:
                kwargs["runtime_context"] = runtime_context

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

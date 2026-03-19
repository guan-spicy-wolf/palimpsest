# Evo Provider Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move tool/context implementations to evo as Python ToolProvider/ContextProvider classes; fix tool_loader to avoid sys.modules; add task_complete termination protocol; fill evo/tools/ so the system can actually run a job end-to-end.

**Architecture:** The runtime (skeleton) defines ABC interfaces (`ToolProvider`, `ContextProvider`) and a one-shot resolver that loads evo Python files into local scope, extracts provider instances, and passes them to the pipeline as plain objects. Evo (muscle) contains concrete implementations. Prompt stays as text. The runtime owns termination decisions; agent-side `task_complete` is a signal, not the trigger.

**Tech Stack:** Python 3.14, pytest, gitpython, pydantic, litellm

**Key design decisions:**
- **No `sys.modules` registration** — evo modules are loaded into isolated local scope via `importlib`, never registered globally.
- **Shared resolver** — `resolve_providers()` is a generic function used for both tools and context providers, parameterized by directory, ABC type, and key extraction function.
- **ContextProvider render context** — runtime dependencies (gateway, task text) are passed via an explicit `runtime_deps: dict` parameter on `render()`, not smuggled through `section_config`.
- **Evo tests use the resolver** — tests load evo providers through `resolve_tool_providers()` / `resolve_context_providers()`, not direct Python imports, because evo is a submodule without `__init__.py`.
- **Graceful degradation** — if evo context providers fail to load, builtin fallbacks are used. The runtime logs a warning but does not crash.

---

## File Map

### Runtime (palimpsest/) — modify only

| File | Responsibility | Change |
|------|---------------|--------|
| `palimpsest/runtime/interfaces.py` | ABC definitions | Add `runtime_deps` param to `ContextProvider.render()` |
| `palimpsest/runtime/resolver.py` | **NEW** — Generic provider resolver | Shared one-shot resolve logic for tools and context |
| `palimpsest/gateway/tool_loader.py` | Tool-specific resolver + EvoToolGateway | Rewrite: delegates to generic resolver, no sys.modules |
| `palimpsest/gateway/tools.py` | BuiltinToolGateway, CompositeToolGateway, ToolResult | Add `terminal: bool` to ToolResult; dispatch index on Composite |
| `palimpsest/stages/context.py` | Context building | Use generic resolver for evo providers; keep builtins as fallback |
| `palimpsest/stages/interaction.py` | Agent loop | Check `terminal` flag on ToolResult to end loop |
| `palimpsest/runner.py` | Orchestrator | Wire new loaders, pass `evo_root` to `build_context` |
| `palimpsest/stages/publication.py` | Git publish | Remove bare except, let failures propagate |

### Evo (evo/) — create new

| File | Responsibility |
|------|---------------|
| `evo/tools/__init__.py` | Package marker (empty) |
| `evo/tools/file_ops.py` | ToolProvider: read_file, write_file, list_files |
| `evo/tools/task_complete.py` | ToolProvider: task_complete (returns terminal signal) |
| `evo/contexts/__init__.py` | Package marker (empty) |
| `evo/contexts/file_tree_provider.py` | ContextProvider: file_tree section |
| `evo/contexts/recent_events_provider.py` | ContextProvider: recent_events section |
| `evo/contexts/task_description_provider.py` | ContextProvider: task_description section |
| `evo/contexts/version_history_provider.py` | ContextProvider: version_history (degraded placeholder) |
| `evo/roles/default.yaml` | Updated tool list (flat format) |

### Tests — create new

| File | Tests |
|------|-------|
| `tests/__init__.py` | Package marker |
| `tests/conftest.py` | Shared fixtures, PYTHONPATH setup |
| `tests/test_tool_result.py` | ToolResult terminal field |
| `tests/test_interaction_terminal.py` | Interaction loop stops on terminal signal |
| `tests/test_resolver.py` | Generic one-shot resolver, no sys.modules leak |
| `tests/test_tool_loader.py` | Tool-specific resolver + EvoToolGateway |
| `tests/test_evo_tools.py` | file_ops, task_complete providers via resolver |
| `tests/test_context_loader.py` | Context provider discovery from evo |
| `tests/test_composite_gateway.py` | CompositeToolGateway dispatch index |
| `tests/test_publication.py` | Publication failure propagates |

---

## Task 0: Test infrastructure

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create test package and conftest**

```python
# tests/__init__.py
# (empty)
```

```python
# tests/conftest.py
"""Shared fixtures for palimpsest tests."""

import sys
from pathlib import Path

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
```

- [ ] **Step 2: Verify pytest discovers the test directory**

Run: `python -m pytest tests/ --collect-only`
Expected: `no tests ran` (no test files yet, but no errors)

- [ ] **Step 3: Commit**

```bash
git add tests/__init__.py tests/conftest.py
git commit -m "test: add test infrastructure"
```

---

## Task 1: Add `terminal` flag to ToolResult + interaction loop termination

**Files:**
- Modify: `palimpsest/gateway/tools.py:31-33`
- Modify: `palimpsest/stages/interaction.py:38-40`
- Create: `tests/test_tool_result.py`
- Create: `tests/test_interaction_terminal.py`

- [ ] **Step 1: Write ToolResult tests**

```python
# tests/test_tool_result.py
from palimpsest.gateway.tools import ToolResult


def test_tool_result_has_terminal_field():
    r = ToolResult(success=True, output="done", terminal=True)
    assert r.terminal is True


def test_tool_result_terminal_defaults_false():
    r = ToolResult(success=True, output="ok")
    assert r.terminal is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tool_result.py -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'terminal'`

- [ ] **Step 3: Add `terminal` field to ToolResult**

In `palimpsest/gateway/tools.py`, change the ToolResult dataclass:

```python
@dataclass
class ToolResult:
    success: bool
    output: str
    terminal: bool = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tool_result.py -v`
Expected: PASS

- [ ] **Step 5: Write the interaction loop terminal tests**

```python
# tests/test_interaction_terminal.py
from unittest.mock import MagicMock
from palimpsest.gateway.tools import ToolResult
from palimpsest.stages.interaction import run_interaction_loop


class FakeLLM:
    """Always returns specified tool calls."""
    def __init__(self, tool_calls_per_turn):
        self._tool_calls = tool_calls_per_turn
        self.call_count = 0

    def call(self, messages, tools_schema):
        self.call_count += 1
        tcs = self._tool_calls
        return MagicMock(
            text=None,
            tool_calls=[MagicMock(id=f"tc{i}", name=tc[0], arguments=tc[1]) for i, tc in enumerate(tcs)],
            raw_message={"role": "assistant", "content": None, "tool_calls": [
                {"id": f"tc{i}", "type": "function", "function": {"name": tc[0], "arguments": "{}"}}
                for i, tc in enumerate(tcs)
            ]},
        )


class FakeTools:
    """Returns terminal=True for task_complete, normal for others."""
    def schema(self):
        return []

    def execute(self, name, call_id, args, workspace):
        if name == "task_complete":
            return ToolResult(success=True, output="Task complete.", terminal=True)
        return ToolResult(success=True, output="ok")


def test_interaction_loop_stops_on_terminal():
    llm = FakeLLM([("task_complete", {"summary": "done", "status": "success"})])
    tools = FakeTools()
    context = {"system": "test agent", "task": "do nothing"}
    result = run_interaction_loop("job-1", context, "/tmp", llm, tools, max_iterations=10)
    assert llm.call_count == 1
    assert result["status"] == "success"


def test_interaction_loop_terminal_mid_batch():
    """When task_complete appears after other tools in a batch, loop still terminates."""
    llm = FakeLLM([("bash", {"command": "echo hi"}), ("task_complete", {"summary": "done", "status": "success"})])
    tools = FakeTools()
    context = {"system": "test agent", "task": "do something then complete"}
    result = run_interaction_loop("job-1", context, "/tmp", llm, tools, max_iterations=10)
    assert llm.call_count == 1
    assert result["status"] == "success"
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `python -m pytest tests/test_interaction_terminal.py -v`
Expected: FAIL — loop runs all 10 iterations

- [ ] **Step 7: Modify interaction loop to check terminal flag**

In `palimpsest/stages/interaction.py`, change the tool call loop:

```python
        for tc in response.tool_calls:
            result = tools.execute(tc.name, tc.id, tc.arguments, workspace_path)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result.output})

            if result.terminal:
                logger.info("Runtime received terminal signal from tool")
                return {"status": "success", "summary": result.output[:500]}
```

- [ ] **Step 8: Run all tests to verify**

Run: `python -m pytest tests/test_tool_result.py tests/test_interaction_terminal.py -v`
Expected: all 4 PASS

- [ ] **Step 9: Commit**

```bash
git add palimpsest/gateway/tools.py palimpsest/stages/interaction.py tests/test_tool_result.py tests/test_interaction_terminal.py
git commit -m "feat: add terminal flag to ToolResult, interaction loop respects it"
```

---

## Task 2: Generic provider resolver + rewrite tool_loader

This task creates the shared resolver, rewrites tool_loader, and updates runner.py in one atomic step to avoid broken imports.

**Files:**
- Create: `palimpsest/runtime/resolver.py`
- Rewrite: `palimpsest/gateway/tool_loader.py`
- Modify: `palimpsest/runner.py:35-36, 121-133`
- Create: `tests/test_resolver.py`
- Create: `tests/test_tool_loader.py`

- [ ] **Step 1: Write generic resolver tests**

```python
# tests/test_resolver.py
import sys
import textwrap
from pathlib import Path

from palimpsest.runtime.interfaces import ToolProvider, ToolSpec
from palimpsest.gateway.tools import ToolResult


def test_resolve_discovers_subclasses(tmp_path):
    from palimpsest.runtime.resolver import resolve_providers

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "__init__.py").write_text("")
    (tools_dir / "greet.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.interfaces import ToolProvider, ToolSpec
        from palimpsest.gateway.tools import ToolResult

        class GreetProvider(ToolProvider):
            def tools(self):
                return [ToolSpec(name="greet", description="Say hi", parameters={})]
            def execute(self, name, args, workspace):
                return ToolResult(success=True, output="hello")
    """))

    result = resolve_providers(
        scan_dir=tools_dir,
        base_class=ToolProvider,
        key_fn=lambda inst: [s.name for s in inst.tools()],
        requested=["greet"],
    )
    assert "greet" in result
    assert result["greet"].execute("greet", {}, "/tmp").output == "hello"


def test_resolve_no_sys_modules_leak(tmp_path):
    from palimpsest.runtime.resolver import resolve_providers

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "__init__.py").write_text("")
    (tools_dir / "leak_check.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.interfaces import ToolProvider, ToolSpec
        from palimpsest.gateway.tools import ToolResult

        class LeakProvider(ToolProvider):
            def tools(self):
                return [ToolSpec(name="leak", description="x", parameters={})]
            def execute(self, name, args, workspace):
                return ToolResult(success=True, output="ok")
    """))

    before = set(sys.modules.keys())
    resolve_providers(
        scan_dir=tools_dir,
        base_class=ToolProvider,
        key_fn=lambda inst: [s.name for s in inst.tools()],
        requested=["leak"],
    )
    after = set(sys.modules.keys())
    new_modules = after - before
    # The specific evo module must not appear in sys.modules
    assert not any("leak_check" in m for m in new_modules)


def test_resolve_filters_to_requested_only(tmp_path):
    from palimpsest.runtime.resolver import resolve_providers

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "__init__.py").write_text("")
    (tools_dir / "multi.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.interfaces import ToolProvider, ToolSpec
        from palimpsest.gateway.tools import ToolResult

        class MultiProvider(ToolProvider):
            def tools(self):
                return [
                    ToolSpec(name="a", description="a", parameters={}),
                    ToolSpec(name="b", description="b", parameters={}),
                ]
            def execute(self, name, args, workspace):
                return ToolResult(success=True, output=name)
    """))

    result = resolve_providers(
        scan_dir=tools_dir,
        base_class=ToolProvider,
        key_fn=lambda inst: [s.name for s in inst.tools()],
        requested=["a"],
    )
    assert "a" in result
    assert "b" not in result


def test_resolve_warns_missing(tmp_path, caplog):
    from palimpsest.runtime.resolver import resolve_providers

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "__init__.py").write_text("")

    resolve_providers(
        scan_dir=tools_dir,
        base_class=ToolProvider,
        key_fn=lambda inst: [s.name for s in inst.tools()],
        requested=["nonexistent"],
    )
    assert "nonexistent" in caplog.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'palimpsest.runtime.resolver'`

- [ ] **Step 3: Create palimpsest/runtime/resolver.py**

```python
"""Generic one-shot provider resolver.

Scans a directory for .py files, loads each in an isolated namespace
(NOT registered in sys.modules), finds subclasses of a given ABC,
instantiates them, and returns a dict keyed by the provider's declared
names, filtered to the requested set.

Used by both tool_loader and context_loader.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Callable

from loguru import logger


def resolve_providers(
    scan_dir: Path,
    base_class: type,
    key_fn: Callable,
    requested: list[str],
) -> dict[str, object]:
    """One-shot resolve: scan *.py in scan_dir, return {key: instance} for requested keys.

    Args:
        scan_dir: Directory to scan for .py files.
        base_class: ABC to find subclasses of.
        key_fn: Given an instance, return a list of string keys it provides.
        requested: Only return providers whose keys are in this list.

    Returns:
        Dict mapping key -> provider instance, filtered to requested.
    """
    if not scan_dir.is_dir():
        logger.warning(f"Provider directory not found: {scan_dir}")
        return {}

    requested_set = set(requested)
    result: dict[str, object] = {}

    for py_file in sorted(scan_dir.glob("*.py")):
        if py_file.name.startswith("_"):
            continue
        try:
            instances = _load_subclasses(py_file, base_class)
            for instance in instances:
                for key in key_fn(instance):
                    if key in requested_set:
                        result[key] = instance
        except Exception as exc:
            logger.error(f"Failed to load providers from {py_file}: {exc}")

    missing = requested_set - set(result.keys())
    if missing:
        logger.warning(f"Providers not found in {scan_dir}: {missing}")

    return result


def _load_subclasses(py_path: Path, base_class: type) -> list:
    """Load a .py file in isolated scope, return instances of base_class subclasses."""
    module_name = f"_evo_resolve_{py_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, py_path)
    if spec is None or spec.loader is None:
        return []

    module = importlib.util.module_from_spec(spec)
    # Isolated local scope — NOT registered in sys.modules
    spec.loader.exec_module(module)

    instances = []
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, base_class)
            and attr is not base_class
        ):
            instances.append(attr())

    return instances
```

- [ ] **Step 4: Run resolver tests to verify**

Run: `python -m pytest tests/test_resolver.py -v`
Expected: all 4 PASS

- [ ] **Step 5: Write tool_loader tests**

```python
# tests/test_tool_loader.py
import textwrap
from pathlib import Path

from palimpsest.gateway.tool_loader import resolve_tool_providers, EvoToolGateway
from palimpsest.gateway.tools import ToolResult
from unittest.mock import MagicMock


def test_resolve_tool_providers(tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "__init__.py").write_text("")
    (tools_dir / "echo.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.interfaces import ToolProvider, ToolSpec
        from palimpsest.gateway.tools import ToolResult

        class EchoProvider(ToolProvider):
            def tools(self):
                return [ToolSpec(name="echo", description="Echo back", parameters={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]})]
            def execute(self, name, args, workspace):
                return ToolResult(success=True, output=args.get("msg", ""))
    """))

    providers = resolve_tool_providers(tmp_path, ["echo"])
    assert "echo" in providers


def test_evo_tool_gateway_schema(tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "__init__.py").write_text("")
    (tools_dir / "echo.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.interfaces import ToolProvider, ToolSpec
        from palimpsest.gateway.tools import ToolResult

        class EchoProvider(ToolProvider):
            def tools(self):
                return [ToolSpec(name="echo", description="Echo", parameters={"type": "object", "properties": {}})]
            def execute(self, name, args, workspace):
                return ToolResult(success=True, output="ok")
    """))

    providers = resolve_tool_providers(tmp_path, ["echo"])
    gw = EvoToolGateway(providers, MagicMock(), "job-1")
    schemas = gw.schema()
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "echo"
```

- [ ] **Step 6: Rewrite palimpsest/gateway/tool_loader.py**

```python
"""Tool-specific provider resolver + gateway wrapper.

Delegates to the generic ``resolve_providers()`` for discovery and loading.
Wraps resolved providers in ``EvoToolGateway`` for transparent event capture.
"""

from __future__ import annotations

import time
from pathlib import Path

from loguru import logger

from palimpsest.events import ToolExecData, ToolResultData
from palimpsest.gateway.tools import ToolGateway, ToolResult
from palimpsest.runtime.event_gateway import EventGateway
from palimpsest.runtime.interfaces import ToolProvider
from palimpsest.runtime.resolver import resolve_providers


def resolve_tool_providers(
    evo_root: str | Path,
    requested: list[str],
) -> dict[str, ToolProvider]:
    """Resolve tool providers from evo/tools/*.py."""
    evo_root = Path(evo_root)
    return resolve_providers(
        scan_dir=evo_root / "tools",
        base_class=ToolProvider,
        key_fn=lambda inst: [s.name for s in inst.tools()],
        requested=requested,
    )


class EvoToolGateway(ToolGateway):
    """Wraps resolved ToolProviders into the ToolGateway interface with event capture."""

    def __init__(
        self,
        providers: dict[str, ToolProvider],
        gateway: EventGateway,
        job_id: str,
    ):
        self._providers = providers
        self._gateway = gateway
        self._job_id = job_id
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
            return ToolResult(success=False, output=f"Unknown evo tool: {name}")

        self._gateway.emit_tool_exec(
            ToolExecData(
                job_id=self._job_id,
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
                job_id=self._job_id,
                tool_name=name,
                tool_call_id=call_id,
                success=result.success,
                duration_ms=duration_ms,
                output_preview=result.output[:256],
            )
        )

        return result
```

- [ ] **Step 7: Update runner.py imports and wiring simultaneously**

In `palimpsest/runner.py`, change:

```python
# Old imports to remove:
# from palimpsest.gateway.tool_loader import EvoToolLoader
# from palimpsest.gateway.tools import CompositeToolGateway

# New imports:
from palimpsest.gateway.tool_loader import resolve_tool_providers, EvoToolGateway
from palimpsest.gateway.tools import CompositeToolGateway
```

And in `_run_job_from_spec`, replace the tool composition block:

```python
        # Compose tool gateways: runtime builtins + evo tools
        builtin_tools = BuiltinToolGateway(
            config.tools,
            gateway,
            job_id,
            spawn_callback=spawn_cb,
        )
        evo_providers = resolve_tool_providers(evo_path, spec.tools)
        evo_tools = EvoToolGateway(evo_providers, gateway, job_id)
        tools = CompositeToolGateway([builtin_tools, evo_tools])
```

- [ ] **Step 8: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add palimpsest/runtime/resolver.py palimpsest/gateway/tool_loader.py palimpsest/runner.py tests/test_resolver.py tests/test_tool_loader.py
git commit -m "refactor: generic one-shot resolver, rewrite tool_loader, no sys.modules"
```

---

## Task 3: Fix CompositeToolGateway — dispatch index at init

**Files:**
- Modify: `palimpsest/gateway/tools.py:49-71`
- Create: `tests/test_composite_gateway.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_composite_gateway.py
from unittest.mock import MagicMock
from palimpsest.gateway.tools import CompositeToolGateway, ToolResult


def _make_gateway(names: list[str]) -> MagicMock:
    gw = MagicMock()
    gw.schema.return_value = [
        {"type": "function", "function": {"name": n}} for n in names
    ]
    gw.execute.return_value = ToolResult(success=True, output="ok")
    return gw


def test_composite_dispatches_to_correct_gateway():
    gw_a = _make_gateway(["a"])
    gw_b = _make_gateway(["b"])
    composite = CompositeToolGateway([gw_a, gw_b])

    composite.execute("b", "call-1", {}, "/tmp")
    gw_b.execute.assert_called_once()
    gw_a.execute.assert_not_called()


def test_composite_schema_merges_all():
    gw_a = _make_gateway(["a"])
    gw_b = _make_gateway(["b", "c"])
    composite = CompositeToolGateway([gw_a, gw_b])

    names = [s["function"]["name"] for s in composite.schema()]
    assert names == ["a", "b", "c"]


def test_composite_unknown_tool():
    composite = CompositeToolGateway([_make_gateway(["a"])])
    result = composite.execute("nonexistent", "x", {}, "/tmp")
    assert not result.success
```

- [ ] **Step 2: Run tests — should pass with current code (baseline)**

Run: `python -m pytest tests/test_composite_gateway.py -v`
Expected: PASS

- [ ] **Step 3: Refactor CompositeToolGateway**

In `palimpsest/gateway/tools.py`:

```python
class CompositeToolGateway(ToolGateway):
    """Composes multiple tool gateways into a single interface."""

    def __init__(self, gateways: list[ToolGateway]):
        self._gateways = gateways
        self._dispatch: dict[str, ToolGateway] = {}
        for gw in gateways:
            for s in gw.schema():
                self._dispatch[s["function"]["name"]] = gw

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
```

- [ ] **Step 4: Run tests to verify**

Run: `python -m pytest tests/test_composite_gateway.py -v`
Expected: all 3 PASS

- [ ] **Step 5: Commit**

```bash
git add palimpsest/gateway/tools.py tests/test_composite_gateway.py
git commit -m "refactor: CompositeToolGateway uses dispatch index"
```

---

## Task 4: Create evo tool providers — file_ops + task_complete

**Files:**
- Create: `evo/tools/__init__.py`
- Create: `evo/tools/file_ops.py`
- Create: `evo/tools/task_complete.py`
- Create: `tests/test_evo_tools.py`

- [ ] **Step 1: Write tests (using resolver, not direct import)**

```python
# tests/test_evo_tools.py
"""Tests for evo tool providers loaded via the resolver."""
from pathlib import Path

from palimpsest.gateway.tool_loader import resolve_tool_providers

# Path to the real evo directory
EVO_ROOT = Path(__file__).parent.parent / "evo"


class TestFileOps:
    def test_read_file(self, tmp_path):
        (tmp_path / "hello.txt").write_text("world")
        providers = resolve_tool_providers(EVO_ROOT, ["read_file"])
        result = providers["read_file"].execute("read_file", {"path": "hello.txt"}, str(tmp_path))
        assert result.success
        assert "world" in result.output

    def test_read_file_not_found(self, tmp_path):
        providers = resolve_tool_providers(EVO_ROOT, ["read_file"])
        result = providers["read_file"].execute("read_file", {"path": "nope.txt"}, str(tmp_path))
        assert not result.success

    def test_read_file_path_traversal(self, tmp_path):
        providers = resolve_tool_providers(EVO_ROOT, ["read_file"])
        result = providers["read_file"].execute("read_file", {"path": "../../etc/passwd"}, str(tmp_path))
        assert not result.success

    def test_write_file(self, tmp_path):
        providers = resolve_tool_providers(EVO_ROOT, ["write_file"])
        result = providers["write_file"].execute("write_file", {"path": "new.txt", "content": "hello"}, str(tmp_path))
        assert result.success
        assert (tmp_path / "new.txt").read_text() == "hello"

    def test_write_file_creates_dirs(self, tmp_path):
        providers = resolve_tool_providers(EVO_ROOT, ["write_file"])
        result = providers["write_file"].execute("write_file", {"path": "sub/dir/f.txt", "content": "nested"}, str(tmp_path))
        assert result.success
        assert (tmp_path / "sub" / "dir" / "f.txt").read_text() == "nested"

    def test_list_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        providers = resolve_tool_providers(EVO_ROOT, ["list_files"])
        result = providers["list_files"].execute("list_files", {"path": "."}, str(tmp_path))
        assert result.success
        assert "a.txt" in result.output
        assert "b.txt" in result.output

    def test_list_files_not_a_dir(self, tmp_path):
        providers = resolve_tool_providers(EVO_ROOT, ["list_files"])
        result = providers["list_files"].execute("list_files", {"path": "nonexistent"}, str(tmp_path))
        assert not result.success


class TestTaskComplete:
    def test_returns_terminal(self):
        providers = resolve_tool_providers(EVO_ROOT, ["task_complete"])
        result = providers["task_complete"].execute(
            "task_complete", {"summary": "all done", "status": "success"}, "/tmp"
        )
        assert result.success
        assert result.terminal is True
        assert "all done" in result.output

    def test_tool_spec(self):
        providers = resolve_tool_providers(EVO_ROOT, ["task_complete"])
        specs = providers["task_complete"].tools()
        assert any(s.name == "task_complete" for s in specs)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_evo_tools.py -v`
Expected: FAIL — tools not found

- [ ] **Step 3: Create evo/tools/__init__.py**

Empty file.

- [ ] **Step 4: Create evo/tools/file_ops.py**

```python
"""File operations tool provider (evolvable).

Provides read_file, write_file, list_files.  Path traversal is prevented
by resolving paths relative to the workspace and verifying containment.
"""

from __future__ import annotations

import os
from pathlib import Path

from palimpsest.runtime.interfaces import ToolProvider, ToolSpec
from palimpsest.gateway.tools import ToolResult


def _safe_resolve(workspace: str, rel_path: str) -> Path:
    """Resolve a relative path within workspace, raise on traversal."""
    ws = Path(workspace).resolve()
    target = (ws / rel_path).resolve()
    try:
        target.relative_to(ws)
    except ValueError:
        raise ValueError(f"Path traversal denied: {rel_path}")
    return target


class FileOpsProvider(ToolProvider):

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="read_file",
                description="Read the contents of a file in the workspace.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path relative to workspace root"},
                    },
                    "required": ["path"],
                },
            ),
            ToolSpec(
                name="write_file",
                description="Write content to a file in the workspace (overwrites if exists).",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path relative to workspace root"},
                        "content": {"type": "string", "description": "File content to write"},
                    },
                    "required": ["path", "content"],
                },
            ),
            ToolSpec(
                name="list_files",
                description="List files in a directory within the workspace.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Directory path relative to workspace root (default: .)"},
                    },
                },
            ),
        ]

    def execute(self, name: str, args: dict, workspace: str) -> ToolResult:
        try:
            if name == "read_file":
                return self._read(args, workspace)
            elif name == "write_file":
                return self._write(args, workspace)
            elif name == "list_files":
                return self._list(args, workspace)
            return ToolResult(success=False, output=f"Unknown tool: {name}")
        except ValueError as exc:
            return ToolResult(success=False, output=str(exc))
        except FileNotFoundError as exc:
            return ToolResult(success=False, output=f"File not found: {exc}")
        except Exception as exc:
            return ToolResult(success=False, output=f"Error: {exc}")

    def _read(self, args: dict, workspace: str) -> ToolResult:
        fpath = _safe_resolve(workspace, args["path"])
        if not fpath.is_file():
            return ToolResult(success=False, output=f"File not found: {args['path']}")
        content = fpath.read_text(errors="replace")
        return ToolResult(success=True, output=content[:8192])

    def _write(self, args: dict, workspace: str) -> ToolResult:
        fpath = _safe_resolve(workspace, args["path"])
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(args["content"])
        return ToolResult(success=True, output=f"Written {len(args['content'])} chars to {args['path']}")

    def _list(self, args: dict, workspace: str) -> ToolResult:
        rel = args.get("path", ".")
        dpath = _safe_resolve(workspace, rel)
        if not dpath.is_dir():
            return ToolResult(success=False, output=f"Not a directory: {rel}")
        entries = sorted(os.listdir(dpath))
        return ToolResult(success=True, output="\n".join(entries))
```

- [ ] **Step 5: Create evo/tools/task_complete.py**

```python
"""Task completion signal provider (evolvable).

Returns a ToolResult with terminal=True.  The runtime's interaction loop
checks this flag and ends the loop — the termination decision is the
runtime's, this tool merely provides the signal and structured metadata.
"""

from __future__ import annotations

from palimpsest.runtime.interfaces import ToolProvider, ToolSpec
from palimpsest.gateway.tools import ToolResult


class TaskCompleteProvider(ToolProvider):

    def tools(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="task_complete",
                description="Signal that the task is complete. Provide a summary and status.",
                parameters={
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "Brief summary of what was accomplished"},
                        "status": {
                            "type": "string",
                            "enum": ["success", "partial"],
                            "description": "Whether the task was fully or partially completed",
                        },
                    },
                    "required": ["summary", "status"],
                },
            ),
        ]

    def execute(self, name: str, args: dict, workspace: str) -> ToolResult:
        summary = args.get("summary", "")
        status = args.get("status", "success")
        return ToolResult(
            success=True,
            output=f"[{status}] {summary}",
            terminal=True,
        )
```

- [ ] **Step 6: Run all evo tool tests**

Run: `python -m pytest tests/test_evo_tools.py -v`
Expected: all 10 PASS

- [ ] **Step 7: Commit**

```bash
git add evo/tools/ tests/test_evo_tools.py
git commit -m "feat: add evo tool providers — file_ops + task_complete"
```

---

## Task 5: Update ContextProvider ABC + add evo context loader

**Files:**
- Modify: `palimpsest/runtime/interfaces.py:36` — add `runtime_deps` parameter
- Modify: `palimpsest/stages/context.py` — add evo loader, keep builtins as fallback
- Create: `tests/test_context_loader.py`

- [ ] **Step 1: Write context loader tests**

```python
# tests/test_context_loader.py
import textwrap
from pathlib import Path


def test_resolve_context_discovers_providers(tmp_path):
    from palimpsest.stages.context import resolve_context_providers

    ctx_dir = tmp_path / "contexts"
    ctx_dir.mkdir()
    (ctx_dir / "__init__.py").write_text("")
    (ctx_dir / "custom.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.interfaces import ContextProvider

        class CustomSection(ContextProvider):
            @property
            def section_type(self) -> str:
                return "custom"
            def render(self, job_id, workspace, section_config, runtime_deps=None):
                return "## Custom\\nhello"
    """))

    providers = resolve_context_providers(tmp_path, ["custom"])
    assert "custom" in providers
    assert "hello" in providers["custom"].render("j1", "/tmp", {})


def test_resolve_context_ignores_unrequested(tmp_path):
    from palimpsest.stages.context import resolve_context_providers

    ctx_dir = tmp_path / "contexts"
    ctx_dir.mkdir()
    (ctx_dir / "__init__.py").write_text("")
    (ctx_dir / "two.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.interfaces import ContextProvider

        class AProvider(ContextProvider):
            @property
            def section_type(self): return "a"
            def render(self, job_id, workspace, section_config, runtime_deps=None): return "a"

        class BProvider(ContextProvider):
            @property
            def section_type(self): return "b"
            def render(self, job_id, workspace, section_config, runtime_deps=None): return "b"
    """))

    providers = resolve_context_providers(tmp_path, ["a"])
    assert "a" in providers
    assert "b" not in providers
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_context_loader.py -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Update ContextProvider ABC in interfaces.py**

In `palimpsest/runtime/interfaces.py`, add `runtime_deps` parameter:

```python
class ContextProvider(ABC):
    """Renders a single section of the LLM context window."""

    @property
    @abstractmethod
    def section_type(self) -> str:
        """The section type string this provider handles."""

    @abstractmethod
    def render(
        self,
        job_id: str,
        workspace: str,
        section_config: dict,
        runtime_deps: dict | None = None,
    ) -> str:
        """Return rendered markdown for this context section.

        ``runtime_deps`` carries runtime-injected dependencies (e.g.
        EventGateway, task text).  Keys are documented per provider.
        """
```

- [ ] **Step 4: Add `resolve_context_providers` to context.py and update `build_context`**

Rewrite `palimpsest/stages/context.py`:

```python
"""Stage 2: Context building from the resolved JobSpec.

Assembles the LLM context window using the JobSpec's system prompt and
a registry of ``ContextProvider`` implementations.  Evo providers are
loaded first; builtin fallbacks are used for section types not found in evo.
"""

from __future__ import annotations

import os
from pathlib import Path

from loguru import logger

from palimpsest.runtime.event_gateway import EventGateway
from palimpsest.runtime.interfaces import ContextProvider
from palimpsest.runtime.resolver import resolve_providers
from palimpsest.runtime.role_resolver import JobSpec


# ---------------------------------------------------------------------------
# Evo context provider resolution
# ---------------------------------------------------------------------------

def resolve_context_providers(
    evo_root: str | Path,
    requested: list[str],
) -> dict[str, ContextProvider]:
    """Resolve context providers from evo/contexts/*.py."""
    evo_root = Path(evo_root)
    return resolve_providers(
        scan_dir=evo_root / "contexts",
        base_class=ContextProvider,
        key_fn=lambda inst: [inst.section_type],
        requested=requested,
    )


# ---------------------------------------------------------------------------
# Built-in fallback context providers
# ---------------------------------------------------------------------------

class _FileTreeFallback(ContextProvider):
    @property
    def section_type(self) -> str:
        return "file_tree"

    def render(self, job_id, workspace, section_config, runtime_deps=None):
        max_files = section_config.get("max_files", 50)
        excludes = set(section_config.get("exclude", [".git"]))
        lines, count = [], 0
        for dirpath, dirnames, filenames in os.walk(workspace):
            dirnames[:] = [d for d in dirnames if d not in excludes]
            rel_dir = os.path.relpath(dirpath, workspace)
            prefix = "" if rel_dir == "." else rel_dir + "/"
            for fname in filenames:
                lines.append(prefix + fname)
                count += 1
                if count >= max_files:
                    lines.append(f"... (truncated at {max_files} files)")
                    return f"## Workspace file tree\n```\n{chr(10).join(lines)}\n```"
        tree = "\n".join(lines) if lines else "(empty)"
        return f"## Workspace file tree\n```\n{tree}\n```"


class _RecentEventsFallback(ContextProvider):
    @property
    def section_type(self) -> str:
        return "recent_events"

    def render(self, job_id, workspace, section_config, runtime_deps=None):
        gateway = (runtime_deps or {}).get("gateway")
        if not gateway:
            return "## Recent events\n(event gateway not available)"
        limit = section_config.get("limit", 10)
        fmt = section_config.get("format", "- [{ts}] {type}")
        recent = gateway.recent_events(limit, job_id=job_id)
        lines = []
        for e in recent:
            try:
                lines.append(fmt.format_map({
                    "ts": e.get("ts", "N/A"),
                    "type": e.get("type", "unknown"),
                    "summary": e.get("data", {}).get("summary", ""),
                }))
            except KeyError:
                lines.append(f"- [{e.get('ts', 'N/A')}] {e.get('type', 'unknown')}")
        summary = "\n".join(lines) if lines else "(no recent events)"
        return f"## Recent events\n{summary}"


class _TaskDescriptionFallback(ContextProvider):
    @property
    def section_type(self) -> str:
        return "task_description"

    def render(self, job_id, workspace, section_config, runtime_deps=None):
        task = (runtime_deps or {}).get("task", "(no task provided)")
        return f"## Task\n{task}"


class _VersionHistoryFallback(ContextProvider):
    @property
    def section_type(self) -> str:
        return "version_history"

    def render(self, job_id, workspace, section_config, runtime_deps=None):
        return "## Version history\n(reading current checkout only)"


def _build_fallback_registry() -> dict[str, ContextProvider]:
    providers = [
        _FileTreeFallback(),
        _RecentEventsFallback(),
        _TaskDescriptionFallback(),
        _VersionHistoryFallback(),
    ]
    return {p.section_type: p for p in providers}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_context(
    job_id: str,
    workspace_path: str,
    task: str,
    spec: JobSpec,
    gateway: EventGateway,
    evo_root: Path | None = None,
) -> dict:
    """Build LLM context from a resolved JobSpec. Returns {"system": str, "task": str}."""
    system_prompt = spec.prompt

    # Start with builtin fallbacks
    registry = _build_fallback_registry()

    # Override with evo providers where available
    if evo_root:
        section_types = [s.get("type", "") for s in spec.context_template.get("sections", [])]
        evo_providers = resolve_context_providers(evo_root, section_types)
        registry.update(evo_providers)

    runtime_deps = {"gateway": gateway, "task": task}

    sections = spec.context_template.get("sections", [])
    parts: list[str] = []
    for section in sections:
        section_type = section.get("type", "")
        provider = registry.get(section_type)
        if provider:
            parts.append(provider.render(job_id, workspace_path, section, runtime_deps=runtime_deps))
        else:
            logger.warning(f"Unknown context section type: {section_type!r}")

    task_message = "\n\n".join(parts)
    logger.info(f"Built context for job {job_id}")
    return {"system": system_prompt, "task": task_message}
```

- [ ] **Step 5: Update runner.py to pass evo_root to build_context**

In `palimpsest/runner.py`, change the `build_context` call:

```python
        context = build_context(
            job_id,
            workspace,
            config.task,
            spec,
            gateway,
            evo_root=evo_path,
        )
```

- [ ] **Step 6: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add palimpsest/runtime/interfaces.py palimpsest/stages/context.py palimpsest/runner.py tests/test_context_loader.py
git commit -m "feat: add ContextProvider evo loader with builtin fallbacks"
```

---

## Task 6: Create evo context providers

**Files:**
- Create: `evo/contexts/__init__.py`
- Create: `evo/contexts/file_tree_provider.py`
- Create: `evo/contexts/recent_events_provider.py`
- Create: `evo/contexts/task_description_provider.py`
- Create: `evo/contexts/version_history_provider.py`

- [ ] **Step 1: Create evo/contexts/__init__.py**

Empty file.

- [ ] **Step 2: Create evo/contexts/file_tree_provider.py**

```python
"""File tree context provider (evolvable)."""

from __future__ import annotations

import os

from palimpsest.runtime.interfaces import ContextProvider


class FileTreeProvider(ContextProvider):

    @property
    def section_type(self) -> str:
        return "file_tree"

    def render(self, job_id: str, workspace: str, section_config: dict, runtime_deps=None) -> str:
        max_files = section_config.get("max_files", 50)
        excludes = set(section_config.get("exclude", [".git"]))
        lines: list[str] = []
        count = 0
        for dirpath, dirnames, filenames in os.walk(workspace):
            dirnames[:] = [d for d in dirnames if d not in excludes]
            rel_dir = os.path.relpath(dirpath, workspace)
            prefix = "" if rel_dir == "." else rel_dir + "/"
            for fname in filenames:
                lines.append(prefix + fname)
                count += 1
                if count >= max_files:
                    lines.append(f"... (truncated at {max_files} files)")
                    tree = "\n".join(lines)
                    return f"## Workspace file tree\n```\n{tree}\n```"
        tree = "\n".join(lines) if lines else "(empty)"
        return f"## Workspace file tree\n```\n{tree}\n```"
```

- [ ] **Step 3: Create evo/contexts/recent_events_provider.py**

```python
"""Recent events context provider (evolvable).

Requires runtime_deps["gateway"] (EventGateway instance).
"""

from __future__ import annotations

from palimpsest.runtime.interfaces import ContextProvider


class RecentEventsProvider(ContextProvider):

    @property
    def section_type(self) -> str:
        return "recent_events"

    def render(self, job_id: str, workspace: str, section_config: dict, runtime_deps=None) -> str:
        gateway = (runtime_deps or {}).get("gateway")
        if not gateway:
            return "## Recent events\n(event gateway not available)"

        limit = section_config.get("limit", 10)
        fmt = section_config.get("format", "- [{ts}] {type}")
        recent = gateway.recent_events(limit, job_id=job_id)
        lines = []
        for e in recent:
            try:
                lines.append(fmt.format_map({
                    "ts": e.get("ts", "N/A"),
                    "type": e.get("type", "unknown"),
                    "summary": e.get("data", {}).get("summary", ""),
                }))
            except KeyError:
                lines.append(f"- [{e.get('ts', 'N/A')}] {e.get('type', 'unknown')}")
        summary = "\n".join(lines) if lines else "(no recent events)"
        return f"## Recent events\n{summary}"
```

- [ ] **Step 4: Create evo/contexts/task_description_provider.py**

```python
"""Task description context provider (evolvable).

Requires runtime_deps["task"] (task text string).
"""

from __future__ import annotations

from palimpsest.runtime.interfaces import ContextProvider


class TaskDescriptionProvider(ContextProvider):

    @property
    def section_type(self) -> str:
        return "task_description"

    def render(self, job_id: str, workspace: str, section_config: dict, runtime_deps=None) -> str:
        task = (runtime_deps or {}).get("task", "(no task provided)")
        return f"## Task\n{task}"
```

- [ ] **Step 5: Create evo/contexts/version_history_provider.py**

```python
"""Version history context provider (evolvable).

Currently degraded — only reports that the runtime reads the current checkout.
Will be enhanced when full version state machine is implemented.
"""

from __future__ import annotations

from palimpsest.runtime.interfaces import ContextProvider


class VersionHistoryProvider(ContextProvider):

    @property
    def section_type(self) -> str:
        return "version_history"

    def render(self, job_id: str, workspace: str, section_config: dict, runtime_deps=None) -> str:
        return "## Version history\n(reading current checkout only)"
```

- [ ] **Step 6: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add evo/contexts/
git commit -m "feat: add evo context providers"
```

---

## Task 7: Update evo/roles/default.yaml

**Files:**
- Modify: `evo/roles/default.yaml`

- [ ] **Step 1: Update role definition**

```yaml
# Default role - baseline for all agents
# Other roles can inherit from this

name: default
prompt: prompts/default.md
context: contexts/default.yaml

# Evo tools to load (runtime builtins bash + spawn are always available)
tools:
  - read_file
  - write_file
  - list_files
  - task_complete
```

- [ ] **Step 2: Verify role resolution works**

Run: `python -c "from palimpsest.runtime.role_resolver import RoleResolver; r = RoleResolver('evo'); s = r.resolve('default'); print('tools:', s.tools); print('prompt:', s.prompt[:50]); print('context sections:', [sec['type'] for sec in s.context_template.get('sections', [])])"`

Expected output should show:
- `tools: ['read_file', 'write_file', 'list_files', 'task_complete']`
- Prompt text preview
- Context section types from default.yaml

- [ ] **Step 3: Commit**

```bash
git add evo/roles/default.yaml
git commit -m "chore: update default role to flat tools list"
```

---

## Task 8: Fix publication failure propagation

**Files:**
- Modify: `palimpsest/stages/publication.py`
- Create: `tests/test_publication.py`

- [ ] **Step 1: Write publication tests**

```python
# tests/test_publication.py
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

import git

from palimpsest.stages.publication import publish_results
from palimpsest.config import PublicationConfig


def test_publication_commits_changes(tmp_path):
    """Normal case: changes are committed and git_ref returned."""
    repo = git.Repo.init(tmp_path)
    (tmp_path / "init.txt").write_text("init")
    repo.index.add(["init.txt"])
    repo.index.commit("init")
    repo.git.checkout("-b", "palimpsest/job/test-1")

    (tmp_path / "new.txt").write_text("content")

    config = PublicationConfig()
    result = {"status": "success", "summary": "test"}
    git_ref = publish_results("test-1", result, str(tmp_path), config)

    assert git_ref is not None
    assert "palimpsest/job/test-1:" in git_ref


def test_publication_skips_failed_job(tmp_path):
    """Failed jobs skip publication."""
    config = PublicationConfig()
    result = {"status": "failed"}
    git_ref = publish_results("test-2", result, str(tmp_path), config)
    assert git_ref is None


def test_publication_push_failure_propagates(tmp_path):
    """Push failure must propagate as an exception, not return None."""
    repo = git.Repo.init(tmp_path)
    (tmp_path / "init.txt").write_text("init")
    repo.index.add(["init.txt"])
    repo.index.commit("init")
    repo.git.checkout("-b", "palimpsest/job/test-3")

    config = PublicationConfig()
    result = {"status": "success", "summary": "test"}

    with patch.object(git.Remote, "push", side_effect=git.GitCommandError("push", "simulated failure")):
        repo.create_remote("origin", "https://example.com/repo.git")
        with pytest.raises(git.GitCommandError):
            publish_results("test-3", result, str(tmp_path), config)
```

- [ ] **Step 2: Run tests to verify the failure test fails (current code swallows)**

Run: `python -m pytest tests/test_publication.py::test_publication_push_failure_propagates -v`
Expected: FAIL — no exception raised (current code catches it)

- [ ] **Step 3: Fix publication.py — remove bare except**

```python
from __future__ import annotations

import git
from loguru import logger

from palimpsest.config import PublicationConfig


def publish_results(
    job_id: str,
    result: dict,
    workspace_path: str,
    config: PublicationConfig,
) -> str | None:
    """Git commit and push. Returns git_ref 'branch:sha' or None.

    Raises on failure — the caller (runner.py) handles the exception
    and emits the appropriate job_failed event.
    """
    if result.get("status") == "failed":
        logger.warning("Skipping publication for failed job")
        return None

    repo = git.Repo(workspace_path)
    repo.git.add("-A")

    summary = result.get("summary", "")[:500]
    if repo.is_dirty(index=True) or repo.untracked_files:
        commit = repo.index.commit(f"feat: palimpsest job {job_id}\n\n{summary}")
        logger.info(f"Committed {commit.hexsha[:8]}")
    else:
        repo.git.commit("--allow-empty", "-m", f"chore: palimpsest job {job_id} (no changes)")
        commit = repo.head.commit
        logger.info(f"Empty commit {commit.hexsha[:8]}")

    branch_name = repo.active_branch.name
    git_ref = f"{branch_name}:{commit.hexsha}"

    if repo.remotes:
        logger.info(f"Pushing {branch_name}")
        repo.remotes[0].push(branch_name)
    else:
        logger.warning("No remote configured, skipping push")

    return git_ref
```

- [ ] **Step 4: Run all publication tests**

Run: `python -m pytest tests/test_publication.py -v`
Expected: all 3 PASS

- [ ] **Step 5: Commit**

```bash
git add palimpsest/stages/publication.py tests/test_publication.py
git commit -m "fix: publication failure now propagates instead of returning None"
```

---

## Task 9: Run full test suite + final verification

- [ ] **Step 1: Run entire test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: all tests PASS

- [ ] **Step 2: Verify end-to-end role resolution + tool loading**

Run: `python -c "
from pathlib import Path
from palimpsest.runtime.role_resolver import RoleResolver
from palimpsest.gateway.tool_loader import resolve_tool_providers
from palimpsest.stages.context import resolve_context_providers

evo = Path('evo')
spec = RoleResolver(evo).resolve('default')
print('JobSpec tools:', spec.tools)

tools = resolve_tool_providers(evo, spec.tools)
print('Resolved tools:', list(tools.keys()))

section_types = [s['type'] for s in spec.context_template.get('sections', [])]
ctx = resolve_context_providers(evo, section_types)
print('Resolved context providers:', list(ctx.keys()))
print('Fallback covers:', [t for t in section_types if t not in ctx])
"`

Expected: all 4 tools resolved, context providers resolved from evo (with fallback for any missing).

- [ ] **Step 3: Final commit (if any loose changes)**

```bash
git status
```

---

## Summary of changes by layer

**Runtime (skeleton) modifications:**
- `ToolResult`: +`terminal` field
- `ContextProvider.render()`: +`runtime_deps` parameter
- `interaction.py`: check `terminal` flag
- `runtime/resolver.py`: **new** — shared generic provider resolver
- `tool_loader.py`: rewritten (delegates to resolver, no sys.modules)
- `context.py`: evo loader + builtin fallbacks
- `tools.py`: `CompositeToolGateway` dispatch index
- `runner.py`: wire new loaders, pass `evo_root`
- `publication.py`: propagate failures

**Evo (muscle) additions:**
- `evo/tools/file_ops.py`: FileOpsProvider (read_file, write_file, list_files)
- `evo/tools/task_complete.py`: TaskCompleteProvider (terminal signal)
- `evo/contexts/file_tree_provider.py`: FileTreeProvider
- `evo/contexts/recent_events_provider.py`: RecentEventsProvider
- `evo/contexts/task_description_provider.py`: TaskDescriptionProvider
- `evo/contexts/version_history_provider.py`: VersionHistoryProvider (degraded)
- `evo/roles/default.yaml`: updated to flat tools list

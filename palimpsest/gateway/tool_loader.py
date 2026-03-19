"""Dynamic tool loader — loads ToolProvider implementations from evo.

Scans ``evo/tools/*.py`` for classes that implement ``ToolProvider``
and registers them.  This allows the Agent to evolve its own tool
repertoire using full Python — not limited to YAML templates.

The role YAML declares tool names; the loader matches them against
the ``ToolSpec.name`` exposed by each discovered ``ToolProvider``.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

from loguru import logger

from palimpsest.events import ToolExecData, ToolResultData
from palimpsest.gateway.tools import ToolGateway, ToolResult
from palimpsest.runtime.event_gateway import EventGateway
from palimpsest.runtime.interfaces import ToolProvider


class EvoToolLoader(ToolGateway):
    """Loads ToolProvider implementations from evo/tools/*.py.

    Discovery:
      1. Scan all .py files in evo/tools/
      2. Import each module and find ToolProvider subclasses
      3. Instantiate each provider and index by tool name
      4. Filter to only the tools requested by the role
    """

    def __init__(
        self,
        evo_root: str | Path,
        requested_tools: list[str],
        gateway: EventGateway,
        job_id: str,
    ):
        self._evo_root = Path(evo_root)
        self._gateway = gateway
        self._job_id = job_id
        # name -> (provider_instance, ToolSpec)
        self._tools: dict[str, tuple[ToolProvider, object]] = {}

        self._discover_and_register(requested_tools)

    def _discover_and_register(self, requested: list[str]) -> None:
        """Scan evo/tools/*.py, import modules, find ToolProvider subclasses."""
        tools_dir = self._evo_root / "tools"
        if not tools_dir.is_dir():
            logger.warning(f"No tools directory found at {tools_dir}")
            return

        requested_set = set(requested)

        for py_file in sorted(tools_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                providers = self._load_module_providers(py_file)
                for provider in providers:
                    for spec in provider.tools():
                        if spec.name in requested_set:
                            self._tools[spec.name] = (provider, spec)
                            logger.debug(f"Registered tool '{spec.name}' from {py_file.name}")
            except Exception as exc:
                logger.error(f"Failed to load tools from {py_file}: {exc}")

        # Warn about missing tools
        found = set(self._tools.keys())
        missing = requested_set - found
        if missing:
            logger.warning(f"Tools not found in evo: {missing}")

    @staticmethod
    def _load_module_providers(py_path: Path) -> list[ToolProvider]:
        """Dynamically import a .py file and return all ToolProvider instances."""
        module_name = f"evo_tools_{py_path.stem}"

        spec = importlib.util.spec_from_file_location(module_name, py_path)
        if spec is None or spec.loader is None:
            return []

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        providers = []
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, ToolProvider)
                and attr is not ToolProvider
            ):
                providers.append(attr())

        return providers

    def schema(self) -> list[dict]:
        schemas = []
        for name, (provider, spec) in self._tools.items():
            schemas.append({
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            })
        return schemas

    def execute(self, name: str, call_id: str, args: dict, workspace: str) -> ToolResult:
        entry = self._tools.get(name)
        if not entry:
            return ToolResult(success=False, output=f"Unknown evo tool: {name}")

        provider, spec = entry

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
        try:
            result = provider.execute(name, args, workspace)
        except Exception as exc:
            logger.error(f"Tool {name} raised: {exc}")
            result = ToolResult(success=False, output=f"Tool error: {exc}")

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

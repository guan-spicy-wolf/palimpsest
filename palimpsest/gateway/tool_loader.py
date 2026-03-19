"""Tool-specific provider resolver + gateway wrapper.

Delegates to the generic resolve_providers() for discovery and loading.
Wraps resolved providers in EvoToolGateway for transparent event capture.
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

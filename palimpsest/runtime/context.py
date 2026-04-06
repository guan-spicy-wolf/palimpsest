"""RuntimeContext — job-scoped context spanning all pipeline stages.

Part of the Runtime (skeleton). Provides a shared context object that
preparation_fn can populate with resources, tools can consume, and
finalization can clean up.

This enables non-filesystem-centric job types (e.g. Factorio RCON,
external API sessions) to inject stateful resources into the tool layer
without modifying tool signatures or the interaction loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from loguru import logger


@dataclass
class RuntimeContext:
    """Job-scoped context spanning preparation → interaction → publication.

    Created by the runner before calling preparation_fn. The preparation
    function populates ``resources`` with job-specific state (e.g. a
    database connection, an RCON bridge). Tools that declare a
    ``runtime_context: RuntimeContext`` parameter receive it via injection.

    Lifecycle::

        runner creates ctx
        → preparation_fn(runtime_context=ctx)   # populate resources
        → setup_workspace(...)                   # ctx.workspace_path set
        → context_fn(...)                        # build LLM context
        → interaction loop                       # tools receive ctx
        → publication_fn(runtime_context=ctx)    # may use resources
        → ctx.cleanup()                          # release resources
    """

    workspace_path: str = ""
    job_id: str = ""
    task_id: str = ""
    team: str = "default"  # team name for tools and publication
    role: str = ""  # role name for observation events
    resources: dict[str, Any] = field(default_factory=dict)
    _cleanup_fns: list[Callable] = field(default_factory=list, repr=False)

    def register_cleanup(self, fn: Callable[[], None]) -> None:
        """Register a callable to run during finalization (LIFO order)."""
        self._cleanup_fns.append(fn)

    def cleanup(self) -> None:
        """Release all registered resources. Errors are logged, not raised."""
        for fn in reversed(self._cleanup_fns):
            try:
                fn()
            except Exception as exc:
                logger.warning(f"RuntimeContext cleanup error: {exc}")
        self._cleanup_fns.clear()
        self.resources.clear()

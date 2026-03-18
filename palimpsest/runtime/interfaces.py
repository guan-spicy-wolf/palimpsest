"""Stable runtime interfaces for context and tool extension points.

These protocols define the contracts that the runtime honours.  Concrete
implementations live in the evolvable layer; the runtime loads them at
startup based on the ``JobSpec`` and delegates through these interfaces.

Adding a new context section type or a new tool no longer requires
touching the runner or the stage code — only a new provider
implementation is needed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Context provider interface
# ---------------------------------------------------------------------------

class ContextProvider(ABC):
    """Renders a single section of the LLM context window.

    Each provider is responsible for one ``section_type`` string (e.g.
    ``"file_tree"``, ``"recent_events"``).  The runtime calls
    ``render()`` during Stage 2 and concatenates the results.
    """

    @property
    @abstractmethod
    def section_type(self) -> str:
        """The section type string this provider handles."""

    @abstractmethod
    def render(self, job_id: str, workspace: str, section_config: dict) -> str:
        """Return rendered markdown for this context section."""


# ---------------------------------------------------------------------------
# Tool provider interface
# ---------------------------------------------------------------------------

@dataclass
class ToolSpec:
    """JSON-schema-compatible tool definition."""

    name: str
    description: str
    parameters: dict


class ToolProvider(ABC):
    """Provides one or more tools to the runtime.

    Builtin tools and custom tools both implement this interface.  The
    runtime collects all providers declared in the ``JobSpec``, merges
    their schemas, and dispatches tool calls accordingly.
    """

    @abstractmethod
    def tools(self) -> list[ToolSpec]:
        """Return the tool definitions this provider offers."""

    @abstractmethod
    def execute(self, name: str, args: dict, workspace: str) -> "ToolResult":
        """Execute the named tool and return a result.

        The ``ToolResult`` type is imported from ``gateway.tools`` to
        avoid a circular dependency at module level.
        """

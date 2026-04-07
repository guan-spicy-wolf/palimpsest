"""Factorio-specific tool for interacting with game server via RCON."""

from palimpsest.runtime.tools import tool, ToolResult
from palimpsest.runtime.context import RuntimeContext


@tool
def factorio_tool(command: str, runtime_context: RuntimeContext) -> ToolResult:
    """Execute a Factorio RCON command.

    Args:
        command: The RCON command to execute (e.g., '/c game.print("hello")')
        runtime_context: Injected runtime context containing RCON connection

    Returns:
        ToolResult with the command output
    """
    # Check for RCON connection in runtime_context
    if "rcon_connection" not in runtime_context.resources:
        return ToolResult(
            success=False,
            output="No RCON connection available. Was preparation_fn called?"
        )

    rcon = runtime_context.resources["rcon_connection"]

    # Mock execution - in real implementation would send command via RCON
    # For testing, we just return success with mock response
    runtime_context.resources["last_command"] = command

    return ToolResult(
        success=True,
        output=f"RCON command executed: {command} (via {rcon['host']}:{rcon['port']})"
    )
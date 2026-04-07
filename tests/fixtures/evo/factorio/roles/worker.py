"""Factorio-specific worker role - shadows global worker for factorio bundle."""

from palimpsest.runtime.roles import role, JobSpec, context_spec, workspace_config


def factorio_preparation(*, goal: str = "", repo: str = "", runtime_context=None, **params):
    """Factorio-specific preparation that creates a mock RCON resource."""
    from palimpsest.runtime.context import RuntimeContext

    if runtime_context is not None:
        # Create a mock RCON "connection" resource
        runtime_context.resources["rcon_connection"] = {
            "host": "localhost",
            "port": 27015,
            "connected": True,
        }
        # Register cleanup to "close" the connection
        runtime_context.register_cleanup(
            lambda: runtime_context.resources.update({"rcon_closed": True})
        )

    # Return workspace config (for git-based jobs, use standard config)
    return workspace_config()(goal=goal, repo=repo, **params)


def factorio_publication(*, result: dict, workspace_path: str, runtime_context=None, **params):
    """Factorio-specific publication that uses runtime_context resources."""
    # Factorio jobs may not use git publication - they might push to game state
    if runtime_context is not None and "rcon_connection" in runtime_context.resources:
        # Access the RCON connection from preparation
        rcon = runtime_context.resources["rcon_connection"]
        # In real implementation, would push changes to Factorio server
        return f"factorio://{rcon['host']}:{rcon['port']}/changes"
    return None


factorio_publication.__publication_strategy__ = "skip"  # Factorio doesn't use git


@role(
    name="worker",
    description="Factorio-specific worker role with RCON integration",
    role_type="worker",
)
def worker(**params):
    """Factorio team worker role definition.

    This shadows the global worker role for factorio bundle.
    Uses runtime_context for RCON connection lifecycle.
    """
    return JobSpec(
        preparation_fn=factorio_preparation,
        context_fn=context_spec(
            system="You are a Factorio worker agent. Manage game state via RCON.",
            sections=[
                {
                    "kind": "static",
                    "content": "Use factorio_tool to interact with the game server.",
                }
            ],
        ),
        publication_fn=factorio_publication,
        tools=["bash", "factorio_tool"],
    )
"""Factorio-specific worker role - shadows global worker for factorio bundle.

Per ADR-0018: Uses capability-only lifecycle model.
"""

from palimpsest.runtime.roles import role, JobSpec, context_spec


@role(
    name="worker",
    description="Factorio-specific worker role with RCON integration",
    role_type="worker",
    needs=[],  # ADR-0018: Explicit empty capability (fixture doesn't need real runtime)
)
def worker(**params):
    """Factorio team worker role definition.

    This shadows the global worker role for factorio bundle.
    Per ADR-0018: No legacy hooks, uses capability model.
    """
    return JobSpec(
        context_fn=context_spec(
            system="You are a Factorio worker agent. Manage game state via RCON.",
            sections=[
                {
                    "kind": "static",
                    "content": "Use factorio_tool to interact with the game server.",
                }
            ],
        ),
        tools=["bash", "factorio_tool"],
    )
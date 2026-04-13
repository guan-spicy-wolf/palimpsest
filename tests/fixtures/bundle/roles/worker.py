"""Bundle fixture worker role.

Per ADR-0018: Uses capability-only lifecycle model.
"""

from palimpsest.runtime.roles import role, JobSpec, context_spec


@role(
    name="worker",
    description="Bundle fixture worker role",
    role_type="worker",
    needs=[],  # ADR-0018: Explicit empty capability
)
def worker(**params):
    """Bundle fixture worker role definition.

    Per ADR-0018: No legacy hooks, uses capability model.
    """
    return JobSpec(
        context_fn=context_spec(
            system="You are a worker agent.",
            sections=[],
        ),
        tools=["bash", "factorio_tool"],
    )
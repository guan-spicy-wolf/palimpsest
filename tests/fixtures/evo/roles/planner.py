"""Global planner role - available to all teams."""

from palimpsest.runtime.roles import role, JobSpec, context_spec, workspace_config


@role(
    name="planner",
    description="Global planner role for task decomposition",
    role_type="planner",
)
def planner(**params):
    """Global planner role definition."""
    return JobSpec(
        preparation_fn=workspace_config(new_branch=False),
        context_fn=context_spec(
            system="You are a planner agent. Decompose goals into tasks.",
            sections=[],
        ),
        publication_fn=lambda **kw: None,
        tools=["spawn"],
    )
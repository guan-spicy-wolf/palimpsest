"""Global worker role - available to all teams."""

from palimpsest.runtime.roles import role, JobSpec, context_spec, workspace_config, git_publication


@role(
    name="worker",
    description="Global worker role for general tasks",
    role_type="worker",
)
def worker(**params):
    """Global worker role definition."""
    return JobSpec(
        preparation_fn=workspace_config(),
        context_fn=context_spec(
            system="You are a global worker agent. Execute tasks precisely.",
            sections=[],
        ),
        publication_fn=git_publication(strategy="branch"),
        tools=["bash"],
    )
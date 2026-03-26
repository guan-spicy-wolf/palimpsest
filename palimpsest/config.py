from yoitsu_contracts.config import (
    EvalContextConfig,
    EventStoreConfig,
    JobConfig,
    JobContextConfig,
    JoinContextConfig,
    LLMConfig,
    PublicationConfig as _BasePublicationConfig,
    ToolsConfig,
    WorkspaceConfig,
)


class PublicationConfig(_BasePublicationConfig):
    """Extended PublicationConfig with trust model modes.
    
    Publication modes:
    - branch_only: Commit and push to branch only, no PR created (automated trust)
    - pr_draft: Commit, push, and create draft PR (review suggested)
    - approval_required: Commit, push, mark for human approval (human required)
    """
    
    mode: str = "branch_only"  # "branch_only" | "pr_draft" | "approval_required"


__all__ = [
    "EventStoreConfig",
    "EvalContextConfig",
    "JobConfig",
    "JobContextConfig",
    "JoinContextConfig",
    "LLMConfig",
    "PublicationConfig",
    "ToolsConfig",
    "WorkspaceConfig",
]

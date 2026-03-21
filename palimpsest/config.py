from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class WorkspaceConfig:
    repo: str = ""
    branch: str = "main"
    depth: int = 1
    git_token_env: str = ""


@dataclass
class LLMConfig:
    model: str = "claude-sonnet-4-6"
    api_base: str = ""
    api_key_env: str = "ANTHROPIC_API_KEY"
    max_iterations: int = 50
    temperature: float = 0.0
    # Generation parameters
    max_tokens: int = 4096
    top_p: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    # Retry configuration (exponential backoff)
    max_retries: int = 3
    retry_initial_delay: float = 1.0  # seconds
    retry_max_delay: float = 60.0  # seconds
    retry_backoff_factor: float = 2.0
    # Anthropic-specific cache control
    anthropic_cache_system: bool = True
    anthropic_cache_tools: bool = True


@dataclass
class ToolsConfig:
    builtin: dict[str, dict] = field(default_factory=dict)
    disabled_builtins: list[str] = field(default_factory=list)


@dataclass
class PublicationConfig:
    strategy: str = "branch"
    branch_prefix: str = "palimpsest/job"
    max_recovery_attempts: int = 1


@dataclass
class EventStoreConfig:
    url: str = ""
    api_key_env: str = ""
    source_id: str = "palimpsest-agent"


@dataclass
class JobConfig:
    job_id: str = ""
    task: str = ""
    role: str = "default"
    timeout: int = 0  # job wall-clock timeout in seconds; 0 = no limit
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    publication: PublicationConfig = field(default_factory=PublicationConfig)
    eventstore: EventStoreConfig = field(default_factory=EventStoreConfig)

    @classmethod
    def from_yaml(cls, path: str) -> JobConfig:
        with open(path) as f:
            data = yaml.safe_load(f) or {}

        return cls(
            job_id=data.get("job_id", ""),
            task=data.get("task", ""),
            role=data.get("role", "default"),
            timeout=data.get("timeout", 0),
            workspace=WorkspaceConfig(**data.get("workspace", {})),
            llm=LLMConfig(**data.get("llm", {})),
            tools=ToolsConfig(**data.get("tools", {})),
            publication=PublicationConfig(**data.get("publication", {})),
            eventstore=EventStoreConfig(**data.get("eventstore", {})),
        )

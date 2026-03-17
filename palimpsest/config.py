from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class WorkspaceConfig:
    repo: str = ""
    branch: str = "main"
    depth: int = 1


@dataclass
class ContextConfig:
    system_prompt: str = "default"
    recent_events: int = 10


@dataclass
class LLMConfig:
    model: str = "claude-sonnet-4-6"
    api_base: str = ""
    api_key_env: str = "ANTHROPIC_API_KEY"
    max_iterations: int = 50
    temperature: float = 0.0


@dataclass
class ToolsConfig:
    builtin: dict[str, dict] = field(default_factory=dict)


@dataclass
class PublicationConfig:
    strategy: str = "branch"
    branch_prefix: str = "palimpsest/job"


@dataclass
class EventStoreConfig:
    url: str = ""
    api_key_env: str = ""
    source_id: str = "palimpsest-agent"


@dataclass
class JobConfig:
    task: str = ""
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    publication: PublicationConfig = field(default_factory=PublicationConfig)
    eventstore: EventStoreConfig = field(default_factory=EventStoreConfig)

    @classmethod
    def from_yaml(cls, path: str) -> JobConfig:
        with open(path) as f:
            data = yaml.safe_load(f) or {}

        return cls(
            task=data.get("task", ""),
            workspace=WorkspaceConfig(**data.get("workspace", {})),
            context=ContextConfig(**data.get("context", {})),
            llm=LLMConfig(**data.get("llm", {})),
            tools=ToolsConfig(**data.get("tools", {})),
            publication=PublicationConfig(**data.get("publication", {})),
            eventstore=EventStoreConfig(**data.get("eventstore", {})),
        )

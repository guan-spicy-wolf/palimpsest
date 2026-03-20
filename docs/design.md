# Palimpsest Runtime — Project Design

This document describes what this repository actually is and how it works.

## Scope

Palimpsest is the **Agent Runtime** (skeleton) portion of the self-evolving agent system described in `architecture.md`. It is a single-job execution engine that:

1. Receives a task configuration
2. Sets up an isolated workspace (git clone + branch)
3. Assembles LLM context from the evo repo
4. Runs an agent loop (LLM ↔ tools)
5. Publishes results (git commit + push)
6. Emits events to an external EventStore throughout

The **Supervisor** (orchestration, version management, fork-join coordination) lives in a separate repository and consumes events emitted by this runtime.

## Module Map

```
palimpsest/
├── runner.py              Main 4-stage pipeline orchestrator
├── config.py              JobConfig dataclasses (YAML-driven)
├── events.py              Event data models (Pydantic)
├── emitter.py             HTTP EventStore client
├── cli.py                 CLI entry point
├── runtime/
│   ├── event_gateway.py   Transparent event capture gateway
│   ├── interfaces.py      ContextProvider + ToolProvider ABCs
│   ├── resolver.py        Generic one-shot provider loader
│   └── roles.py   Role template → JobSpec expansion
├── gateway/
│   ├── llm.py             LiteLLM wrapper with retry + events
│   ├── tools.py           Builtin tools (bash, spawn) + CompositeGateway
│   └── tool_loader.py     Evo tool provider resolution + EvoToolGateway
└── stages/
    ├── workspace.py       Clone repo, create job branch
    ├── context.py         Assemble LLM context from providers
    ├── interaction.py     Agent loop (LLM calls + tool execution)
    ├── publication.py     Git commit + push
    └── finalization.py    Publication guardrails + cleanup

evo/                       Evolvable repository (muscle)
├── roles/                 Role definitions (YAML)
├── prompts/               System prompts (Markdown)
├── contexts/              ContextProvider implementations (Python)
└── tools/                 ToolProvider implementations (Python)
```

## Pipeline

```
run_job(config)
  │
  ├─ RoleManager.resolve(role_name) → JobSpec
  │
  └─ _run_job_from_spec(config, spec)
       │
       ├─ Stage 1: Workspace
       │    setup_workspace() → clone + branch
       │    emit job.started (evo_sha, base_sha)
       │
       ├─ Stage 2: Context
       │    build_context() → {system, task}
       │    Loads ContextProviders from evo/contexts/
       │
       ├─ Stage 3: Interaction
       │    while True:
       │      run_interaction_loop()
       │        LLM call → tool execution → repeat
       │        Ends on: task_complete | max_iterations | no-tool-call
       │
       │      find_publication_issues()
       │        If issues + can retry → inject user prompt, re-enter loop
       │        If issues + no retry → fail
       │        If clean → proceed
       │
       └─ Stage 4: Publication
            publish_results() → git commit + push
            emit job.completed
```

## Event Schema

All events pass through the transparent EventGateway. The Agent never touches the emission mechanism.

| Event Type | Data Model | Key Fields |
|-----------|-----------|------------|
| `job.started` | JobStartedData | job_id, workspace_path, evo_sha, base_sha |
| `job.completed` | JobCompletedData | job_id, status, git_ref, summary |
| `job.failed` | JobFailedData | job_id, error, code, traceback |
| `job.runtime.issue` | RuntimeIssueData | job_id, stage, message, fatal, code |
| `job.stage.transition` | StageTransitionData | job_id, from_stage, to_stage |
| `job.spawn.request` | SpawnRequestData | job_id, tasks, wait_for |
| `agent.llm.request` | LLMRequestData | job_id, model, messages_count, iteration |
| `agent.llm.response` | LLMResponseData | job_id, model, finish_reason, tokens, duration_ms |
| `agent.tool.exec` | ToolExecData | job_id, tool_name, tool_call_id |
| `agent.tool.result` | ToolResultData | job_id, tool_name, success, duration_ms |

## Error Codes

`JobFailedData.code` and `RuntimeIssueData.code` carry machine-readable codes for Supervisor consumption:

| Code | Meaning |
|------|---------|
| `duplicate_tool_name` | Evo and builtin tools have conflicting names |
| `publication_guardrail` | Publication preflight detected sensitive files |
| `timeout` | Job exceeded wall-clock timeout |
| `cleanup_failed` | Workspace cleanup failed (non-fatal) |

## Key Design Decisions

- **Role is a template, not runtime identity.** See ADR-001.
- **Spawn emits events, does not execute.** See ADR-002.
- **Providers load in isolated scope.** See ADR-003.
- **Only task_complete can end a job.** See ADR-004.
- **Publication recovery re-enters the agent loop.** See ADR-005.

## Configuration

```yaml
task: "description"
role: "default"
timeout: 600              # wall-clock seconds, 0 = no limit

workspace:
  repo: "https://..."
  branch: "main"
  depth: 1
  git_token_env: "GIT_TOKEN"

llm:
  model: "claude-sonnet-4-6"
  api_base: ""
  api_key_env: "ANTHROPIC_API_KEY"
  max_iterations: 50
  temperature: 0.0

tools:
  builtin:
    bash:
      timeout: 60
      output_limit: 4096
  disabled_builtins: []

publication:
  strategy: "branch"
  branch_prefix: "palimpsest/job"
  max_recovery_attempts: 1

eventstore:
  url: "http://..."
  api_key_env: ""
  source_id: "palimpsest-agent"
```

## What This Repo Does NOT Do

- **Supervisor / orchestration** — separate repo
- **Version state machine** — Supervisor's responsibility; runtime only reads current checkout and records evo_sha
- **Self-evolution execution** — the architecture supports it, but the end-to-end loop (agent modifies evo → CI → merge → version advance) is orchestrated by Supervisor
- **Event stream querying for orchestration** — Supervisor queries events; runtime only emits

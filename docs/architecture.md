# Self-Evolving Agent System — Architecture

Draft v0.4 · 2026-03

## 1 System Overview

A self-evolving Agent system built on Git repositories and an event stream. The Agent completes external tasks while discovering and improving its own capabilities by modifying an evolvable repository.

Two evolution axes:

- **Context assembly** — how to select and compose relevant context from the event stream
- **Workflow orchestration** — how to assign prompts and coordinate sub-tasks

Two real data sources: **Git repositories** and the **event stream**. All other state (Supervisor orchestration state, active evo version) is derived and can be rebuilt from these sources.

Core design principle: split the Agent into an **immutable skeleton (Runtime)** and **freely evolvable muscle (evo repository)**. The evo repo is a normal Git repository; evolution uses the same mechanics as external tasks — branch, modify, commit, merge.

## 2 Core Components

### 2.1 Event Stream

The event stream is one of two persistent sources of truth. All components are state machines whose transitions produce events.

**Event envelope:**

| Field  | Description |
|--------|-------------|
| id     | Unique event ID |
| source | Origin (Agent Runtime, Supervisor, CI, external client) |
| ts     | Timestamp |
| type   | Event type |
| data   | Payload with causal-relationship fields |

Causal fields (job_id, commit_sha, workflow_instance_id, etc.) are embedded in `data`, not at the top level. Different event sources have different causal structures.

**Projection:** EventStore extracts key fields from `data` into domain tables for efficient querying. These are derived data that can be rebuilt.

**Event sources:**

- Agent Runtime: LLM request/response, tool exec/result, stage transitions, job lifecycle, spawn requests
- Supervisor: start/pause/resume/abort, spawn/trigger/resume
- CI/CD: build triggers, test results, deploy status
- External clients: task requests, human feedback

### 2.2 Git Repositories

Two types:

- **Task repo** — external code the Agent works on, isolated branch per job
- **Evo repo** — the Agent's own definitions (prompts, context templates, tools, roles). Modifying this repo *is* self-evolution.

Both use the same branch model: each job works on an isolated branch; results merge after validation.

### 2.3 Agent Runtime (Skeleton)

The immutable infrastructure layer. Responsibilities:

- **LLM communication** — calls, retries, timeouts
- **Tool execution framework** — load and dispatch tools, enforce sandbox boundaries
- **Sandbox management** — isolated workspace per job
- **Transparent event capture** — all events are emitted by the Runtime; the Agent cannot touch the event mechanism
- **Evo repo version reading** — records which evo commit was used for each job

The Runtime sits between Agent and EventStore as a transparent gateway. Event capture is split into:

- **Runtime-level (fully transparent):** LLM request/response, tool call/result, stage transitions, job lifecycle. Automatic, Agent-invisible.
- **Business-level (via stable tools):** spawn requests are explicit tool calls. The tool interface is stable and not evolvable.

### 2.4 Supervisor

The orchestration layer (separate repository). Responsibilities:

- Listen to events, dispatch Agent jobs
- Resolve Role definitions from the evo repo's active commit
- Manage fork-join lifecycle (spawn/trigger/resume)
- Enforce permission boundaries
- Pause and escalate after repeated job failures

## 3 Evo Repository

Directory structure:

- `prompts/` — system prompts, instruction templates
- `contexts/` — context provider implementations (Python `ContextProvider` subclasses)
- `tools/` — tool provider implementations (Python `ToolProvider` subclasses)
- `roles/` — role definitions combining prompt + context template + tool list

All files live under one commit — a commit is a complete, self-consistent snapshot. No cross-version compatibility issues.

### 3.1 Roles

A Role is a named combination of prompt, tool set, and context template. Roles support single-level inheritance. Roles are used only at job creation; the Runtime expands them into a flat `JobSpec` and never references the role name again.

### 3.2 Version Management

Git commit SHA is the version number. The Supervisor tracks an "active commit" pointer. Version progression and rollback are the Supervisor's responsibility, not the Runtime's. The Runtime records `evo_sha` in its `job.started` events so the Supervisor can correlate job outcomes with evo versions.

## 4 Permission Model

| Layer     | Content | Permission |
|-----------|---------|------------|
| Locked    | Runtime code, event gateway, sandbox, EventStore, schema validation | Agent cannot touch. Changes require PR. |
| Stable    | spawn tool interface, evo directory structure conventions | Agent can use but not modify. Convention changes require PR. |
| Free      | All files in the evo repo (prompts, contexts, tools, roles) | Agent evolves freely via Git. |

## 5 Workflow Primitives

### 5.1 Fork-Join

The only orchestration primitive: fork-join + failure trigger.

- Initiator spawns child tasks (specifying role and parameters)
- Trigger condition: `all_complete` or `any_failed` → resume initiator
- Initiator decides next steps on resume (may fork again)

Expressiveness: sequential (spawn [A], wait, spawn [B]), parallel (spawn [A, B], wait all), conditional branching (decide on resume), nesting (children can fork), iteration (resume then fork again).

### 5.2 Job Lifecycle

Every job ends in success or failure. No infinite hangs.

- **Normal:** transparent events at each stage → completion event
- **Failure:** retry first → commit WIP → follow-up event for continuation
- **Supervisor protection:** pause and escalate after repeated startup failures

## 6 Self-Evolution Loop

### 6.1 Evolution Path

Modify files in the evo repo: context templates, prompts, role definitions, tool implementations. Changes go through normal branch → CI → merge flow.

### 6.2 Validation (Dual Gate)

- **Hard gate (immediate):** new version must pass CI/smoke test; first job must start successfully. Failure triggers automatic rollback by the Supervisor.
- **Soft gate (record now, enforce later):** quantifiable metrics (LLM call rounds, extra queries, task completion time) are recorded in the event stream. Version progression events include `changed_files` for precise A/B comparison.

### 6.3 Evaluation Anchors

To avoid Goodhart's Law:

- Automated tests (hard metrics, always on)
- External output quality (objective measure)
- LLM cross-review (on demand; scoring prompt is outside evolution scope)
- Human feedback (high latency, highest trust)

## 7 System Invariants

- **Single source of truth:** Git repos + event stream only. Everything else is derived.
- **Skeleton/muscle separation:** Runtime is immutable. Evo repo is freely evolvable.
- **Transparent event capture:** Agent cannot touch event emission.
- **Three-layer permissions:** locked / stable / free boundaries enforced by Supervisor.
- **Event schema stability:** field names and semantics are stable within each event type.
- **Job terminal determinism:** every job ends in success or failure.
- **Branch isolation:** job output does not affect trunk until merged.

## 8 Implementation Phases

- **Phase 0 — Minimal loop:** Fixed-role Agent → events + branch → Supervisor merges.
- **Phase 1 — Evo repo:** Prompts and context templates move from hardcoded to repo. Role resolution, version reading.
- **Phase 2 — Context assembly:** Event stream query tools. Agent retrieves context from the stream.
- **Phase 3 — Fork-join:** Supervisor orchestration. Agent spawns child tasks.
- **Phase 4 — Self-evolution:** Agent modifies evo repo. Hard gate enabled.
- **Phase 5 — Optimization loop:** Context template optimization tasks. Soft gate comparison.

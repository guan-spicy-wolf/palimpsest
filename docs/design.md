# Palimpsest Design

Palimpsest is the stable runtime skeleton. It executes exactly one job configuration and emits the corresponding events.

The evo repo remains the free surface:

- prompts
- roles
- context providers
- evo-defined tools

## Core Decisions

### 1. Role Resolves To A Flat Job Spec

Roles are authoring conveniences. At runtime Palimpsest resolves a role into:

- prompt text
- context template
- evo tool names

After resolution, the runtime only sees the flat job spec.

### 2. Context And Tools Stay Python-Native

Evo providers are loaded with isolated `importlib` scopes. They do not leak into `sys.modules`, and they can be replaced between jobs without restarting the runtime.

For the current single-bundle MVP, the materialized `evo_root` is also treated as a Python import root. This allows runtime-executed evo modules to import shared code such as `teams.<team>.lib.*` while still being loaded from file paths via `importlib`.

The current implementation establishes this by injecting the materialized `evo_path` into `sys.path` at job start. This is an explicit runtime contract for team-specific roles, tools, and context providers, not a preparation-function concern. A future multi-bundle design may replace this with a more scoped import strategy, but the invariant remains: evo-executed modules must be able to import team-local shared libraries.

### 3. Task Lifecycle Is Owned By Trenni

Palimpsest does not infer or broadcast task state. It only manages execution of a single job:

- the interaction loop ends when the agent naturally stops calling tools
- budget exhaustion exits through `job.completed(code="budget_exhausted")`
- publication commits changes and emits `job.completed`

If the runtime itself fails, it emits `job.failed`.

Trenni observes these job events and structurally derives whether the parent task has reached a terminal state (`task.completed`, `task.failed`, `task.partial`, `task.cancelled`).

### 4. Publication Guardrails Re-enter The Loop

If publication guardrails fail but recovery attempts remain, Palimpsest injects a user message and returns to the interaction loop with preserved conversation state. The job does not fork or schedule additional work by itself.

### 5. Join Context Is Still Runtime Context, Not Scheduler Logic

Join jobs are ordinary jobs from Palimpsest's perspective. The only difference is that their job config may contain join context metadata, which context providers can use to query Pasloe and reconstruct child task state.

## Pipeline

The runtime pipeline is:

1. workspace
2. context
3. interaction
4. publication

The pipeline is linear except for the publication-recovery loop.

## Non-Goals

- no scheduling
- no replay
- no checkpointing
- no worker-capacity management
- no event-stream ownership
- no evo version selection policy

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

### 3. Task State And Job Result Are Separate

The interaction loop produces a task state:

- `complete`
- `failed`
- `in_progress`
- `blocked`
- `needs_review`

After publication succeeds, the runtime emits:

- `task.updated`
- `job.completed`

If the runtime itself fails, it emits `job.failed`.

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

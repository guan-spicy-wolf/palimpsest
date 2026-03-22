# Palimpsest — Design

Palimpsest is the **skeleton**: immutable, single-job execution engine. It does one thing — run a task and emit events. The **evo repo** is the muscle: prompts, context providers, tools, and roles that the agent evolves freely.

The runtime never imports from the evo repo at startup. Evo providers are loaded per-job via isolated `importlib` scopes and discarded after the job ends.

## Pipeline

Four sequential stages: workspace → context → interaction → publication.

The interaction and publication stages are coupled: publication guardrails can re-enter the interaction loop (up to `max_recovery_attempts` times) rather than failing the job outright. This is the only non-linear part of the pipeline.

Job status semantics:
- `success` — agent called `task_complete` and publication succeeded
- `partial` — agent stopped without completing (iteration limit, provider errors, etc.); changes are committed with a `wip:` prefix and pushed
- `failed` — agent explicitly declared failure, or a runtime constraint was violated; nothing is pushed

## What This Repo Does NOT Do

- **Orchestration** — no fork-join, no retry policy, no version state machine. That is Trenni's responsibility.
- **Event querying** — the runtime only emits. Trenni queries.
- **Evo version selection** — the runtime reads whatever is checked out at `./evo` and records the SHA. Trenni controls which commit is active.

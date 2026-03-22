# Palimpsest — Design

Palimpsest is the **skeleton**: immutable, single-job execution engine. It does one thing — run a task and emit events. The **evo repo** is the muscle: prompts, context providers, tools, and roles that the agent evolves freely.

All roles, tools, and context providers are defined in pure Python (not YAML). This eliminates runtime failures from config syntax errors and lets the LLM work with type-checked code rather than opaque configuration.

## Key Decisions

**Role is a template, not runtime identity.** A Role is expanded into a flat `JobSpec` at job creation time. After that, the role name is never referenced — the Runtime operates solely on the `JobSpec`. This means roles can be freely renamed or restructured without affecting in-flight jobs.

**Evo providers are loaded in isolated scopes.** Provider files from the evo repo are loaded via `importlib` into isolated namespaces, never registered in `sys.modules`. Each job gets a fresh load; providers can be swapped between jobs without restart and cannot pollute the Runtime's namespace.

**Only `task_complete` can end the interaction loop successfully.** If the LLM stops producing tool calls, the Runtime sends one follow-up prompt requesting explicit completion. `terminal=True` from any other tool is ignored. This prevents premature job endings from ambiguous LLM behavior.

**Publication guardrails re-enter the agent loop rather than fail immediately.** When guardrails fire and recovery attempts remain, the Runtime injects a user message explaining the issue and resumes the loop with the accumulated message history. The agent must call `task_complete` again after fixing the problem. This avoids starting a new job for recoverable issues.

## Pipeline

Four sequential stages: workspace → context → interaction → publication.

The interaction and publication stages are coupled via the recovery mechanism above — it is the only non-linear part of the pipeline.

Job status semantics:
- `success` — agent called `task_complete` and publication succeeded
- `partial` — agent stopped without completing (iteration limit, provider errors, etc.); changes are committed with a `wip:` prefix and pushed
- `failed` — agent explicitly declared failure, or a runtime constraint was violated; nothing is pushed

## What This Repo Does NOT Do

- **Orchestration** — no fork-join, no retry policy, no version state machine. That is Trenni's responsibility.
- **Event querying** — the Runtime only emits. Trenni queries.
- **Evo version selection** — the Runtime reads whatever is checked out at `./evo` and records the SHA. Trenni controls which commit is active.

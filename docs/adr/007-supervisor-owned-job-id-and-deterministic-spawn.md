# ADR-007: Supervisor-owned Job ID and Deterministic Spawn Targeting

## Status

Accepted

## Context

In our autonomous agent architecture, Palimpsest acts as the pure task runtime engine, while the external Supervisor handles orchestration (such as starting, stopping, branching, and fork-joining). Previously, there were two areas of responsibility leak and ambiguity:

1. **Job ID Generation**: Palimpsest generated its own `job_id` via UUID fallback when it wasn't specified. This disrupted the Supervisor's ability to strictly own identity logic and traceability. 
2. **Generic Role Spawning**: When the Agent invoked the `spawn` builtin tool, it simply passed along the abstract `role` name (e.g., `{"role": "default"}`). The child job expansion was thus deferred, causing ambiguity about exactly *which* codebase state the child task should run. This introduced the risk of version drift across the fork-join parallel processing cluster if the repository `evo_sha` updated in the middle of a complex branching execution.

## Decision

1. **Job ID is Strictly Passed Down**: We eliminated the `uuid` fallback generation in the Palimpsest runner. Palimpsest now terminates with a `ValueError` if a `job_id` is not strictly provided within the `JobConfig`. Job identity generation and tracing strictly belong to the Supervisor.
2. **Pre-bound Spawn References**: The `spawn` builtin tool now intercepts role configurations requested by the Agent, expanding them strictly into hard file paths (`role_file`) locked to the orchestrating runner's current `evo_sha` (`role_sha`). 

## Consequences

- **Determinism**: Fork-joined child tasks are immutably tied into the exact code version (`evo_sha`) that initiated their spawn event, preventing unexpected behavioral drift during distributed or extended parallel workflows.
- **Traceability**: All job lifecycle logs, sub-branches, and event storage strictly match the globally managed job schemas without random identifier mismatches.
- **Simpler Architecture**: The Palimpsest agent runtime continues to trend toward acting as a purely deterministic and dumb executor, dependent heavily on concrete context bounds explicitly provided by orchestrators.

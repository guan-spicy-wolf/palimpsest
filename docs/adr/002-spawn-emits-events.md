# ADR-002: Spawn Emits Events, Does Not Execute

## Status

Accepted

## Context

An earlier implementation had the `spawn` tool executing child jobs inline (sequentially, in-process). This was a placeholder that:

- Gave the Agent a false promise of fork-join semantics
- Made the runtime responsible for child job orchestration
- Coupled the runtime to Supervisor-level concerns

## Decision

The `spawn` builtin tool emits a `job.spawn.request` event containing the task list and wait condition. It does NOT execute child jobs. The external Supervisor consumes this event and handles the actual fork-join orchestration.

## Consequences

- The runtime is a single-job engine with no orchestration responsibility.
- The Supervisor is the sole owner of fork-join lifecycle.
- The Agent receives a confirmation that the spawn request was emitted, not the child results. The Supervisor will resume the parent job when the wait condition is met.
- ~150 lines of inline spawn execution code were removed from the runtime.

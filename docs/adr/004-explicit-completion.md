# ADR-004: Only task_complete Can End a Job Successfully

## Status

Accepted

## Context

LLMs can stop producing tool calls for various reasons (confusion, mid-thought pause, wrong finish_reason). Treating "no tool calls" as task completion led to premature job endings.

Additionally, other tools (e.g., bash) could accidentally set `terminal=True`, ending the job from an unexpected source.

## Decision

- Only an explicit `task_complete` tool call can terminate the interaction loop successfully.
- If the LLM stops producing tool calls, the runtime sends one follow-up prompt requesting explicit `task_complete`. If the LLM still doesn't comply, the job is marked `partial`.
- `terminal=True` from any tool other than `task_complete` is ignored by the runtime.
- The interaction loop returns its accumulated message history so the job can resume (e.g., for publication recovery).

## Consequences

- No accidental job termination from ambiguous LLM behavior.
- The Agent must make an explicit decision to end the job.
- `partial` status signals that the Agent stopped without completing, allowing the Supervisor to decide on retry.
- The runtime owns the termination decision; `task_complete` is a signal, not an override.

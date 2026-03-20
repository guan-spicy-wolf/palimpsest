# ADR-005: Publication Recovery via Agent Loop Re-entry

## Status

Accepted

## Context

Publication guardrails (e.g., detecting committed secrets) can block publication after the Agent has already called `task_complete`. Previously, this required starting a new job.

## Decision

When publication guardrails fire and recovery attempts remain, the runner:

1. Emits a `job.runtime.issue` event with `code="publication_guardrail"`
2. Transitions back from `publication` to `interaction` stage
3. Injects a user prompt explaining the issue and asking the Agent to fix the workspace
4. Re-enters the interaction loop with the accumulated message history
5. The Agent must call `task_complete` again after fixing the issue

Recovery attempts are bounded by `publication.max_recovery_attempts` (default: 1). Exhausting retries fails the job with a `publication_guardrail` error code.

## Consequences

- Publication failures are recoverable within the same job — no need for a new job.
- The Agent sees a natural user message explaining the issue, not a special API.
- The message history is preserved, so the Agent has full context of what it already did.
- The Supervisor can distinguish guardrail failures from other failures via the error code.

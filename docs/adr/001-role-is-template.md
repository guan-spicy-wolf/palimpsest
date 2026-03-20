# ADR-001: Role Is a Template, Not Runtime Identity

## Status

Accepted

## Context

Early implementations had the runner directly consuming `role` names at execution time. This made the runtime dependent on evo-layer naming, blurring the skeleton/muscle boundary.

## Decision

A Role is a convenience template used only at job creation. `RoleResolver.resolve()` expands a role name into a `JobSpec` — the flat, self-contained execution configuration. After expansion, the role name is no longer referenced. The runtime operates solely on the `JobSpec`.

## Consequences

- The runtime never imports or depends on role names at execution time.
- The same job can be reproduced from a `JobSpec` alone, regardless of which role produced it.
- Roles can be freely evolved (renamed, restructured) without affecting in-flight jobs.
- `source_role` is kept in `JobSpec` as informational metadata only.

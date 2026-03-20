# ADR-003: Provider Isolation via importlib

## Status

Accepted

## Context

The runtime loads Python implementations (ToolProvider, ContextProvider) from the evo repository. These are untrusted in the sense that they are evolvable — the Agent can modify them.

Standard `import` would register evo modules in `sys.modules`, causing:

- Global namespace pollution
- Stale module caching across jobs
- Potential conflicts if multiple evo versions are loaded

## Decision

A generic `resolve_providers()` function loads `.py` files using `importlib.util` into isolated namespaces. Modules are **never** registered in `sys.modules`. The resolver:

1. Scans a directory for `.py` files
2. Loads each into an isolated module object
3. Finds subclasses of the requested ABC
4. Instantiates and returns them, keyed by their declared names
5. Filters to only the requested set

The same resolver function serves both tool and context providers.

## Consequences

- No `sys.modules` pollution — evo code is truly ephemeral.
- Each job gets a fresh load of evo providers.
- Provider implementations can be swapped between jobs without restart.
- The resolver is 72 lines, shared across all provider types.

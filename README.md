# Palimpsest

Palimpsest is the single-job runtime for the [Yoitsu](https://github.com/guan-spicy-wolf/yoitsu) stack.

Its scope is intentionally narrow:

- prepare one workspace
- assemble context
- run the LLM and tool loop
- publish results
- emit job events consumed by Trenni

When the runtime reaches a hard budget limit such as `max_iterations`, it exits
cleanly through `job.completed` with `code="budget_exhausted"`. Trenni maps
that to `task.partial`.

It does not schedule siblings, evaluate spawn conditions, or own checkpoint state. Those responsibilities live in Trenni.

The current component design is documented in [docs/design.md](docs/design.md). System-level architecture and the merged ADR live in the umbrella repo.

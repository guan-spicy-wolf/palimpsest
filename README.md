# Palimpsest

Palimpsest is the single-job runtime for the [Yoitsu](https://github.com/guan-spicy-wolf/yoitsu) stack.

Its scope is intentionally narrow:

- prepare one workspace
- assemble context
- run the LLM and tool loop
- publish results
- emit task and job events

It does not schedule siblings, evaluate spawn conditions, or own checkpoint state. Those responsibilities live in Trenni.

The current component design is documented in [docs/design.md](docs/design.md). System-level architecture and the merged ADR live in the umbrella repo.

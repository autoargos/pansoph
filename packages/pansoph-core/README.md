# pansoph-core

The thin assembly. It wires the ports to their configured adapters, discovered as plugins, and exposes the platform surface. It owns no domain logic of its own beyond orchestration.

## Surfaces (the UI is just a consumer of these)

- REST API for chat, search, connector administration, and access administration.
- An MCP server, so other agents can use pansoph permission aware.
- An SSE stream following the AI SDK UI Message Stream protocol, so any frontend can render streaming answers with citations. The shipped React app is the reference consumer and is fully replaceable.

## Adapter selection

Every port reads its adapter from configuration (see `.env.example`) and resolves it from the matching entry point group, so an operator swaps an implementation without touching code. `none` is valid for the authz port (open or single tenant) and for identity (built in store).

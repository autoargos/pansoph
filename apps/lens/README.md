# lens

The reference web UI for pansoph. Verification first: every answer shows where it came from, and you can only open what you already have access to.

lens is a replaceable consumer of agora's contracts (REST, MCP, SSE). It is not a dependency of the backend. Replace it with your own frontend, or drive pansoph purely through the API.

## Stack (scaffolded in task #11)

- React 19 with Vite, TypeScript, Tailwind v4, shadcn/ui.
- assistant-ui for the thread shell and the streaming runtime (`useDataStreamRuntime`).
- Vercel AI Elements for the verification UX: `InlineCitation`, `Sources`, `Reasoning`, `Response`.
- Refine for the connector and access administration screens.
- Streaming over SSE using the AI SDK UI Message Stream protocol.

## Screens

Chat, search, connector administration, access administration. Citations render as numbered pills with hover previews and deep links to the exact passage. Sources open only if the user can access them. When an answer comes from world knowledge, no citation is shown.

Built to a static `dist/` and served by nginx (see `deploy/Dockerfile.lens`).

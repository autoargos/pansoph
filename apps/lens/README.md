# lens

The reference web UI for pansoph. Verification first: every answer shows where it came from, and you can only open what you already have access to.

lens is a replaceable consumer of agora's contracts (REST, MCP, SSE). It is not a dependency of the backend. Replace it with your own frontend, or drive pansoph purely through the API.

## Stack (implemented)

- React 19 with Vite, TypeScript (strict), Tailwind v4.
- A custom SSE client (`src/api.ts`) that consumes agora's `/api/chat` stream (POST + stream reader, since the chat endpoint is a POST and browser EventSource is GET only), parsing source parts and text deltas in the AI SDK UI Message Stream shape.
- Inline citation rendering: `[n]` markers in the answer become pills linked to a sources rail.
- A dev proxy to agora (`vite.config.ts`), so `pnpm dev` runs against a local backend.

## Screens

- **chat**: streaming answer with inline `[n]` citations and a permitted-sources rail. When the answer comes from world knowledge, no citation is shown.
- **search**: answer-first list of permitted chunks with scores.
- **admin**: the "everything replaceable" view, the active adapter for each port and the other installed adapters, plus a cloud-model banner.

A demo identity bar sets the acting user and groups. Retrieval enforces permissions through the authz bulk check regardless of what is typed there.

## Planned richer layer

assistant-ui (thread shell), Vercel AI Elements (`InlineCitation`, `Sources`, `Reasoning`, `Response`), and Refine for admin are the planned upgrade. The current UI is dependency-light and runnable today; the contracts it consumes (REST + SSE) are stable, so swapping in those libraries does not change the backend.

Built to a static `dist/` and served by nginx (see `deploy/Dockerfile.lens`).

# company-memory-mcp (DOOR3 fork package)

- `company_memory_mcp.server`: the Company Memory MCP server (`company-memory-mcp`).
  Streamable HTTP, bearer auth validated at the transport, and tools that read the verified
  principal from the request context. No tool takes a credential.
- `company_memory_mcp.service`: tool logic over the pansoph enforcement path.
- `company_memory_mcp.knowledge`: the write tools (add, list, delete manual knowledge).
  The creator and tenant come from the token; the ACL is private by default or a group
  scope allowed by `publish_policy`. Indexing goes through the normal `IngestionPipeline`.
- `company_memory_mcp.knowledge_store`: the manual source records and the connector
  adapter the pipeline reads them through.
- `company_memory_mcp.publish_policy`: who may publish to which scope, and who reads it
  (`CM_PUBLISH_POLICY_FILE`).
- `company_memory_mcp.identity`: the `company_memory` identity adapter (OIDC, verified
  email principal, configured users only via `CM_REQUIRED_GROUPS`).
- `company_memory_mcp.real_users` and `cli_real`: the real-user local profile
  (`company-memory user ...`, `company-memory verify-real`).
- `company_memory_mcp.agora_app`: the agora app for the Company Memory deployment. It drops
  `/api/grab` and the API docs, refuses unsafe config, and turns on audit logging.
- `company_memory_mcp.cli`: the local demo CLI (`company-memory`). See
  `docs/company-memory/DEMO.md`.
- `company_memory_mcp.cli_demo`, `demo_check`, `demo_state`: `company-memory demo-reset`
  (a deterministic local-only reset through supported paths), `demo-check` (a read-only
  preflight, exit 0 only when the demo is ready), and `demo-ready` (both). `demo_state`
  runs inside the MCP container via `docker compose exec`, and only when
  `CM_DEMO_ENV=local-poc`.

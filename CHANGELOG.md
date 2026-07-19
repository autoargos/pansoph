# Changelog

All notable changes to pansoph are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and pansoph aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
once it reaches 1.0. Before then, minor versions may include breaking changes,
which will always be called out under a Changed or Removed heading.

## [Unreleased]

This is the first public release of pansoph: an open source, self hostable,
modular knowledge platform whose defining feature is document level access
control enforced at retrieval, built in and fully removable.

### Added

- Enforcement path: hybrid retrieval that overfetches candidates, runs one authz
  bulk check as the source of truth, keeps only the allowed subset, then reranks.
  The vector pre filter is a speed optimization and never the authority.
- Authz port with a SpiceDB default adapter and an OpenFGA alternate, plus ACL to
  relationship tuple mappers with union semantics and instance scope narrowing.
- Connector SDK: a declarative manifest runtime, a Python escape hatch, and one
  shared conformance suite that every connector must pass.
- Native connectors: Confluence, Jira, Google Drive, Slack, Notion, GitHub, Gmail,
  and a public link fetcher, each mapping its source ACL model faithfully and
  refreshing its own OAuth token at sync time.
- Ingestion: parse, chunk, contextualize, and embed as idempotent durable steps,
  with incremental sync and deletion handling.
- Model ports: LLM, Embedder, and Reranker are all bring your own model, local
  first by default (any OpenAI compatible endpoint), cloud opt in with a banner.
- Identity port with a built in store and OIDC (Keycloak) support.
- agora: the FastAPI assembly exposing a stable REST API, an MCP server, and an
  SSE stream.
- lens: the React reference UI, reskinned to the "aperture" design language, with
  connectors management, permission aware search and chat, an access explorer, and
  a system health view. Light and dark themes are both first class.
- Background sync with a stop control and orphan run cleanup on boot.
- Permission red team test suite as a standalone CI gate.

### Security

- Secrets never live in code, logs, or committed files. The repository ships a
  gitleaks pre-commit hook and CI secret hygiene test.
- Per connector credential isolation with sealed refresh tokens that rotate on use.

[Unreleased]: https://github.com/autoargos/pansoph/commits/main

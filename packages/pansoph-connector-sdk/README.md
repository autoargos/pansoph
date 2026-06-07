# pansoph-connector-sdk

One contract, three tiers, many instances. This package is usable on its own: depend on it, implement the `Connector` protocol or write a declarative manifest, and you have a connector with no other pansoph package required.

The runtime implements pagination, retries, backoff, incremental cursors, and resumable sync once, so every connector inherits them. Connectors are declarative first (a YAML manifest describes simple REST sources) with a Python escape hatch for the awkward cases. This follows the lesson from Airbyte's low code connector framework.

## The contract

```
Connector
  capabilities: { incremental, permission_sync, deletions, slim_listing }
  auth:         oauth2 | api_token | service_account | none
  validate(config)            -> ok | error
  list_changes(cursor)        -> [DocRef], next_cursor      # incremental sync
  fetch(DocRef)               -> RawDoc + Acl
  list_acls(DocRef)           -> Acl                        # when permission_sync
  on_webhook(event)           -> [change]                   # optional, for freshness
```

## Tiers

- Tier 1 native: first party connectors (Google Drive, GitHub, Confluence, Granola).
- Tier 2 reach anything: public link fetcher, and the MCP bridge that turns any MCP server into a connector (see `pansoph-mcp-bridge`).
- Tier 3 grabber: a browser extension that ingests what the signed in user can already see, with consent.

## Discovery

Connectors register under the `pansoph.connectors` entry point group. The platform discovers them as plugins. Each source supports many independent instances, each with its own credentials, schedule, and ACL namespace.

## Conformance

Every connector must pass the shared conformance suite, which exercises the contract, incremental sync, deletions, and ACL mapping.

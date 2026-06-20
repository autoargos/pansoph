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

The suite is CAT style: a per connector `conformance.yaml` separates config from expectations, and the engine runs one category per concern.

```yaml
connector: github
categories:
  spec: {}
  connection: { expect_ok: true }
  discovery: {}
  read: { expected_records: fixtures/expected_records.jsonl }
  incremental: { enabled: true, abnormal_state: fixtures/future_state.json }
  deletions: { enabled: true }
  permissions:                       # mandatory for every connector
    source_acl: fixtures/permissions/source_acl.json
    principals: fixtures/permissions/principals.json
    expected:   fixtures/permissions/expected_acl.json
```

Run it from a test with `run_conformance(path_to_yaml, factory)`, where `factory(instance_id, config)` builds your connector. Wrap the call in `respx` (hand authored hermetic fixtures) or `vcrpy` (live recorded cassettes); the engine itself does no HTTP, so it stays transport agnostic.

The permission category is mandatory and gating. For every document it asserts the produced `AccessControl` equals the expected one by set equality (public, allow_groups, allow_users, deny), and that every synthetic principal sees exactly what the expected ACL implies, including explicit negatives and deny precedence. Oversharing fails as hard as under sharing. The category runs under `@pytest.mark.permission`, the same standalone gate that guards retrieval enforcement.

A simple REST source needs no Python: write a manifest and `compile_manifest(manifest)` returns a class that satisfies the `Connector` protocol and passes the identical suite.

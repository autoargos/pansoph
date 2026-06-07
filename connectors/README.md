# pansoph connectors

Community connectors live here (this directory will be split into its own repository, `pansoph-connectors`, so it can move at its own pace).

A connector is anything that satisfies the contract in `pansoph-connector-sdk`. Two ways to write one:

1. Declarative. Describe a simple REST source with a YAML manifest. The runtime implements pagination, retries, backoff, incremental cursors, and resumable sync for you. This is the default and covers most sources.
2. Code. For awkward sources, implement the `Connector` protocol in Python. You still inherit the runtime's durability.

## Tiers

- Tier 1 native: `google-drive`, `github`, `confluence`, `granola` (via the MCP bridge).
- Tier 2 reach anything: the public link fetcher, and any MCP server through `relay`.
- Tier 3 grabber: the `scout` browser extension.

## Rules every connector follows

- Many instances. A connector is configured per instance, each with its own credentials, schedule, and ACL namespace. Every document is tagged with its instance.
- Permission sync. If the source exposes ACLs, map them. Source ACL to authz tuple mapping is how document level access control stays correct.
- Never bypass authentication. No anti bot, no CAPTCHA solving, no credential replay. Respect robots and rate limits.

## Conformance

Every connector must pass the shared conformance suite (task #13), which exercises the contract, incremental sync, deletions, and ACL mapping. A connector that does not pass does not merge.

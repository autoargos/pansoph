# pansoph-connector-granola

A pansoph connector for Granola meeting notes. Granola ships no bulk transcript API, so it is reached through its MCP server: this connector is the pansoph MCP bridge (`pansoph_mcp_bridge.bridge.MCPConnector`) branded for Granola. Point an instance at the Granola MCP endpoint and the resources that server exposes become access-controlled documents. Each configured instance carries its own MCP endpoint and its own ACL namespace (the instance id), so scoped instances never collide.

## What it syncs

- Granola meeting notes and transcripts, surfaced as the resources the Granola MCP server exposes. Listing maps to MCP `resources/list`; each resource becomes one document.
- Each document's text is the concatenation of the text contents returned by MCP `resources/read`, its `source_doc_id` is the resource URI, and its MIME type is `text/plain`.
- An instance can be confined to a subset of resources with `resource_uri_prefix`: only resources whose URI starts with that prefix are ingested. Left empty, all resources are ingested.

## Setup

An operator connects an instance with the config fields defined by the connector spec:

- `server_url` (required): the streamable HTTP URL of the Granola MCP server to bridge, for example `https://mcp.granola.example/mcp`. The Granola MCP URL and any token behind it are the operator's to provide. `validate()` rejects a missing `server_url`.
- `public` (optional, bool): expose the ingested Granola resources to everyone in the tenant.
- `allow_groups` (optional, list): groups allowed to read the ingested Granola resources.
- `resource_uri_prefix` (optional): confine this instance to resources whose URI starts with this prefix, so one MCP server can back several scoped instances. Leave empty to ingest all resources.

The connector's declared auth scheme is `AuthScheme.oauth2` and none of the config fields is a credential/secret kind: authentication to the Granola MCP server is handled at the MCP endpoint the operator supplies, not by a token stored on the connector.

## Permission model (ACL mapping)

MCP resources carry no access control, so the ACL is set locally by the instance configuration, not imported from Granola. Every ingested document gets the same ACL, built by the bridge:

- `public` from the `public` config field. When true the documents are readable by the whole tenant.
- `allow_groups` from the `allow_groups` config field.
- `principal_ns = instance_id`, so this instance's principals live in their own namespace and never collide with another instance's.

This is fail-closed by construction: with neither `public` set nor any group in `allow_groups`, no group and no user is granted access, so a document is readable by no one until the operator explicitly grants it. `allow_users` and `deny` are left empty. `list_acls` returns this same instance-level ACL for every resource.

## Sync

- Listing walks MCP `resources/list` and yields one `DocRef` (`source_doc_id = resource URI`, `instance_id`) per resource, skipping any whose URI does not match the configured `resource_uri_prefix`.
- The cursor emitted alongside each ref is empty, and `list_changes` ignores the incoming cursor: every sync re-lists the server's current resources. There is no server-side change feed or incremental cursor.
- Deletions are not surfaced. `on_webhook` returns no change events, and the listing yields only the resources currently present rather than tombstones for resources that disappeared.

## Known limits

- Granola has no bulk transcript API; ingestion is limited to whatever the Granola MCP server exposes as resources.
- No incremental sync and no change feed: each sync re-lists all resources rather than fetching a delta.
- No deletion detection: a resource removed at the source is not surfaced as a tombstone, and `on_webhook` is a no-op.
- ACLs are set locally on the instance (public or a set of groups), because MCP resources carry no access control; the source's own permission model is not imported.

## Conformance

This connector passes the shared conformance suite (`assert_conforms` from `pansoph-connector-sdk`), run hermetically with `uv run pytest -m conformance`.

## References

- Model Context Protocol: https://modelcontextprotocol.io
- Granola: https://www.granola.ai

Reached via the Model Context Protocol (Granola's MCP server), using the same verified MCP client path as the relay bridge. See the header of `src/pansoph_connector_granola/connector.py` and `pansoph_mcp_bridge.bridge.MCPConnector`.

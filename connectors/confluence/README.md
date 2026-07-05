# pansoph-connector-confluence

A pansoph connector for Confluence Cloud.

`src/pansoph_connector_confluence/connector.py` is the authoritative implementation
(the `manifest.yaml` in this directory is an unused stub, since Confluence's ACL
cascade and change feed do not fit the declarative runtime). Its module header records
the official Atlassian docs verified on 2026-07-05, the endpoints, the auth and scopes,
the incremental cursor, the deletion signal, and the ACL model.

ACL fidelity is the priority. Confluence VIEW restrictions cascade to descendants and
the restrictions API returns only directly-applied restrictions, so the connector
reconstructs the inherited cascade across ancestors and intersects the layers, never
widening a restricted page to the space audience. An unrestricted page falls back to
the real space View principals from the v2 space-permissions endpoint.

It passes the shared conformance suite (`assert_conforms` from `pansoph-connector-sdk`),
exercised in `tests/test_confluence_conformance.py`, which also pins the inherited
restriction (no widening), the trashed-page deletion signal, the space-wide fallback,
and groupId-keyed restriction groups. Run the tests with:

```
uv run --no-sync pytest connectors/confluence -q
```

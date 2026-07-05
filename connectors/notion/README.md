# pansoph-connector-notion

A pansoph connector for Notion.

Implemented against the latest official Notion API documentation. See the header of
`src/pansoph_connector_notion/connector.py` for the verified endpoints, the mandatory
`Notion-Version` header, the bearer auth, cursor pagination, the deletion signal, and
the verified date (2026-07-05).

It passes the shared conformance suite (`assert_conforms` from
`pansoph-connector-sdk`), wired in `tests/test_notion_conformance.py`.

## What it ingests

- `list_changes` pages through `POST /v1/search` filtered to pages, yielding one
  `DocRef` per shared page. A page that Notion reports as `in_trash` or `is_archived`
  is yielded as a tombstone (`deleted=True`) and never ingested as live content.
- `fetch` reads `GET /v1/blocks/{id}/children` and joins the `plain_text` of every
  block's rich text into a plain text document.

## Access control: the integration is the principal

A Notion integration behaves like a **restricted user**. `POST /v1/search` returns
**only the pages that have been shared with the connection**, and results adhere to
the connection's capabilities. So the integration's reachable set is a real,
observable permission signal: what the connector can see is exactly what search
returns.

Notion has no single "which principals can see this page" endpoint, so a full
per-principal readout is not constructible from the API. The faithful, minimal ACL
that never overshares is therefore: **each ingested page is readable only by this
instance's integration principal**, namespaced by the instance. The principal id is
the bot id read from `GET /v1/users/me` (cached), overridable via config
`integration_principal`. This mirrors the gmail connector's owner-only model: coarse,
but it can never grant a page to anyone the integration could not already reach.

Because the connector maps a real (if coarse) source-scoped ACL,
`capabilities.permission_sync` is `True` and `list_acls` returns the integration
grant for a page.

### `public` is an explicit, audited override

| config key              | type   | default | meaning                                           |
| ----------------------- | ------ | ------- | ------------------------------------------------- |
| `public`                | bool   | `False` | audited override: publish every page to the instance |
| `integration_principal` | string | (auto)  | override the ACL principal; else bot id from `/v1/users/me` |

`public` **never** defaults the grant wider than the integration scope. Setting
`public: true` is a deliberate decision to publish every ingested page to every user
in the instance, regardless of who could see it in Notion. Leave it off (the default)
to keep access scoped to the integration principal.

### Operator responsibility

1. Scope the instance's Notion integration to only the pages you intend to expose.
   The connector's ACL follows that scope automatically.
2. Only set `public: true` when you truly intend a tenant-wide grant, and audit it.
3. Use separate instances (each with its own integration token and ACL namespace)
   when different page sets need different audiences.

On Business/Enterprise, Notion supports page-level access rules expressed through
`Person` / `Created-by` page properties, and user enumeration via `GET /v1/users`.
Enriching the ACL to those real per-page principals is a documented follow-up, not a
current feature.

## Config

```json
{
  "token": "<notion integration or oauth token>",
  "base_url": "https://api.notion.com",
  "public": false,
  "integration_principal": ""
}
```

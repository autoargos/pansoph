# pansoph-connector-notion

A pansoph connector for Notion.

Implemented against the latest official Notion API documentation. See the header of
`src/pansoph_connector_notion/connector.py` for the verified endpoints, the mandatory
`Notion-Version` header, the bearer auth, cursor pagination, and the verified date.

It must pass the shared conformance suite (`assert_conforms` from
`pansoph-connector-sdk`) before it merges.

## What it ingests

- `list_changes` pages through `POST /v1/search` filtered to pages, yielding one
  `DocRef` per page, with the search cursor checkpointed by the runtime.
- `fetch` reads `GET /v1/blocks/{id}/children` and joins the `plain_text` of every
  block's rich text into a plain text document.

## ⚠️ CRITICAL: Notion sharing is NOT mirrored

**Notion exposes NO API that returns who can see a page.** There is no per document
permission endpoint, so this connector CANNOT and DOES NOT mirror real Notion
sharing. This is a hard limitation of the Notion API, not a missing feature.

Instead, **access is whatever the operator configured for this instance**, applied
uniformly to every page the instance ingests:

| config key     | type | default | meaning                                  |
| -------------- | ---- | ------- | ---------------------------------------- |
| `public`       | bool | `False` | every user in this instance can see it   |
| `allow_groups` | list | `[]`    | these groups can see every ingested page |
| `allow_users`  | list | `[]`    | these users can see every ingested page  |

Because there is no real permission data to sync, `capabilities.permission_sync` is
`False`. The conformance permission category still runs (it is mandatory for every
connector) and asserts the connector reports exactly the configured ACL.

### Operator responsibility

If you set `public: true`, **you publish every ingested page to every user in this
instance, regardless of who could see it in Notion.** To keep access correct:

1. Scope the instance's Notion integration to only the pages you intend to expose.
2. Set `public` / `allow_groups` / `allow_users` to match the exact audience you
   intend for those pages.
3. Use separate instances (each with its own integration token and ACL namespace)
   when different page sets need different audiences.

Treat the configured ACL as the source of truth for access, because Notion gives us
nothing else to go on.

## Config

```json
{
  "token": "<notion integration or oauth token>",
  "base_url": "https://api.notion.com",
  "public": false,
  "allow_groups": [],
  "allow_users": []
}
```

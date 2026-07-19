# pansoph-connector-weblink

The public-link fetcher (Tier 2, reach-anything). It ingests a list of directly-fetchable public web pages that the operator supplies, with no credentials. Each configured URL becomes one document in pansoph.

## What it syncs

The connector fetches exactly the URLs listed in its config, nothing more. It does not crawl, follow links, or discover pages.

- `list_changes` yields one `DocRef` per configured URL (keyed by the URL itself).
- `fetch` issues an HTTP GET with a descriptive `User-Agent` and a browser-like `Accept` header. Redirects are followed manually, up to 5 hops, and every hop is re-checked by the SSRF guard before the request.
- The response body is stored as-is: `text` is the raw `resp.text`, `mime` is taken from the `Content-Type` header, `title` is the last path segment of the URL, and `source_url` is the URL. There is no HTML-to-clean-text extraction, so point it at text, markdown, or otherwise clean URLs for the best result.

## Setup

No OAuth, no credentials. Configure per instance with these fields (from `WeblinkConnector.spec`):

| Field | Kind | Required | Meaning |
|---|---|---|---|
| `urls` | list | yes | Absolute `http(s)` URLs to ingest, one per line. `validate` fails if empty. |
| `public` | bool | no (default `true`) | Expose ingested pages to everyone in the tenant. |
| `allow_groups` | list | no | Groups allowed to read these pages. |
| `user_agent` | string | no | Sent on every fetch. Defaults to `pansoph/0.1 (+https://github.com/autoargos/pansoph)`. Some sites (for example Wikipedia) reject generic bot agents with 403, so set a descriptive agent with your own contact URL. |

Example config:

```json
{ "urls": ["https://example.com/a", "https://example.com/b"], "public": true, "allow_groups": [] }
```

## Permission model (ACL mapping)

There is no source-side ACL to import, so the connector applies a locally-configured `AccessControl` to every document (`_acl`):

- `public` from config (default `true`), meaning readable by the whole tenant.
- `allow_groups` from config.
- `principal_ns` set to the instance id, so principals from different instances never collide.
- `allow_users` and `deny` are left at their empty defaults; this connector does not set them.

`list_acls` returns the same ACL for every document. The auth scheme is `AuthScheme.none`.

## Sync

- Poll-based. Each sync re-yields every configured URL and re-fetches it. The input cursor is ignored and an empty cursor (`""`) is emitted.
- No change detection: `DocRef.updated_at` is not populated, so there is no conditional-fetch or ETag handling. Every page is fetched fresh each run.
- No deletions from the source: the connector never emits a `deleted` tombstone. A page is removed from pansoph by removing its URL from `urls`.
- No webhooks: `on_webhook` returns an empty list.

## Boundaries (mandatory to document)

This connector never bypasses authentication. There is no anti-bot handling, no CAPTCHA solving, and no credential replay. It fetches only openly reachable URLs and stores whatever the origin returns, honoring the server's response (including 4xx/5xx via `raise_for_status`).

It identifies itself honestly on every request through a descriptive `User-Agent` that names pansoph and gives a contact URL, which is the correct non-bypass answer to sites that enforce a User-Agent policy. It does not spoof a browser identity.

An SSRF guard (`_assert_public_url`) runs on the initial URL and on every redirect hop. It permits only `http(s)` schemes and only hosts that resolve entirely to public addresses. Loopback, RFC1918/ULA private ranges, link-local (which includes the `169.254.169.254` cloud-metadata address), reserved, multicast, and unspecified addresses are blocked, so a configured or redirected URL cannot make the worker read internal services or cloud metadata.

Rate limits and retry/backoff are owned by the connector runtime, not re-implemented here, in line with the project rule to respect robots and rate limits and never bypass authentication.

## Known limits

- No HTML-to-clean-text extraction; the raw response text is stored.
- No change detection; every configured URL is re-fetched on every sync.
- DNS rebinding is a residual risk: the SSRF guard resolves and validates the host, but does not pin the resolved IP for the actual request. Pinning is the noted stronger follow-up.
- Redirect chains longer than 5 hops are rejected as an error.

## Conformance

This connector passes the shared conformance suite (`assert_conforms` from `pansoph-connector-sdk`), run hermetically with `uv run pytest -m conformance`.

## References

- Module header verified 2026-07-19: `src/pansoph_connector_weblink/connector.py`.
- Wikimedia User-Agent policy: https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy

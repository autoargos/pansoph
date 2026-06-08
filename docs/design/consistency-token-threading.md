# Design note: durable consistency-token threading

Status: proposed, awaiting sign-off. Scope: the deferred P0-security item from
`docs/audits/2026-06-08-adr-alignment.md`. Building-block lens: the token lives with
the data it describes; no new service.

## Problem

ADR-0002: retrieval runs one SpiceDB `bulk_check` `at_least_as_fresh` using the
stored consistency token, so a revocation is never read stale. Today
`retrieve()` passes `at_least_as_fresh=None`, and the SpiceDB adapter falls back to
an in-memory `_last_token` (the last write this process saw). That token is lost on
restart and not shared across processes, so on a cold or load-balanced agora the
check degrades to `minimize_latency` (no freshness guarantee): a revoked document
can be served until the next write warms the token. Correct for the single-process
demo, wrong for multi-process / Helm.

Key fact we can rely on: **SpiceDB revisions are globally monotonic.** A ZedToken
obtained from a later write encodes a revision >= every earlier write. So checking
at the freshest token among a candidate set guarantees freshness >= every
candidate's last ACL change, including a revocation on any one of them.

## Option A (recommended): per-document token in the vector payload

At ingest, `write_tuples` already returns a `ConsistencyToken`. Store it on each
chunk together with a monotonic comparator (the ingest timestamp, `acl_written_at`).
At query, `hybrid_search` returns both; `retrieve()` picks the token with the max
`acl_written_at` across candidates and passes it as `at_least_as_fresh`.

- Correct: by global monotonicity, the freshest candidate token dominates all.
- Precise: freshness is only as strict as the retrieved set needs (not the whole DB).
- Building block: the `VectorStore` payload carries `consistency_token` +
  `acl_written_at`; the `Authz` block is untouched; no new service.
- Cost: two columns / payload fields, one `max()` in `retrieve()`.

Edge case: a brand-new tenant with zero candidates returns `[]` (already handled);
a candidate ingested before this change has an empty token and is simply skipped in
the max, falling back to the next freshest, or to `None` if none carry one (a
one-time re-sync sets them).

## Option B (simpler, less precise): durable global watermark

Persist the latest write token to a one-row table (`pansoph_authz_watermark`,
optionally per tenant); update on every `write_tuples`; load at query and pass it.

- Correct and trivial, but over-strict: every read waits for the revision of the
  most recent ACL write anywhere (or per tenant), adding latency under a hot write
  stream.
- Couples the freshness source to a small Postgres table (still a building block,
  but a second store touch on the read path).

## Recommendation

Option A. It keeps the token with the data, leaves the AuthzPort surface unchanged,
needs no new service, and is the most precise. Implementation touches `SearchHit`
(+`consistency_token`, +`acl_written_at`), both `VectorStore` adapters (persist +
return), `ingest_one` (write tuples first, stamp the hits, then upsert), and
`retrieve()` (max-token selection). A red-team test ingests, revokes, and asserts the
revoked doc disappears when the check is threaded with the stored token.

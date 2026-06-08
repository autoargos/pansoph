# ADR alignment audit, 2026-06-08

Five parallel read-only audits against ADR-0001 (vision, connector tiers, access control) and
ADR-0002 (finalized stack, ports). Each scored alignment and produced file:line evidence. This is
the synthesis. The governing lens is ADR-0002's number one principle: total replaceability,
everything is a building block.

## Scorecard

| Dimension | Score | One line |
|---|---|---|
| Roles / RBAC / identity / token | 2/10 | No role concept anywhere; only users and groups |
| Connector scoping and ACL namespacing | 3/10 | Per-instance namespace silently collapsed to tenant; no first-class scope |
| lens UI/UX | 3/10 | 3 of 4 ADR screens; Access admin missing; actions ungated |
| Security and permission correctness | 6/10 | Enforcement shape correct; deny filter missing; admin surface open |
| Feature coverage vs ADR-0002 stack | ~57% | Ports defined, defaults exist; durability, contextual retrieval, sealing absent |

The permission enforcement core, the port architecture, and five native connectors are genuinely
functional. The owner's specific concern (org/group/individual connector instances managed by
role) is real and confirmed across three independent audits.

## P0, correctness and security, must fix before any demo that claims the headline feature

1. **`deny` is never enforced.** `AccessControl.deny` exists and is documented as a final filter,
   but no filter exists in `pansoph-retrieval/pipeline.py`. A document that says "allow group:eng,
   deny user:alice" is returned to alice. Building block: the `Authz`/retrieval block. Carry `deny`
   in the `SearchHit` payload at upsert, apply as the last step after `bulk_check`.
2. **Per-instance ACL namespace is collapsed (isolation bug).** `pansoph-ingestion/pipeline.py:93`
   overwrites every connector's `principal_ns = instance_id` with `tenant_id`. Two instances in one
   tenant share a namespace, contradicting ADR-0001 "two instances never collide." Paired with a
   namespace mismatch in `mappers.py` membership tuples. Needs the namespace-model decision below.
3. **Admin surface is unauthenticated.** `POST/DELETE /api/connectors/instances`, `.../sync`, and
   `/api/grab` take no token. Any caller can create a connector (exfil credentials), delete any
   connector, or inject content into any tenant. The MCP `pansoph_search` trusts a caller-supplied
   `user`/`tenant_id`. Building block: a roles-aware authorization guard that calls the Identity
   port, applied as a FastAPI dependency.
4. **Qdrant pre-filter leak.** The group filter uses `should` (optional OR) so it does not enforce
   `tenant AND (public OR group-overlap)` the way the Postgres adapter does. Restricted docs enter
   the overfetch pool. (bulk_check still gates the final answer, so not a leak to the user, but the
   pre-filter is wrong.) Building block: `VectorStore` Qdrant adapter.
5. **Consistency token discarded.** Retrieval calls `bulk_check(at_least_as_fresh=None)` and falls
   back to an in-memory `_last_token`, lost on restart and not shared across processes. Revocations
   can read stale on a cold instance. Store the per-document ZedToken at ingest, thread it to
   retrieval. Building block: `Authz` + retrieval.

## P1, the owner's headline concern, role-based multi-scope connector management

6. **No roles, end to end.** `User` has `group_ids` but no `roles`. OIDC maps only a `groups` claim.
   No realm role mapper, no `roles_claim` in the adapter, no API guard, no UI gating. Groups gate
   document retrieval (correct, keep); roles must gate management actions (new, orthogonal).
   Building block: roles flow through the Identity port. Realm mapper -> token `roles` claim ->
   OIDC adapter reads it -> `require_role` FastAPI dependency -> lens hides actions. Never coupled
   to Keycloak; the built-in store sets `User.roles` directly, Zitadel uses project roles.
7. **No first-class scope on a connector instance.** Scope (org vs group vs individual; which
   folder/space/repo) lives only in free-form `config`. No `scope_type`, no validated config
   schema, no `connector.validate(config)` call before insert. Drive and MCP cannot even be
   confined to one folder/resource-prefix. Building block: a generic SDK concept, `ConnectorMeta`
   carries a config + scope schema so every connector inherits it and the admin UI renders it
   generically. Single-field additions to Drive (`folder_id`) and MCP (`resource_uri_prefix`).
8. **Instance ownership / management roles.** No notion of who may manage an instance. ADR direction:
   an org admin creates org-scoped instances, a group/connector admin manages their own scope, a
   member is read-only. Needs `created_by`/`owner_scope` on the instance and a check in the manager.
9. **Access administration screen is missing.** ADR names it a primary screen. It is the only place
   the headline feature is visible: users, groups, role assignment, document-ACL inspector, a
   who-can-see-what query, revocation/re-sync. Building block: lens stays a consumer of the API;
   the screen is built on a roles-aware data layer (Refine's access-control provider is the ADR
   choice).

## P2, Phase-1 coverage and quality (close before calling Phase-1 done)

- **Durable execution not running.** `scribe/worker.py` is a sleep loop; `workflow_dbos.py` exists
  but is never registered. Production sync uses the in-process interval scheduler (no crash-resume,
  no cross-replica coordination). DBOS is an ADR differentiator.
- **Connector credentials plaintext.** `EnvelopeSecretStore` is implemented and tested but not wired
  into `assembly.py`/`ConnectorManager`. Tokens land in `connector_instances.config` jsonb.
- **Contextual retrieval hardwired off.** `contextualize=False` at every production callsite, and the
  prompt-cache hint is dropped by the LLM adapter (ADR marks caching mandatory).
- **Qdrant sparse missing; pgvectorscale DiskANN index not created.** Hybrid is real only on the
  Postgres tier (dense + tsvector + RRF k=60).
- **Postgres RLS FORCE written but not applied** (adapter never sets `app.tenant`); third isolation
  layer inactive.
- **Drive/Confluence incremental sync is a full re-list** though `Capabilities.incremental=True`.
- **Confluence space-inheriting pages get a synthetic `space:` group with no members** -> denied to
  everyone; needs space-member sync.
- **Frontend libraries not adopted:** assistant-ui, AI Elements, Refine absent; chat citations have
  no hover-preview or deep-link; everything hand-rolled. Token system and primitives are clean.
- **Valkey, pgmq, object store, OTel provisioned but unwired.** Eval harness has custom NDCG/MRR but
  no RAGAS/DeepEval and no golden set. Real connectors never run through `assert_conforms` (only the
  in-memory reference does). Manifest compiler is `NotImplementedError`. Images use `:latest`.

## Decisions that are the owner's to make (blocking P1)

- **D1 Namespace model.** Recommended: group objects are tenant-scoped (`{tenant}_{group}`),
  user-reader tuples are instance-scoped (`{instance}_{user}`); drop the `ingest_one` override.
  This makes IdP-group auth and per-instance isolation both hold (today only one can).
- **D2 Roles model.** Recommended: `admin`, `connector_admin`, `member`, carried as a flat `roles`
  claim, plus instance `owner_scope` (org/group/user) for delegated management.
- **D3 Scope as an SDK concept.** Add `scope_type` + a validated config schema to `ConnectorMeta`;
  call `validate(config)` before insert; add `folder_id` (Drive) and `resource_uri_prefix` (MCP).
- **D4 Frontend libraries.** Adopt Refine for the admin + Access screens and assistant-ui/AI
  Elements for chat citations, or keep hand-rolled and build the missing pieces by hand.

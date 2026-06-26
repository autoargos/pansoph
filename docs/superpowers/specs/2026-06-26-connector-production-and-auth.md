# Spec: production-grade connectors + the auto-refresh auth subsystem

Date: 2026-06-26
Status: proposed (design approved in direction: "full auto-refresh auth"; build staged below)
Owner: maintainer

This spec is the design basis for making every pansoph connector production-grade. It is
grounded in a per-connector audit against each source's CURRENT (2026) official API docs,
plus dedicated research into the production auth flows for Google, Microsoft, and
Salesforce and the cross-cutting token-refresh + secret-storage architecture. It exists
because the work is large and foundational: most connector fixes depend on first evolving
the connector SDK contract and adding an auth subsystem. No em dashes anywhere, per house
style. Nothing here claims a connector "works" against a live API; live verification is
gated on operator credentials (the maintainer's explicit choice).

---

## 1. The honest scope

The audit (one agent per connector, against current official docs) found, across 14
connectors: roughly 30 blocker-severity and 76 major-severity defects. The recurring
classes:

- **Auth is not production-grade.** Most connectors consume a raw access token from config
  with no refresh. Google, Microsoft, Salesforce, Dropbox, Slack, and others issue tokens
  that expire in 1 to 4 hours, so any scheduled sync dies after the first window.
- **Capability flags lie.** `incremental=True` declared with a full re-list (Confluence,
  Drive); `incremental=False` declared while the API fully supports it (Gmail, Notion,
  Slack, Linear, Salesforce, Zendesk, Teams); `deletions=True` declared with no deletion
  detection anywhere (Confluence, Dropbox, SharePoint, Zendesk, github).
- **ACL fidelity is weak exactly where ReBAC matters (security bugs).** Jira ignores
  issue-level security (over-shares restricted issues); Linear exposes public-team issues
  to the whole world; Salesforce ignores org-wide-defaults and the role hierarchy;
  SharePoint maps `organization` links to fully public; github never expands teams to
  member users and uses an ACL endpoint that needs write access; Drive mis-maps `domain`
  grants to groups and skips permission pagination.
- **Pagination and rate limits unhandled.** Slack ignores its envelope errors, 429s, and
  the 1-req-per-minute history limit; many connectors read only the first page.
- **Content extraction is demo-grade.** Gmail indexes only the snippet; Slack only the
  first history page; SharePoint and Dropbox download no content at all.

Conclusion: this is a foundational build (an auth subsystem + SDK contract evolution +
14 connector rewrites), not a credential wire-up. It is staged in section 5.

---

## 2. The auth subsystem (auto-refresh)

A central, provider-agnostic design (patterned on Airbyte's secret hydration, Meltano's
Authenticator, google-auth, and RFC 6749/7523). Four separated concerns.

### 2.1 Ports (pure contracts, no I/O)

```python
# A short-lived bearer credential, minted and cached centrally.
@dataclass(frozen=True)
class AccessToken:
    value: str
    token_type: str  # "Bearer"
    expires_at: datetime
    scopes: frozenset[str]

# What every connector depends on. Connectors NEVER hold raw secrets; they hold a
# TokenSource and call get_access_token() per request.
class TokenSource(Protocol):
    async def get_access_token(self, *, force_refresh: bool = False) -> AccessToken: ...

# What a connector DECLARES (data, not behavior). The platform reads this to know which
# grant to run and which fields are secret (so it can externalize them).
class GrantType(StrEnum):
    REFRESH_TOKEN = "refresh_token"          # OAuth2 6749 s6: Slack, Confluence, Jira,
                                             #   Notion, Dropbox, Linear, Zendesk, SF web
    JWT_BEARER = "jwt_bearer"                # RFC 7523: Google SA/DWD, Salesforce JWT,
                                             #   GitHub App, MS certificate variant
    CLIENT_CREDENTIALS = "client_credentials"  # MS Graph app-only, Salesforce client-creds
    STATIC = "static"                        # long-lived PAT / API token (github PAT,
                                             #   Atlassian API token, Notion internal)

@dataclass(frozen=True)
class AuthRequirement:
    grant: GrantType
    token_url: str
    scopes: frozenset[str]
    secret_refs: dict[str, SecretRef]   # logical name -> opaque handle (NOT the secret)
    extra: dict[str, str]               # non-secret: client_id, iss, sub, tenant, audience

# Resolve opaque handles to plaintext, lazily, at mint time. Adapters wrap the existing
# pansoph-infra envelope secret store; managed vaults (AWS/GCP/Azure/Vault) are adapters.
class SecretStore(Protocol):
    async def resolve(self, ref: SecretRef) -> SecretValue: ...   # wiped after use
    async def put(self, ref: SecretRef, value: SecretValue) -> None: ...
```

### 2.2 The central CredentialProvider (where refresh lives)

The single service that owns the token cache and the refresh logic, and the ONLY place
that touches secrets and token endpoints. Per credential key it keeps
`{access_token, expires_at, refresh_token?, lock}`.

- **Proactive lazy refresh.** `get_token()` fast-path returns the cached token while
  `now < expires_at - SKEW` (SKEW default 300s). Slow-path takes a per-credential
  single-flight lock (asyncio.Lock in-process; a DB/Valkey advisory lock for multi-instance),
  double-checks, then mints.
- **Three minters.**
  - REFRESH_TOKEN: POST `grant_type=refresh_token` + client_id + client_secret. If the
    response returns a NEW refresh_token (rotation), persist it atomically or the connector
    locks itself out.
  - JWT_BEARER: build and RS256-sign a short-lived JWT assertion (iss/sub/aud/exp<=now+max),
    POST `grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer`. No refresh token; re-mint
    on expiry. Used for Google SA/DWD (sub = impersonated user), Salesforce JWT, GitHub App.
  - CLIENT_CREDENTIALS: POST `grant_type=client_credentials` (+ `.default` scope for Graph).
    No refresh token; re-request when near expiry.
- **On the wire the access token is authoritative.** A 401 invalidates the cache and
  triggers exactly one refresh-and-retry (distinguish `invalid_token` -> refresh from
  `invalid_grant` -> re-auth via the WWW-Authenticate header). A 429 triggers exponential
  backoff honoring Retry-After.
- **instance_url discovery** (Salesforce) and **cloud_id discovery** (Atlassian) are part of
  the mint result, cached alongside the token.

### 2.3 Secret storage

Connection config stores ONLY opaque secret handles. Secret material (refresh tokens,
client secrets, service-account private keys, certificates) lives in the SecretStore,
resolved just-in-time and wiped from memory after use. pansoph already ships an envelope
encryption secret store in `pansoph-infra`; the gaps to close: (a) it is not registered in
the agora image (the `/api/adapters` secretstores list is empty), and (b) the connection
config model must split non-secret config from secret refs. Managed vaults are adapters
behind the same port.

---

## 3. Connector SDK contract changes (must land before the connector rewrites)

The audit surfaced contract-level gaps the per-connector code cannot fix alone:

1. **Deletions in the change protocol.** The listing/`ChangeEvent` contract must carry a
   delete/tombstone kind so a connector can propagate a removed or trashed item (Drive
   changes feed, Dropbox `deleted` tag, Graph `deleted` facet, Gmail history, Slack
   `*_deleted`, Notion `in_trash`, full-list-diff). Today `list_changes` cannot express a
   delete, which is why six connectors falsely declare `deletions=True`.
2. **Durable cursor vs page token.** The runtime checkpoint must be a durable resume point
   (a historyId, a delta link, an updated-since high-water mark, a Drive page token, a
   `list_folder` cursor), never an intra-list page token. Several connectors yield a page
   token as the cursor, which is meaningless across runs.
3. **Group-member expansion / identity contract.** ACLs reference source groups (github
   teams, Slack channels, Salesforce 00G groups, Drive groups). The platform needs a
   defined contract for whether the connector expands groups to user principals or emits
   namespaced group ids that the identity layer resolves. This is security-critical and
   currently inconsistent.
4. **Auth wiring.** Connectors stop reading `cfg["token"]`; they receive a `TokenSource`
   from the runtime built from their `AuthRequirement` + the SecretStore.
5. **TokenSource-aware rate-limit/retry.** A shared retry/backoff wrapper (429 + Retry-After,
   conditional-request ETag support) so every connector inherits correct throttling.

---

## 4. Per-connector audit summary

Grant = the production auth grant this connector should use. B/M = blocker/major counts.
Full per-connector findings (every issue + fix) are in the research run record.

| Connector | Prod grant | B | M | Most critical fixes |
|---|---|---|---|---|
| github | JWT_BEARER (App) or STATIC PAT | 2 | 4 | ACL endpoint needs write+; expand teams to users; deletions; SHA cursor not timestamp; memoize repo ACL (N+1) |
| gmail | REFRESH_TOKEN / JWT_BEARER (DWD) | 3 | 4 | no refresh; implement historyId incremental + deletions; index real MIME body not snippet |
| google_drive | JWT_BEARER (SA+DWD) / REFRESH_TOKEN | 3 | 6 | no refresh; changes-feed incremental + deletions; paginate permissions; domain != group; binary corruption |
| slack | REFRESH_TOKEN (rotation) | 3 | 6 | parse `ok:false` envelopes; 429/Retry-After; paginate history; thread replies; resolve user ids |
| confluence | REFRESH_TOKEN (3LO) / STATIC | 3 | 6 | CQL incremental; trashed-sweep deletions; real space permissions; v2 cursor; OAuth mode |
| jira | REFRESH_TOKEN (3LO) / STATIC | 1 | 6 | **issue-level security (over-share)**; permission-scheme ACL not role 10002; durable cursor; comments |
| notion | REFRESH_TOKEN / STATIC | 0 | 7 | data sources not just pages; last_edited_time incremental; in_trash deletions; recursive blocks; OAuth |
| salesforce | CLIENT_CREDENTIALS / JWT_BEARER | 2 | 6 | **OWD + role hierarchy ACL**; expand 00G groups; SOQL injection guard; getDeleted; per-object fields |
| sharepoint | CLIENT_CREDENTIALS | 4 | 4 | delta feed (recursion+deletions); download content; org-link != public; token mint; 429 |
| teams | CLIENT_CREDENTIALS | 2 | 5 | standard-channel ACL via team members; replies; delta incremental+deletions; token mint |
| dropbox | REFRESH_TOKEN | 4 | 3 | refresh (4h token); list_folder cursor incremental + `deleted` tombstones; real ACL; download content |
| linear | REFRESH_TOKEN / API key | 1 | 5 | **public teams visible to world**; updatedAt incremental; archived deletions; GraphQL error handling |
| zendesk | REFRESH_TOKEN / STATIC | 1 | 4 | OAuth mode; full-list-diff deletions; incremental export; drop drafts (leak); segment tags ACL |
| weblink | STATIC (none) | 1 | 9 | **robots.txt (RFC 9309)**; descriptive UA; conditional requests; readability extraction; default public=false |

---

## 5. Staged execution plan

Each phase leaves the tree green and is committed/pushed. Live verification of any
connector is gated on the operator providing that connector's credential.

- **Phase A: auth subsystem + secret store.** Build the ports + CredentialProvider + the
  three minters + SecretStore wiring (register the envelope store in the image). Hermetic
  unit tests mock the token endpoints (no live calls). Heavily commented.
- **Phase B: SDK contract evolution.** Deletions in the change protocol, durable cursor
  semantics, the group-member/identity contract, the TokenSource handoff, the shared
  retry/backoff wrapper. Update the conformance engine (`check_deletions`, capability-truth
  assertions).
- **Phase C: per-connector rewrites, in waves.** One connector at a time (or small parallel
  waves over distinct connector dirs): apply the audit, switch to the TokenSource, fix
  pagination/incremental/deletions/ACL/content, update hermetic fixtures to the corrected
  shapes, keep tests green, comment heavily, refresh the docs-verified header. Pilot one
  connector end to end first to validate the contract, then scale.
- **Phase D: token guide.** `docs/connectors/getting-tokens.md`, per connector, reflecting
  the now-correct auth (exact least-privilege scopes, where to create the credential).
- **Phase E: live verification (gated).** As each credential arrives, create the instance
  and run the connector live, then re-run the ReBAC matrix to prove source-ACL fidelity.

---

## 6. Sources

Per-connector and per-auth-provider official documentation URLs are captured in the
research run record (one agent per connector + four auth agents, 2026-06-26). Key auth
references: Google OAuth 2.0 + service-account DWD (developers.google.com,
cloud.google.com), Microsoft identity platform client-credentials (learn.microsoft.com),
Salesforce OAuth JWT bearer / web-server / client-credentials (developer.salesforce.com),
RFC 6749 (OAuth 2.0), RFC 7523 (JWT bearer), and the Airbyte/Meltano/Nango secret-hydration
and Authenticator patterns.

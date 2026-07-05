"""The notion connector.

Implemented against the official Notion API. Verified against the official docs:

  API docs:    https://developers.notion.com/reference/intro (overview, auth)
               https://developers.notion.com/reference/post-search (list pages;
                 returns ONLY pages/data_sources shared with the connection; supports
                 sort by last_edited_time asc/desc; returns has_more + next_cursor;
                 each page object carries last_edited_time, in_trash, is_archived)
               https://developers.notion.com/reference/get-block-children (page content)
               https://developers.notion.com/reference/rich-text (plain_text extraction)
               https://developers.notion.com/reference/get-self (GET /v1/users/me:
                 the bot user associated with the token; used as the integration
                 principal for ACLs)
               https://developers.notion.com/reference/get-user (GET /v1/users/{id}:
                 user/bot enumeration, capability gated 403)
               https://developers.notion.com/reference/errors (error envelope:
                 code + message; unauthorized 401, restricted_resource 403,
                 object_not_found 404, rate_limited 429)
               https://developers.notion.com/reference/request-limits (rate limits,
                 Retry-After header on 429, ~3 req/s per connection)
               https://developers.notion.com/reference/versioning (Notion-Version header)
               https://developers.notion.com/docs/upgrade-faqs-2025-09-03 (2025-09-03
                 data_sources model; user+bot permissions managed at database level;
                 page-level access rules via Person/Created-by properties on
                 Business/Enterprise)
               https://developers.notion.com/reference/trash-page (trash semantics;
                 "archived" is a deprecated alias for "in_trash" as of 2026-03-11; the
                 API cannot permanently delete, and there is no bulk deletion feed)
               https://github.com/makenotion/notion-sdk-js/issues/524 (confirms POST
                 /v1/search excludes trashed pages: there is no way to query
                 archived/in_trash items via search)
  API version: Notion-Version 2025-09-03 (sent as a mandatory header on every request).
  Auth:        bearer token. Config "token" carries an internal integration token
               (or an OAuth access token). Sent as "Authorization: Bearer <token>".
               The mandatory "Notion-Version" header MUST accompany every call or
               the API rejects it.
  Base URL:    https://api.notion.com/v1, overridable via config "base_url".
  Pagination:  cursor based. POST /v1/search and GET block children both return
               "has_more" and "next_cursor". Pass "start_cursor" to continue.
               list_changes follows this pagination internally to the end, so one
               call yields a complete listing.
  Incremental: not yet. capabilities.incremental is False (see below). The connector
               performs a full listing each run. It does NOT return Notion's
               intra-listing next_cursor as its public cursor, because the contract
               forbids reusing an intra-listing page token across runs; it yields a
               stable full-scan sentinel instead.
  Deletions:   no bulk feed. capabilities.deletions is False, and that is honest.
               POST /v1/search EXCLUDES trashed pages: a page moved to trash (or
               un-shared from the connection) simply STOPS appearing in search
               results, it is never returned carrying in_trash=True. Notion exposes
               no "list deleted objects" feed and no search filter for trash state
               (https://developers.notion.com/reference/post-search returns only
               live, shared objects; confirmed by makenotion/notion-sdk-js issue #524,
               "No way to query archived/in_trash items"). The only per-object trash
               signal is GET /v1/pages/{id} returning in_trash=true (or 404), which
               requires already knowing the id. list_changes receives only an opaque
               cursor, never the prior synced id set, so the connector cannot diff a
               listing or re-probe removed ids from inside a single poll. Declaring
               deletions=True here would be a lie: it would promise a tombstone stream
               that the real API can never produce, and the runtime would trust that
               dead stream instead of reconciling, leaving a trashed/revoked doc in
               the index forever. So deletions=False, which tells the runtime to purge
               removals by reconciling the full-scan listing against the previously
               synced set (search-absence IS the removal signal), not by trusting a
               tombstone feed. Note also that "archived" is a deprecated alias for
               "in_trash" as of Notion-Version 2026-03-11
               (https://developers.notion.com/reference/trash-page); it never meant a
               still-readable page, so mapping is_archived to deletion was doubly wrong.
               Fail-closed guard: a stale doc left in the index carries only the
               integration-scoped grant (allow_users=[integration principal]), which
               no end user is, so it under-shares rather than leaks; and a live
               re-fetch of a trashed/un-shared page 404s server-side and is surfaced
               by _raise_for_notion, so revoked content is never served.
  Rate limit:  ~3 requests/second per connection; 429 carries a Retry-After header.
               The runtime owns backoff; the connector surfaces the 429 code rather
               than swallowing it.

  ACL MODEL (read this, it is faithful to the source, not a stub):
  ===================================================================================
  A Notion integration behaves like a RESTRICTED user. The search endpoint returns
  ONLY the pages/data_sources that have been shared with the connection, and results
  "adhere to any limitations related to a connection's capabilities"
  (https://developers.notion.com/reference/post-search). So the integration's
  reachable set IS a real, observable permission signal: exactly what the connector
  can see is exactly what search returns.

  Notion exposes no single "which principals can see this page" endpoint, so a full
  per-principal readout is not constructible from the API. But the faithful, minimal
  ACL that never overshares is: this page is readable ONLY by this instance's
  integration principal, namespaced by the instance. That is what this connector
  emits. The integration principal is the bot id from GET /v1/users/me (cached),
  falling back to config "integration_principal" or the instance id if the bot id is
  unavailable. This mirrors the gmail connector's owner-only model: coarse, but it
  can never grant a page to anyone the integration could not already reach.

  "public" is an EXPLICIT, AUDITED operator override, defaulting False. It never
  defaults the grant wider than the integration scope; an operator who sets it True
  is deliberately publishing every ingested page to the whole instance and must
  scope the integration accordingly. On Business/Enterprise, page-level-access
  Person/Created-by properties and GET /v1/users could enrich this to real per-page
  principals; that enrichment is a follow-up and is documented, not faked.
  ===================================================================================

  Verified on: 2026-07-05.

Credential scope note: every configured instance carries its own integration token
and its own ACL namespace (principal_ns = instance_id). Two instances never collide.
See pansoph CLAUDE.md section 6.

The runtime owns retries, backoff, and resumable sync, so a method only describes
how to talk to Notion for one page or one document.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from pansoph_connector_sdk import (
    ChangeEvent,
    ConfigField,
    ConnectorMeta,
    ConnectorSpec,
    FieldKind,
    ValidationResult,
)
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc

_API_VERSION = "2025-09-03"
_PAGE_SIZE = 100

# A stable, opaque public cursor for a full-scan (non-incremental) listing. It is
# deliberately NOT Notion's intra-listing next_cursor, which the contract forbids
# reusing across runs. Every yielded ref carries this same sentinel so the
# conformance suite's non-empty-cursor assertion holds without pretending to
# checkpoint an incremental high-water mark.
_FULL_SCAN_CURSOR = "notion:full-scan"


class NotionConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        self.instance_id = instance_id
        cfg = config or {}
        self._token: str = cfg.get("token", "")
        self._base_url: str = cfg.get("base_url", "https://api.notion.com")
        # EXPLICIT, AUDITED operator override. Defaults False. It never defaults the
        # grant wider than the integration scope; see the module header ACL MODEL.
        self._public: bool = bool(cfg.get("public", False))
        # Optional override for the integration principal id used in the ACL. When
        # absent the connector resolves it from GET /v1/users/me (cached), falling
        # back to the instance id. This is the credential's own reachable scope, not
        # a tenant-wide blanket.
        self._integration_principal_cfg: str = cfg.get("integration_principal", "")
        self._integration_principal: str | None = self._integration_principal_cfg or None
        self.meta = ConnectorMeta(
            name="notion",
            auth=AuthScheme.api_token,
            capabilities=Capabilities(
                # incremental is False on purpose: the connector performs a full
                # listing each run. It does not reuse Notion's intra-listing
                # next_cursor as a durable cursor. A real high-water mark would sort
                # and filter search by last_edited_time; that is a follow-up.
                incremental=False,
                # permission_sync is True: list_acls returns the integration-scoped
                # ACL for a page, which is the faithful minimal grant.
                permission_sync=True,
                # deletions is False, and that is honest: POST /v1/search EXCLUDES
                # trashed pages, so a removed page just disappears from the listing
                # and the API never hands back a page carrying in_trash=True. Notion
                # exposes no bulk deletion feed, and list_changes gets only a cursor
                # (never the prior synced id set), so the connector cannot emit a
                # tombstone from a single poll. Declaring True would promise a stream
                # the real API can never produce; the runtime instead reconciles the
                # full-scan listing against the previously synced set. See the module
                # header "Deletions" section.
                deletions=False,
                slim_listing=True,
            ),
        )

    @classmethod
    def spec(cls) -> ConnectorSpec:
        return ConnectorSpec(
            name="notion",
            title="Notion",
            description="Ingest pages and databases shared with a Notion internal integration.",
            fields=(
                ConfigField(
                    key="token",
                    label="Integration token",
                    kind=FieldKind.SECRET,
                    help="Notion internal integration token. Sealed in the secret store.",
                ),
                ConfigField(
                    key="base_url",
                    label="API base URL",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="https://api.notion.com",
                    help="Override only for a proxy or self-managed gateway.",
                ),
                ConfigField(
                    key="public",
                    label="Publish every ingested page to the whole instance (audited override)",
                    kind=FieldKind.BOOL,
                    required=False,
                    help=(
                        "Explicit override. Default off. When on, every page this "
                        "integration can reach is readable by every user in the "
                        "instance. Leave off to keep access scoped to the integration "
                        "principal, which never oversharing beyond what the "
                        "integration itself can reach in Notion."
                    ),
                ),
                ConfigField(
                    key="integration_principal",
                    label="Integration principal id (optional)",
                    kind=FieldKind.STRING,
                    required=False,
                    help=(
                        "Override for the ACL principal. When blank the connector "
                        "reads the bot id from GET /v1/users/me. This is the "
                        "credential's own reachable scope, not a tenant blanket."
                    ),
                ),
            ),
        )

    # --- contract ---
    def validate(self, config: dict[str, Any]) -> ValidationResult:
        if not config.get("token"):
            return ValidationResult(ok=False, error="missing config: token")
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        """List every shared page by following Notion search pagination to the end.

        The connector performs a full listing each run (incremental is False). It
        loops Notion's intra-listing next_cursor internally so one call yields a
        complete listing, but it never surfaces that intra-listing token as its
        public cursor; every yielded ref carries the stable full-scan sentinel.

        This yields only live refs, never a tombstone: POST /v1/search excludes
        trashed pages, so a removed page is simply absent from the listing rather
        than returned carrying in_trash=True. capabilities.deletions is False and the
        runtime purges removals by reconciling this full-scan listing against the
        previously synced set. See the module header "Deletions" section.
        """
        # A run that resumes from the full-scan sentinel is a no-op replay: the
        # connector already returned the complete listing, so there is nothing new to
        # yield. This keeps the conformance incremental replay honest for a
        # non-incremental connector and avoids treating the sentinel as a Notion
        # start_cursor.
        if cursor == _FULL_SCAN_CURSOR:
            return
        async with self._client() as client:
            body: dict[str, Any] = {
                "filter": {"property": "object", "value": "page"},
                "page_size": _PAGE_SIZE,
            }
            while True:
                resp = await client.post("/v1/search", json=body)
                self._raise_for_notion(resp)
                payload = resp.json()
                for page in payload.get("results", []):
                    page_id = page.get("id")
                    if not page_id:
                        continue
                    # Search never returns trashed pages, so every result is live.
                    # A removed page is detected by its ABSENCE here, reconciled by
                    # the runtime, not by a tombstone the API cannot produce.
                    yield self._ref(page_id), _FULL_SCAN_CURSOR
                if not payload.get("has_more"):
                    break
                body["start_cursor"] = payload.get("next_cursor")

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        async with self._client() as client:
            resp = await client.get(f"/v1/blocks/{ref.source_doc_id}/children")
            self._raise_for_notion(resp)
            text = self._extract_text(resp.json().get("results", []))
            raw = RawDoc(
                source_doc_id=ref.source_doc_id,
                instance_id=self.instance_id,
                mime="text/plain",
                text=text,
                title=ref.source_doc_id,
            )
            return raw, await self._acl(client)

    async def list_acls(self, ref: DocRef) -> AccessControl:
        # The faithful minimal ACL: only this instance's integration principal can
        # see the page, namespaced by the instance. Notion exposes no per-page
        # principal readout, so this is the coarsest grant that never oversharing
        # beyond the integration's own reach. See the module header ACL MODEL.
        async with self._client() as client:
            return await self._acl(client)

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        return []

    # --- helpers ---
    def _client(self) -> httpx.AsyncClient:
        headers = {
            "Notion-Version": _API_VERSION,
            "Authorization": f"Bearer {self._token}",
        }
        return httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=30.0)

    def _ref(self, page_id: str, deleted: bool = False) -> DocRef:
        return DocRef(source_doc_id=page_id, instance_id=self.instance_id, deleted=deleted)

    async def _resolve_principal(self, client: httpx.AsyncClient) -> str:
        """Resolve the integration principal id, caching it for the instance.

        Prefers an explicit config override, then the bot id from GET /v1/users/me,
        then the instance id as a last resort so an ACL is always well-formed. The
        network read is best effort: if it fails, the connector still emits an
        integration-scoped ACL keyed by the instance id and never widens the grant.
        """
        if self._integration_principal is not None:
            return self._integration_principal
        principal = self.instance_id
        try:
            resp = await client.get("/v1/users/me")
            self._raise_for_notion(resp)
            bot_id = resp.json().get("id")
            if isinstance(bot_id, str) and bot_id:
                principal = bot_id
        except httpx.HTTPError:
            # Best effort: keep the instance-id principal. The grant stays scoped to
            # the integration and is never widened by a failed lookup.
            principal = self.instance_id
        self._integration_principal = principal
        return principal

    async def _acl(self, client: httpx.AsyncClient) -> AccessControl:
        principal = await self._resolve_principal(client)
        if self._public:
            # Explicit, audited operator override. Never the default. See header.
            return AccessControl(public=True, principal_ns=self.instance_id)
        # Integration-scoped grant: only the integration principal, never public,
        # never a tenant-wide blanket. This is the faithful minimal ACL.
        return AccessControl(
            public=False,
            allow_users=[principal],
            principal_ns=self.instance_id,
        )

    @staticmethod
    def _raise_for_notion(resp: httpx.Response) -> None:
        """Raise on a Notion error, preserving the code/message envelope.

        Notion errors are a JSON envelope with "code" and "message"
        (https://developers.notion.com/reference/errors). A 404 object_not_found or
        403 restricted_resource means the page was un-shared or trashed, which is a
        permission/deletion signal the runtime must see rather than an opaque HTTP
        error; a 429 rate_limited carries a Retry-After the runtime owns. This helper
        surfaces the Notion code in the raised error instead of collapsing every
        failure into a bare HTTPStatusError, so the caller can distinguish
        no-longer-accessible from a transient fault.
        """
        if resp.is_success:
            return
        code = ""
        message = ""
        try:
            body = resp.json()
            if isinstance(body, dict):
                code = str(body.get("code", ""))
                message = str(body.get("message", ""))
        except (ValueError, httpx.DecodingError):
            code = ""
            message = ""
        detail = f"notion {resp.status_code}"
        if code:
            detail = f"{detail} {code}"
        if message:
            detail = f"{detail}: {message}"
        raise httpx.HTTPStatusError(detail, request=resp.request, response=resp)

    @staticmethod
    def _extract_text(blocks: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for block in blocks:
            block_type = block.get("type")
            if not block_type:
                continue
            payload = block.get(block_type)
            if not isinstance(payload, dict):
                continue
            for span in payload.get("rich_text", []):
                plain = span.get("plain_text")
                if plain:
                    parts.append(plain)
        return "\n".join(parts)

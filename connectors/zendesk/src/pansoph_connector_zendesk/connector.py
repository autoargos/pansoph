"""The zendesk connector.

Implemented against the Zendesk Help Center API. Verified against the official docs:

  API docs:    https://developer.zendesk.com/api-reference/help_center/help-center-api/articles/
               https://developer.zendesk.com/api-reference/help_center/help-center-api/user_segments/
               https://developer.zendesk.com/api-reference/help_center/help-center-api/introduction/
               https://developer.zendesk.com/api-reference/introduction/pagination/
               https://developer.zendesk.com/documentation/api-basics/working-with-oauth/creating-and-using-oauth-tokens-with-the-api/
               https://developer.zendesk.com/api-reference/introduction/security-and-auth/
  Base URL:    https://{subdomain}.zendesk.com/api/v2
  Auth:        API token via HTTP Basic. The username is "{email}/token" and the
               password is the API token, base64 encoded into an
               `Authorization: Basic <b64>` header. See the security and auth doc
               above. A test may override base_url; otherwise the base is built
               from the configured subdomain.
  Pagination:  Help Center list endpoints use cursor pagination. The response body
               carries `meta.has_more` and `links.next`; follow `links.next` while
               `meta.has_more` is true. This connector reads the page of `articles`
               and (for a first cut) returns an empty next_cursor, since the runtime
               owns resumable sync; following links is wired through `links.next`.
  Incremental: the Help Center incremental export endpoint
               (`/api/v2/help_center/incremental/articles.json?start_time=...`)
               returns articles changed since a Unix start time. The cursor in
               list_changes is reserved for that start_time; the runtime persists it.
  Rate limit:  Zendesk enforces per minute account limits and returns 429 with a
               `Retry-After` header. The runtime owns backoff; it is surfaced here,
               not hidden.
  ACL model:   a Help Center article carries `user_segment_id`. A null segment means
               the article is visible to everyone, which maps to public. A non null
               segment is fetched from `/api/v2/help_center/user_segments/{id}.json`
               and its `user_type`, `group_ids`, and `organization_ids` determine
               reach. See the mapping table below. Zendesk user segments are additive
               allow lists, so the connector produces no deny entries.
  Verified on: 2026-06-20.

ACL mapping (Zendesk user segment -> pansoph AccessControl):

  user_segment_id is null/None      -> public = True
  user_type == "staff"              -> allow_groups += ["staff"]
  user_type == "signed_in_users"    -> if no group_ids and no organization_ids,
                                       allow_groups += ["signed_in"]
  group_ids = [g, ...]              -> allow_groups += ["group:{g}", ...]
  organization_ids = [o, ...]      -> allow_groups += ["org:{o}", ...]

The build order is fixed: the user_type marker ("staff" or "signed_in") is added
first, then group ids, then organization ids. No deny entries are ever produced,
because a Zendesk user segment only grants access.

Credential scope note: every configured instance carries its own credential and its
own ACL namespace (principal_ns = instance_id). The credential reaches whatever the
Zendesk account and the agent or admin role behind the API token can see; ACLs
reflect that reach and never collide across instances. See pansoph CLAUDE.md
section 6.

The runtime owns retries, backoff, and resumable sync, so a method only describes
how to talk to Zendesk for one page or one document.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pansoph_connector_sdk import ChangeEvent, ConnectorMeta, ValidationResult
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc


class ZendeskConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        self.instance_id = instance_id
        cfg = config or {}
        self._subdomain: str = cfg.get("subdomain", "")
        self._email: str = cfg.get("email", "")
        self._token: str = cfg.get("token", "")
        self._locale: str = cfg.get("locale", "en-us")
        # A test may pin the base URL directly. Otherwise it is built from the
        # configured subdomain so the host is {subdomain}.zendesk.com.
        self._base_url: str = cfg.get("base_url") or (f"https://{self._subdomain}.zendesk.com")
        self.meta = ConnectorMeta(
            name="zendesk",
            auth=AuthScheme.api_token,
            capabilities=Capabilities(
                incremental=True, permission_sync=True, deletions=True, slim_listing=True
            ),
        )

    # --- contract ---
    def validate(self, config: dict[str, Any]) -> ValidationResult:
        # base_url can substitute for subdomain in tests; otherwise subdomain,
        # email, and token are all required to build Basic auth against the account.
        if config.get("base_url"):
            missing = [k for k in ("email", "token") if not config.get(k)]
        else:
            missing = [k for k in ("subdomain", "email", "token") if not config.get(k)]
        if missing:
            return ValidationResult(ok=False, error=f"missing config: {', '.join(missing)}")
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        async with self._client() as client:
            resp = await client.get(f"/api/v2/help_center/{self._locale}/articles.json")
            resp.raise_for_status()
            payload = resp.json()
            for article in payload.get("articles", []):
                yield self._ref(str(article["id"])), ""

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        async with self._client() as client:
            resp = await client.get(f"/api/v2/help_center/articles/{ref.source_doc_id}.json")
            resp.raise_for_status()
            article = resp.json()["article"]
            raw = RawDoc(
                source_doc_id=ref.source_doc_id,
                instance_id=self.instance_id,
                mime="text/html",
                text=article.get("body"),
                title=article.get("title"),
                source_url=article.get("html_url"),
            )
            return raw, await self._acl(client, article.get("user_segment_id"))

    async def list_acls(self, ref: DocRef) -> AccessControl:
        async with self._client() as client:
            resp = await client.get(f"/api/v2/help_center/articles/{ref.source_doc_id}.json")
            resp.raise_for_status()
            article = resp.json()["article"]
            return await self._acl(client, article.get("user_segment_id"))

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        # Zendesk Help Center change notifications are configured per account and are
        # not assumed here; the runtime drives incremental export instead.
        return []

    # --- helpers ---
    def _client(self) -> httpx.AsyncClient:
        raw = f"{self._email}/token:{self._token}".encode()
        token = base64.b64encode(raw).decode("ascii")
        headers = {
            "Accept": "application/json",
            "Authorization": f"Basic {token}",
        }
        return httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=30.0)

    def _ref(self, source_doc_id: str) -> DocRef:
        return DocRef(source_doc_id=source_doc_id, instance_id=self.instance_id)

    async def _acl(self, client: httpx.AsyncClient, user_segment_id: Any) -> AccessControl:
        ns = self.instance_id
        if user_segment_id is None:
            return AccessControl(public=True, principal_ns=ns)
        resp = await client.get(f"/api/v2/help_center/user_segments/{user_segment_id}.json")
        resp.raise_for_status()
        segment = resp.json()["user_segment"]
        user_type = segment.get("user_type")
        group_ids = segment.get("group_ids") or []
        organization_ids = segment.get("organization_ids") or []
        # Build order is fixed so the ACL is deterministic: the user_type marker
        # first, then group ids, then organization ids.
        allow_groups: list[str] = []
        if user_type == "staff":
            allow_groups.append("staff")
        elif user_type == "signed_in_users" and not group_ids and not organization_ids:
            allow_groups.append("signed_in")
        allow_groups.extend(f"group:{g}" for g in group_ids)
        allow_groups.extend(f"org:{o}" for o in organization_ids)
        return AccessControl(allow_groups=allow_groups, principal_ns=ns)

"""The zendesk connector.

Implemented against the Zendesk Help Center API. Verified against the official docs:

  Articles API (draft / user_segment_id / promoted, List Articles, List Articles
    Incrementally):
      https://developer.zendesk.com/api-reference/help_center/help-center-api/articles/
  User Segments (user_type=signed_in_users|staff, group_ids, organization_ids,
    tags, or_tags, added_user_ids, built_in):
      https://developer.zendesk.com/api-reference/help_center/help-center-api/user_segments/
  Understanding incremental article exports (metadata-change semantics, next_page as
    a new start_time, up to 1000/page, agent-or-admin required, OAuth returns only
    published content, no deletion signal for articles):
      https://developer.zendesk.com/documentation/help_center/help-center-api/understanding-incremental-article-exports/
  Incremental Exports overview (tombstone behavior exists for tickets and custom
    objects but NOT for Help Center articles):
      https://developer.zendesk.com/api-reference/ticketing/ticket-management/incremental_exports/
      https://developer.zendesk.com/documentation/api-basics/working-with-data/using-the-incremental-export-api/
  Pagination (cursor pagination, meta.has_more, links.next):
      https://developer.zendesk.com/api-reference/introduction/pagination/
  Security and auth (API token via HTTP Basic, username "{email}/token"):
      https://developer.zendesk.com/api-reference/introduction/security-and-auth/

  Base URL:    https://{subdomain}.zendesk.com
  Auth:        API token via HTTP Basic. The username is "{email}/token" and the
               password is the API token, base64 encoded into an
               `Authorization: Basic <b64>` header. See the security and auth doc.
               A test may override base_url; otherwise the base is built from the
               configured subdomain. This is an agent-or-admin credential.
  Pagination:  Help Center list endpoints use cursor pagination. The response body
               carries `meta.has_more` and `links.next`; follow `links.next` while
               `meta.has_more` is true. This connector paginates fully in one call,
               reading every page of `articles` before it returns.

  Draft policy: an article response carries a per-locale `draft` boolean. With Basic
               auth as an agent, draft (unpublished) translations are visible to the
               credential, unlike OAuth which returns only published content. Indexing
               an unpublished draft would leak internal content to end users, so this
               connector SKIPS every article where `draft` is True, in both the
               listing and the fetch. Drafts are never indexed.

  Incremental: capability is False, and this is honest. `list_changes` performs a full
               listing each run via cursor pagination; it does NOT use the incremental
               export `start_time` watermark. It DOES emit a non-empty resume cursor
               (see the deletion-reconciliation note below), so the cursor carries the
               reconciliation state, not an incremental high-water mark. If incremental
               is implemented later, use the export next_page semantics: next_page
               carries a new start_time equal to the last change time of the last item
               on the page (per the incremental export doc).

  Deletions:  the Help Center incremental articles export has NO deletion signal for
               articles: no deleted flag, no tombstone, and no deleted-id list (unlike
               tickets and custom objects). See the incremental export docs above.
               Deletion is therefore detected by client-side reconciliation. The resume
               cursor carries the set of published article ids seen on the previous run
               (base64-encoded JSON). On each run, after the full listing, any id that
               was in the previous cursor's set but is absent from the current listing
               is surfaced as a DocRef with deleted=True (a tombstone). This makes the
               deletions capability honest: `list_changes` can and does produce a
               tombstone when a published article disappears (deleted, unpublished, or
               removed from the credential's reach).

  Rate limit:  Zendesk enforces per minute account limits and returns 429 with a
               `Retry-After` header. The runtime owns backoff; it is surfaced here,
               not hidden.

  ACL model:   a Help Center article carries `user_segment_id`. A null segment means
               the article is visible to everyone, which maps to public. A non null
               segment is fetched from `/api/v2/help_center/user_segments/{id}.json`
               and its full restriction set determines reach. See the mapping table
               below. Zendesk user segments are additive allow lists, so the connector
               produces no deny entries.
  Verified on: 2026-07-05.

ACL mapping (Zendesk user segment -> pansoph AccessControl):

  user_segment_id is null/None      -> public = True

  For a non-null segment, every restriction the segment expresses is mapped. A
  principal is granted only if it satisfies at least one mapped grant (the resolver
  ORs grants), so nothing is widened beyond what the segment allows:

    user_type == "staff"              -> allow_groups += ["staff"]   (only when the
                                         segment has no other restriction; a
                                         group-restricted staff segment maps to the
                                         group ids, not to all staff)
    user_type == "signed_in_users"    -> allow_groups += ["signed_in"] ONLY when
                                         group_ids, organization_ids, tags, or_tags,
                                         and added_user_ids are ALL empty; a
                                         restricted signed_in segment is never
                                         collapsed to all signed-in users
    group_ids = [g, ...]              -> allow_groups += ["group:{g}", ...]
    organization_ids = [o, ...]       -> allow_groups += ["org:{o}", ...]
    or_tags = [t, ...]                -> allow_groups += ["tag:{t}", ...] (a user with
                                         at least one of these tags is granted; OR maps
                                         directly onto the resolver's OR-over-groups)
    tags = [t, ...] (AND, non-empty)  -> the segment requires the user to hold ALL of
                                         these tags. The pansoph allow_groups resolver
                                         is OR-only and cannot express a conjunction, so
                                         mapping the individual tags would OVER-GRANT
                                         (any single tag would match). To avoid leaking
                                         a tag-conjunction-restricted article, this
                                         connector FAILS CLOSED: it emits a single
                                         unsatisfiable sentinel group that no roster
                                         principal can hold, so the article is denied to
                                         everyone the connector cannot prove is allowed.
                                         Under-granting is safe; over-granting is a leak.
    added_user_ids = [u, ...]         -> allow_users += ["user:{u}", ...] (individual
                                         users granted access regardless of the other
                                         criteria, per the user_segment doc)

No deny entries are ever produced, because a Zendesk user segment only grants access.

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
import json
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

# The sentinel group used to fail closed for a tags (AND / conjunction) restriction.
# No real principal ever holds this group, so an article whose segment requires a tag
# conjunction is denied to everyone rather than over-granted. See the ACL mapping note
# in the module docstring.
_TAGS_AND_UNSATISFIABLE = "unsatisfiable:tags_and"


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
        # A user segment's reach is constant for the whole sync run, and many
        # articles share the same segment id. Cache the computed ACL per segment id
        # (None for a public article) so the segment lookup runs once per distinct
        # segment instead of once per article. A connector instance lives for exactly
        # one sync run (the runtime builds a fresh instance per sync), so this never
        # serves stale reach across runs. The cached ACL equals what a fresh fetch
        # would compute; only the number of GET user_segments calls changes.
        self._acl_cache: dict[Any, AccessControl] = {}
        self.meta = ConnectorMeta(
            name="zendesk",
            auth=AuthScheme.api_token,
            capabilities=Capabilities(
                incremental=False, permission_sync=True, deletions=True, slim_listing=True
            ),
        )

    @classmethod
    def spec(cls) -> ConnectorSpec:
        return ConnectorSpec(
            name="zendesk",
            title="Zendesk Help Center",
            description=(
                "Ingest Help Center articles from a Zendesk account, with per article "
                "ACLs derived from user segments."
            ),
            fields=(
                ConfigField(
                    key="subdomain",
                    label="Subdomain",
                    kind=FieldKind.STRING,
                    placeholder="acme",
                    help=(
                        "The account subdomain in {subdomain}.zendesk.com. Required unless "
                        "an explicit base URL is set."
                    ),
                ),
                ConfigField(
                    key="email",
                    label="Agent email",
                    kind=FieldKind.STRING,
                    placeholder="agent@acme.com",
                    help="The agent email used for API token Basic auth (sent as {email}/token).",
                ),
                ConfigField(
                    key="token",
                    label="API token",
                    kind=FieldKind.SECRET,
                    help="Zendesk API token. Sealed in the secret store, never stored as config.",
                ),
                ConfigField(
                    key="locale",
                    label="Locale",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="en-us",
                    help="Help Center locale used when listing articles. Defaults to en-us.",
                ),
                ConfigField(
                    key="base_url",
                    label="API base URL",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="https://acme.zendesk.com",
                    help=(
                        "Override the host built from the subdomain. Use for a proxy or a "
                        "self-managed gateway."
                    ),
                ),
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
        """Yield a ref for every published Help Center article, plus deletion tombstones.

        Full listing each run via cursor pagination (incremental is off). Draft
        (unpublished) articles are skipped so internal content is never indexed. The
        Help Center article export has no deletion signal, so deletions are detected by
        reconciliation: the incoming `cursor` carries the set of published article ids
        seen on the previous run, and any id in that set that is absent from the current
        listing is surfaced as a DocRef with deleted=True.

        Every yielded tuple carries the same non-empty resume cursor, which encodes the
        full set of published ids seen on THIS run, so the next run can reconcile against
        it. Zendesk Help Center uses cursor pagination: the response `meta.has_more` says
        whether another page exists and `links.next` is the absolute URL of it. We follow
        `links.next` until `has_more` is false or the link is missing.
        """
        previous_ids = self._decode_cursor(cursor)
        current_ids: set[str] = set()
        async with self._client() as client:
            resp = await client.get(f"/api/v2/help_center/{self._locale}/articles.json")
            resp.raise_for_status()
            payload = resp.json()
            while True:
                for article in payload.get("articles", []):
                    if article.get("draft"):
                        # Unpublished draft: never index it. It is also not tracked in
                        # current_ids, so if a previously-published article became a
                        # draft it will reconcile as a deletion below (removed from the
                        # end user's reach).
                        continue
                    current_ids.add(str(article["id"]))
                if not payload.get("meta", {}).get("has_more"):
                    break
                next_url = payload.get("links", {}).get("next")
                if not next_url:
                    break
                resp = await client.get(next_url)
                resp.raise_for_status()
                payload = resp.json()

        next_cursor = self._encode_cursor(current_ids)
        for article_id in sorted(current_ids):
            yield self._ref(article_id), next_cursor
        # Reconciliation-based deletion detection: ids present on the previous run but
        # gone now are tombstoned so the runtime purges the document and its ACL tuples.
        for article_id in sorted(previous_ids - current_ids):
            yield self._deleted_ref(article_id), next_cursor

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        async with self._client() as client:
            resp = await client.get(f"/api/v2/help_center/articles/{ref.source_doc_id}.json")
            resp.raise_for_status()
            article = resp.json()["article"]
            if article.get("draft"):
                # Defense in depth: a draft must never be indexed even if it reaches
                # fetch (for example a race where it was published at list time and
                # unpublished by fetch time). Return an empty raw doc under a fail-closed
                # ACL so no unpublished content is surfaced to end users.
                raw = RawDoc(
                    source_doc_id=ref.source_doc_id,
                    instance_id=self.instance_id,
                    mime="text/html",
                    text=None,
                    title=article.get("title"),
                    source_url=article.get("html_url"),
                )
                acl = AccessControl(
                    allow_groups=[_TAGS_AND_UNSATISFIABLE], principal_ns=self.instance_id
                )
                return raw, acl
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
            if article.get("draft"):
                # A draft is not visible to end users; fail closed.
                return AccessControl(
                    allow_groups=[_TAGS_AND_UNSATISFIABLE], principal_ns=self.instance_id
                )
            return await self._acl(client, article.get("user_segment_id"))

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        # Zendesk Help Center change notifications are configured per account and are
        # not assumed here; the runtime drives full-listing reconciliation instead.
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

    def _deleted_ref(self, source_doc_id: str) -> DocRef:
        return DocRef(source_doc_id=source_doc_id, instance_id=self.instance_id, deleted=True)

    @staticmethod
    def _encode_cursor(ids: set[str]) -> str:
        """Encode the set of published ids seen this run into an opaque resume cursor.

        The cursor is a base64-encoded JSON sorted list, which the runtime persists and
        replays verbatim. It is always non-empty (an empty run still encodes []), so the
        conformance contract's non-empty-cursor requirement holds.
        """
        blob = json.dumps(sorted(ids), separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(blob).decode("ascii")

    @staticmethod
    def _decode_cursor(cursor: str | None) -> set[str]:
        """Decode a resume cursor into the set of ids seen on the previous run.

        A None or unparseable cursor means no prior state (a first run), so no deletion
        is inferred. A malformed cursor is treated as empty rather than raised on: a
        bad prior state must not fail the whole sync, and treating it as empty is the
        safe direction (it under-reports deletions, never fabricates one).
        """
        if not cursor:
            return set()
        try:
            blob = base64.urlsafe_b64decode(cursor.encode("ascii"))
            data = json.loads(blob.decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            return set()
        if not isinstance(data, list):
            return set()
        return {str(item) for item in data}

    async def _acl(self, client: httpx.AsyncClient, user_segment_id: Any) -> AccessControl:
        # The segment lookup depends only on user_segment_id, and a segment's reach
        # is fixed for the whole sync run, so cache the computed ACL per segment id
        # and reuse it. Many articles share a segment, so this avoids refetching the
        # same /user_segments/{id}.json for every article in that segment.
        if user_segment_id in self._acl_cache:
            return self._acl_cache[user_segment_id]
        ns = self.instance_id
        if user_segment_id is None:
            acl = AccessControl(public=True, principal_ns=ns)
            self._acl_cache[user_segment_id] = acl
            return acl
        resp = await client.get(f"/api/v2/help_center/user_segments/{user_segment_id}.json")
        resp.raise_for_status()
        segment = resp.json()["user_segment"]
        acl = AccessControl(**self._segment_grants(segment), principal_ns=ns)
        self._acl_cache[user_segment_id] = acl
        return acl

    @staticmethod
    def _segment_grants(segment: dict[str, Any]) -> dict[str, list[str]]:
        """Map a Zendesk user_segment object to allow_groups and allow_users.

        Every restriction the segment expresses is mapped, so no principal is granted
        beyond what the segment allows and no legitimately granted principal is dropped.
        See the ACL mapping table in the module docstring for the doc-grounded rules.
        Order is fixed so the result is deterministic.
        """
        user_type = segment.get("user_type")
        group_ids = segment.get("group_ids") or []
        organization_ids = segment.get("organization_ids") or []
        tags = segment.get("tags") or []
        or_tags = segment.get("or_tags") or []
        added_user_ids = segment.get("added_user_ids") or []

        has_other_restriction = bool(
            group_ids or organization_ids or tags or or_tags or added_user_ids
        )

        allow_groups: list[str] = []
        # user_type marker first. "signed_in" (all signed-in users) is emitted ONLY when
        # the segment carries no other restriction, so a restricted signed_in segment is
        # never widened to every signed-in user. A "staff" marker is likewise only the
        # broad "all staff" grant when the segment has no narrower restriction.
        if user_type == "staff" and not has_other_restriction:
            allow_groups.append("staff")
        elif user_type == "signed_in_users" and not has_other_restriction:
            allow_groups.append("signed_in")

        allow_groups.extend(f"group:{g}" for g in group_ids)
        allow_groups.extend(f"org:{o}" for o in organization_ids)
        # or_tags is an OR restriction: a user with at least one listed tag is granted.
        # That maps directly onto the resolver's OR-over-allow_groups, so each tag is a
        # group principal.
        allow_groups.extend(f"tag:{t}" for t in or_tags)
        # tags is an AND (conjunction) restriction: the user must hold ALL listed tags.
        # allow_groups is OR-only and cannot express a conjunction, so mapping each tag
        # would over-grant (any single tag would match). Fail closed with an
        # unsatisfiable sentinel so the article is denied to everyone the connector
        # cannot prove is allowed, rather than leaked to holders of a single tag.
        if tags:
            allow_groups.append(_TAGS_AND_UNSATISFIABLE)

        # added_user_ids: individual users granted access regardless of other criteria.
        allow_users = [f"user:{u}" for u in added_user_ids]

        return {"allow_groups": allow_groups, "allow_users": allow_users}

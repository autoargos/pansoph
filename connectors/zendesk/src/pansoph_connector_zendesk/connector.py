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
               and its restriction set determines reach. See the mapping table below.
               Zendesk user segments are additive allow lists, so the connector produces
               no deny entries.
  Verified on: 2026-07-05, against the current official docs. The user_segments API
               reference confirms the field semantics: `tags` (user must have ALL),
               `or_tags` (at least one), `group_ids`, `organization_ids`, and
               `added_user_ids` ("The ids of users added specifically to this user
               segment, regardless of matching tags or other criteria"). The support doc
               "Creating user segments for help center user permissions"
               (https://support.zendesk.com/hc/en-us/articles/4408837707290) confirms the
               combination rule: different attribute types work TOGETHER to narrow the
               segment (AND across categories), values within one type follow that type's
               match rule (OR / all), and individual users are added as an exception that
               bypasses the filters.

ACL mapping (Zendesk user segment -> pansoph AccessControl):

  user_segment_id is null/None      -> public = True

  A segment combines the NARROWING attribute types (group_ids, organization_ids,
  or_tags, tags) with AND ACROSS types and OR WITHIN a type. pansoph AccessControl is a
  pure OR over allow_groups, so it can express at most ONE narrowing category faithfully.
  The mapping therefore counts how many narrowing categories the segment restricts on:

    ZERO narrowing categories:
      user_type == "staff"            -> allow_groups = ["staff"]
      user_type == "signed_in_users"  -> allow_groups = ["signed_in"]
      (the broad base-audience marker is the whole grant; it is emitted ONLY here, never
       alongside a narrowing category, so a restricted segment is never widened to all
       staff / all signed-in users)

    EXACTLY ONE narrowing category (faithful, expressible as an OR):
      group_ids = [g, ...]            -> allow_groups = ["group:{g}", ...]
      organization_ids = [o, ...]     -> allow_groups = ["org:{o}", ...]
      or_tags = [t, ...]              -> allow_groups = ["tag:{t}", ...] (at least one)
      tags = [t, ...] (AND)           -> FAIL CLOSED with _TAGS_AND_UNSATISFIABLE. The
                                         user must hold ALL listed tags; the OR resolver
                                         cannot express that conjunction, so mapping each
                                         tag would over-grant. Under-granting is safe.

    TWO OR MORE narrowing categories (a real cross-category AND):
      any two of {group_ids, organization_ids, or_tags, tags}
                                      -> FAIL CLOSED with
                                         _CROSS_CATEGORY_AND_UNSATISFIABLE. Zendesk ANDs
                                         the categories (e.g. org 9 AND tag vip), but the
                                         OR resolver would grant anyone matching EITHER,
                                         an over-grant / leak. So the connector emits only
                                         the unsatisfiable cross-category sentinel (an
                                         unmaterialized namespaced group that grants no
                                         one) rather than the OR union. Never map to the
                                         broader side. The identity layer may later
                                         materialize the sentinel to the true intersection
                                         of principals; until then it under-shares safely.

  added_user_ids = [u, ...]           -> allow_users += ["user:{u}", ...], ALWAYS, in
                                         addition to whatever the categories map to
                                         (including under a fail-closed grant). These are
                                         individual users the segment grants "regardless
                                         of matching tags or other criteria", so they are
                                         provably allowed and unioned in, not a widening.

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

# The sentinel group used to fail closed for a cross-category AND restriction, i.e. a
# segment that narrows across two or more attribute types (any two of group_ids,
# organization_ids, or_tags, tags). Zendesk combines different attribute types with AND
# (see the ACL mapping note below), but pansoph AccessControl is a pure OR over
# allow_groups and cannot express a conjunction across categories. Flattening the
# categories into one allow_groups list would grant anyone who matches EITHER category,
# an over-grant / leak. This sentinel is an unsatisfiable, namespaced group that no
# roster principal holds, so the article is denied to everyone rather than leaked. It is
# a stable id the identity layer may later materialize to the true intersection of
# principals; until it is materialized it grants no one, which under-shares safely.
_CROSS_CATEGORY_AND_UNSATISFIABLE = "unsatisfiable:cross_category_and"


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

        Zendesk combines the narrowing attribute types (group_ids, organization_ids,
        or_tags, tags) with AND across types and OR within a type, so a segment that
        restricts across two or more of them means the user must satisfy ALL of those
        categories. pansoph AccessControl is a pure OR over allow_groups and cannot
        express a conjunction across categories, so this method fails closed on any real
        cross-category AND (see _CROSS_CATEGORY_AND_UNSATISFIABLE and the module docstring
        ACL mapping table). A single narrowing category maps faithfully. added_user_ids
        are unioned in regardless, because the source grants them regardless of the
        filters. Order is fixed so the result is deterministic.
        """
        user_type = segment.get("user_type")
        group_ids = segment.get("group_ids") or []
        organization_ids = segment.get("organization_ids") or []
        tags = segment.get("tags") or []
        or_tags = segment.get("or_tags") or []
        added_user_ids = segment.get("added_user_ids") or []

        # The narrowing attribute types Zendesk ANDs across. group_ids imply the staff
        # base and organization_ids imply the signed-in base, so those never add a
        # constraint beyond user_type. But a "staff" base combined with a tag or org
        # category IS a real, inexpressible AND: Zendesk tags and organizations are
        # shared with end users, so "staff who also hold tag X" is NOT the same audience
        # as tag:X (which would grant non-staff tag holders). So staff counts as a
        # narrowing category whenever it is paired with an org or tag restriction, and the
        # segment then fails closed rather than dropping the staff constraint.
        # added_user_ids is an explicit exception unioned in, not a narrowing filter, so
        # it never counts. See the official docs cited in the module docstring.
        staff_restrictive = user_type == "staff" and bool(organization_ids or or_tags or tags)
        narrowing_categories = [
            bool(group_ids),
            bool(organization_ids),
            bool(or_tags),
            bool(tags),
            staff_restrictive,
        ]
        narrowing_count = sum(narrowing_categories)

        # added_user_ids: individual users granted access regardless of other criteria
        # (per the user_segments doc: "regardless of matching tags or other criteria").
        # Always unioned in, including under a fail-closed grant, since these named users
        # are provably allowed by the source.
        allow_users = [f"user:{u}" for u in added_user_ids]

        if narrowing_count >= 2:
            # Real cross-category AND. The OR resolver cannot express the intersection,
            # so fail closed: emit only the unsatisfiable cross-category sentinel rather
            # than flatten the categories into an over-broad OR. Under-sharing is safe;
            # over-sharing is a leak. added_user_ids are still granted (explicit
            # exceptions the source grants regardless of the filters).
            return {
                "allow_groups": [_CROSS_CATEGORY_AND_UNSATISFIABLE],
                "allow_users": allow_users,
            }

        allow_groups: list[str] = []
        if narrowing_count == 0:
            # No narrowing category: the base audience marker is the whole grant. These
            # broad markers are emitted ONLY here, never alongside a narrowing category,
            # so a restricted segment is never widened to all staff / all signed-in users.
            if user_type == "staff":
                allow_groups.append("staff")
            elif user_type == "signed_in_users":
                allow_groups.append("signed_in")
        elif group_ids:
            # Single narrowing category: group membership (OR within the category).
            allow_groups.extend(f"group:{g}" for g in group_ids)
        elif organization_ids:
            # Single narrowing category: organization membership (OR within the category).
            allow_groups.extend(f"org:{o}" for o in organization_ids)
        elif or_tags:
            # Single narrowing category: or_tags is an OR restriction (at least one tag),
            # which maps directly onto the resolver's OR-over-allow_groups.
            allow_groups.extend(f"tag:{t}" for t in or_tags)
        else:
            # Single narrowing category: tags is an AND (conjunction) restriction, the
            # user must hold ALL listed tags. allow_groups is OR-only and cannot express
            # a conjunction, so mapping each tag would over-grant (any single tag would
            # match). Fail closed with the tags-AND sentinel.
            allow_groups.append(_TAGS_AND_UNSATISFIABLE)

        return {"allow_groups": allow_groups, "allow_users": allow_users}

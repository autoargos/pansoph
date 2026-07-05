"""The salesforce connector.

Implemented against the Salesforce REST API. Verified against the current official
docs (docs verified 2026-07-05, targeting REST API v67.0, Summer '26):

  API docs (base https://developer.salesforce.com):
    intro:   /docs/atlas.en-us.api_rest.meta/api_rest/intro_what_is_rest_api.htm
    versions:/docs/atlas.en-us.api_rest.meta/api_rest/dome_versions.htm (v67.0 is current)
    query:   /docs/atlas.en-us.api_rest.meta/api_rest/dome_query.htm (SOQL)
    more:    /docs/atlas.en-us.api_rest.meta/api_rest/dome_querymore.htm (queryMore)
    get:     /docs/atlas.en-us.api_rest.meta/api_rest/dome_sobject_retrieve.htm
    deleted: /docs/atlas.en-us.api_rest.meta/api_rest/dome_get_deleted.htm (sObject Get Deleted)
    share: /docs/atlas.en-us.object_reference.meta/object_reference/sforce_api_objects_caseshare.htm
    accshare:/docs/atlas.en-us.object_reference.meta/object_reference/sforce_api_objects_accountshare.htm
             ({Object}AccessLevel is a restricted picklist: None, Read, Edit, All; a
             row also carries sibling columns like CaseAccessLevel, so one row can hold
             AccountAccessLevel="None" while a child level is a read grant)
    owd:     /docs/atlas.en-us.securityImplGuide.meta/securityImplGuide/security_data_sharing.htm
  API version: v67.0 (Summer '26).
  Auth:        OAuth 2.0 bearer access token, supplied by the operator in config
               as "token" and sent as the header "Authorization: Bearer <token>".
               Token acquisition (JWT bearer flow, web server flow, or any other
               OAuth flow) is the operator's responsibility; this connector does
               not mint or refresh tokens, it consumes one.
  Pagination:  SOQL returns a page of records plus "done" and, when more pages
               remain, a relative "nextRecordsUrl". Follow nextRecordsUrl until
               "done" is true (the queryMore mechanism).
  Incremental: filter on SystemModstamp, the system audit field that updates on
               every change. list_changes selects SystemModstamp, orders by it
               ascending, and yields each record's SystemModstamp as the cursor.
               The cursor is the durable high water mark; a later poll issues
               "WHERE SystemModstamp > {cursor}". SystemModstamp is second
               precision, so a record sharing the boundary second with the cursor
               can be re-yielded (re-ingest is idempotent) rather than dropped.
  Deletions:   not modeled. Salesforce exposes sObject Get Deleted, but it reads a
               delete log that Salesforce purges after a short window, so a
               poll-based tombstone stream is not reliably complete. deletions is
               declared False rather than half-implemented. See the caveat.
  Rate limit:  the org's daily API request allocation applies; surfaced, not hidden.
  Objects:     only objects whose read access is governed by the row-level
               {Object}Share table are supported, so the share-derived ACL is
               faithful. That is the allowlist in _OBJECTS: Case, Account,
               Opportunity, Contact. KnowledgeArticleVersion is deliberately not
               offered because its visibility is governed by data categories and
               channels, not by share rows, so a share-derived ACL would be wrong.
  ACL model:   best effort, fail closed. Salesforce record access is governed by
               org wide defaults, the role hierarchy, sharing rules, and manual
               shares. The share table for a standard object ({Object}Share, e.g.
               CaseShare) exposes explicit shares as rows. The access-level field
               on a standard-object share table is named "{Object}AccessLevel"
               (CaseShare.CaseAccessLevel, AccountShare.AccountAccessLevel, and so
               on); only custom-object share tables use a field literally named
               "AccessLevel". This connector queries the correctly named field per
               object and gates on its value. The field is a restricted picklist with
               documented values None, Read, Edit, All. Only Read, Edit, and All grant
               read; "None" is a documented valid value that grants NO read (Salesforce
               uses it to carry related/implicit access on a sibling access-level column
               of the same row, e.g. an AccountShare row with AccountAccessLevel="None"
               but CaseAccessLevel="Read"). So a UserOrGroupId is mapped to an allow
               principal ONLY when its {Object}AccessLevel is a read grant; a "None",
               empty, or unrecognized level is skipped (fail closed, under-shares). For
               a mapped principal, a "005" key prefix is a User, anything else (Group,
               Role, RoleAndSubordinates, PublicGroup) is a group. Sharing in Salesforce
               is additive, so there is no deny. See the caveat below.
  Verified on: 2026-07-05.

ACL caveat (read before trusting this): the {Object}Share table lists only explicit
shares. It does not encode the organization wide default, nor access inherited
through the role hierarchy (that access lives in Group Maintenance tables, not share
rows), nor the implicit access an owner or an owner's manager has. So the computed
ACL is a faithful, deliberately conservative (fail closed, never widened) subset of
the true effective access: it can under-report access, never over-report it.

One specific gap to know: since the "Enable Faster Account Sharing Recalculation by
Not Storing Case and Contact Implicit Child Shares" release update (GA Summer '23,
enforced in existing orgs Winter '24, extended to Opportunity), Salesforce no longer
stores ImplicitChild share rows for Account, Case, Contact, and Opportunity. Queries
on CaseShare / ContactShare / OpportunityShare with RowCause = ImplicitChild return
zero rows. Parent-derived access (a user who can see an Account therefore seeing its
child Cases) is therefore invisible to a share-table read. The faithful sources for
that effective access are UserRecordAccess (per-record HasReadAccess /
MaxAccessLevel) or the parent AccountShare.{Object}AccessLevel column; this connector
reads neither, so parent-derived access is under-reported (fail closed), not leaked.
Salesforce Knowledge is an additional special case and is not offered as an object at
all (its visibility is data-category and channel governed, not row-share governed).

Credential scope note: every configured instance carries its own OAuth credential
and its own ACL namespace (principal_ns = instance_id). The credential's profile and
permission set decide what records and share rows it can read, so the mapped ACL
reflects the credential's actual reach. Two instances never collide. See pansoph
CLAUDE.md section 6.

The runtime owns retries, backoff, and resumable sync, so a method only describes
how to talk to Salesforce for one page or one document.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
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

_API_PATH = "/services/data/v67.0"
# Salesforce User records carry the "005" key prefix. Everything else returned by a
# share table's UserOrGroupId (Group, Role, RoleAndSubordinates, PublicGroup) is
# treated as a group principal.
_USER_KEY_PREFIX = "005"
# Salesforce record ids are 15 (case sensitive) or 18 (case insensitive)
# alphanumeric characters. Validated before interpolation into SOQL.
_SF_ID = re.compile(r"[a-zA-Z0-9]{15}(?:[a-zA-Z0-9]{3})?")
# The {Object}AccessLevel field on a standard-object share table is a restricted
# picklist whose documented values are None, Read, Edit, All (AccountShare object
# reference, docs verified 2026-07-05, see module header). Only these three values
# grant read to the record; "None" is a documented valid value that grants NO read
# (Salesforce uses it internally to carry related/implicit access on a sibling column
# of the same row). We map a share row to an allow principal ONLY when its access
# level is one of these read grants, and fail closed on anything else (None, an empty
# value, or an unrecognized future value) by skipping the row. Skipping under-shares,
# which is the safe direction; mapping a non-read row would over-share (a leak).
_READ_GRANT_LEVELS = frozenset({"Read", "Edit", "All"})


@dataclass(frozen=True)
class _ObjectShape:
    """How one supported sObject exposes its content and its row-level shares.

    title_field / body_field are the standard fields ingested as the document title
    and body. share_object and access_level_field name the object's share table and
    its correctly named access-level column (a standard object uses
    "{Object}AccessLevel", never a bare "AccessLevel").
    """

    title_field: str
    body_field: str
    share_object: str
    access_level_field: str


# Only objects whose read access is governed by the row-level {Object}Share table,
# so the share-derived ACL is faithful. Content fields are the object's standard
# title/body fields, verified against the Object Reference (v67.0). Knowledge is
# intentionally excluded: its visibility is data-category governed, not row-share
# governed, so a share-derived ACL would be wrong.
_OBJECTS: dict[str, _ObjectShape] = {
    "Case": _ObjectShape("Subject", "Description", "CaseShare", "CaseAccessLevel"),
    "Account": _ObjectShape("Name", "Description", "AccountShare", "AccountAccessLevel"),
    "Opportunity": _ObjectShape(
        "Name", "Description", "OpportunityShare", "OpportunityAccessLevel"
    ),
    "Contact": _ObjectShape("Name", "Description", "ContactShare", "ContactAccessLevel"),
}


def _soql_datetime(raw: str) -> str:
    """Normalize a SystemModstamp value into a SOQL dateTime literal.

    Salesforce returns SystemModstamp as e.g. "2026-07-05T12:00:00.000+0000", but a
    SOQL WHERE clause wants the unquoted Zulu form "2026-07-05T12:00:00Z". Parsing
    then reformatting also lets an already-normalized cursor round-trip unchanged.
    """
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class SalesforceConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        self.instance_id = instance_id
        cfg = config or {}
        self._token: str = cfg.get("token", "")
        self._instance_url: str = cfg.get("instance_url", "")
        # The sObject to ingest. Must be one of the supported objects in _OBJECTS;
        # anything else is rejected by validate() and by _shape().
        self._object: str = cfg.get("object", "Case")
        self.meta = ConnectorMeta(
            name="salesforce",
            auth=AuthScheme.oauth2,
            capabilities=Capabilities(
                incremental=True, permission_sync=True, deletions=False, slim_listing=True
            ),
        )

    @classmethod
    def spec(cls) -> ConnectorSpec:
        supported = ", ".join(_OBJECTS)
        return ConnectorSpec(
            name="salesforce",
            title="Salesforce",
            description="Ingest records of one Salesforce sObject over the REST API.",
            fields=(
                ConfigField(
                    key="instance_url",
                    label="Instance URL",
                    kind=FieldKind.STRING,
                    placeholder="https://acme.my.salesforce.com",
                    help="Your org's My Domain base URL. REST calls are made against it.",
                ),
                ConfigField(
                    key="token",
                    label="OAuth access token",
                    kind=FieldKind.SECRET,
                    help=(
                        "OAuth 2.0 bearer access token. This connector consumes a token you "
                        "supply, it does not mint or refresh one. Sealed in the secret store."
                    ),
                ),
                ConfigField(
                    key="object",
                    label="sObject",
                    kind=FieldKind.STRING,
                    required=False,
                    placeholder="Case",
                    help=(
                        f"The sObject to ingest. One of: {supported}. Defaults to Case. Only "
                        "objects whose access is governed by the {Object}Share table are "
                        "supported, so the derived ACL is faithful. Knowledge articles are not "
                        "supported because their visibility is governed by data categories, "
                        "not share rows, which this connector does not model."
                    ),
                ),
            ),
        )

    # --- contract ---
    def validate(self, config: dict[str, Any]) -> ValidationResult:
        missing = [k for k in ("instance_url", "token") if not config.get(k)]
        if missing:
            return ValidationResult(ok=False, error=f"missing config: {', '.join(missing)}")
        obj = config.get("object", "Case")
        if obj not in _OBJECTS:
            supported = ", ".join(_OBJECTS)
            return ValidationResult(
                ok=False, error=f"unsupported object {obj!r}; supported: {supported}"
            )
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        shape = self._shape()
        select = f"Id, SystemModstamp, {shape.title_field}, {shape.body_field}"
        soql = f"SELECT {select} FROM {self._object}"
        if cursor:
            soql += f" WHERE SystemModstamp > {_soql_datetime(cursor)}"
        soql += " ORDER BY SystemModstamp ASC"
        async with self._client() as client:
            url: str | None = f"{_API_PATH}/query/"
            params: dict[str, Any] | None = {"q": soql}
            while url:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                payload = resp.json()
                for rec in payload.get("records", []):
                    modstamp = _soql_datetime(rec["SystemModstamp"])
                    yield self._ref(rec["Id"], modstamp), modstamp
                if payload.get("done", True):
                    break
                # nextRecordsUrl is a relative URL already carrying the locator.
                url, params = payload.get("nextRecordsUrl"), None

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        shape = self._shape()
        record_id = self._checked_id(ref.source_doc_id)
        async with self._client() as client:
            resp = await client.get(f"{_API_PATH}/sobjects/{self._object}/{record_id}")
            resp.raise_for_status()
            payload = resp.json()
            title = payload.get(shape.title_field) or ""
            body = payload.get(shape.body_field) or ""
            text = f"{title}\n{body}".strip()
            raw = RawDoc(
                source_doc_id=record_id,
                instance_id=self.instance_id,
                mime="text/plain",
                text=text,
                title=title,
            )
            return raw, await self._acl(client, record_id)

    async def list_acls(self, ref: DocRef) -> AccessControl:
        record_id = self._checked_id(ref.source_doc_id)
        async with self._client() as client:
            return await self._acl(client, record_id)

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        # Salesforce change delivery (Platform Events, Change Data Capture, or
        # outbound messages) is not modeled here; freshness comes from the
        # SystemModstamp incremental query.
        return []

    # --- helpers ---
    def _shape(self) -> _ObjectShape:
        shape = _OBJECTS.get(self._object)
        if shape is None:
            supported = ", ".join(_OBJECTS)
            raise ValueError(f"unsupported object {self._object!r}; supported: {supported}")
        return shape

    def _checked_id(self, record_id: str) -> str:
        if not _SF_ID.fullmatch(record_id):
            raise ValueError(f"not a Salesforce record id: {record_id!r}")
        return record_id

    def _client(self) -> httpx.AsyncClient:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        return httpx.AsyncClient(base_url=self._instance_url, headers=headers, timeout=30.0)

    def _ref(self, record_id: str, modstamp: str) -> DocRef:
        return DocRef(
            source_doc_id=record_id,
            instance_id=self.instance_id,
            updated_at=datetime.fromisoformat(modstamp),
        )

    async def _acl(self, client: httpx.AsyncClient, record_id: str) -> AccessControl:
        # Read the explicit shares from the object's share table. This is a best
        # effort, fail-closed view (see the module ACL caveat): it captures stated
        # shares, not OWD, not role-hierarchy inheritance, and not ImplicitChild
        # parent-derived access (Salesforce no longer stores those rows). The
        # access-level field is the object's correctly named "{Object}AccessLevel"
        # column, never a bare "AccessLevel", which exists only on custom objects.
        shape = self._shape()
        record_id = self._checked_id(record_id)
        soql = (
            f"SELECT UserOrGroupId, {shape.access_level_field}, RowCause "
            f"FROM {shape.share_object} "
            f"WHERE {self._object}Id = '{record_id}'"
        )
        users: list[str] = []
        groups: list[str] = []
        url: str | None = f"{_API_PATH}/query/"
        params: dict[str, Any] | None = {"q": soql}
        while url:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
            for row in payload.get("records", []):
                # A share row grants read only when its {Object}AccessLevel is a real
                # read grant (Read, Edit, or All). "None" is a documented valid value
                # that grants NO read to this object (the row exists to carry a sibling
                # column's implicit/related access, e.g. an AccountShare row with
                # AccountAccessLevel="None" but CaseAccessLevel="Read"). Mapping such a
                # row to an allow principal would over-share, so we fail closed and skip
                # any row whose level is not a read grant. See _READ_GRANT_LEVELS.
                level = row.get(shape.access_level_field)
                if level not in _READ_GRANT_LEVELS:
                    continue
                principal = row.get("UserOrGroupId", "")
                if not principal:
                    continue
                if principal.startswith(_USER_KEY_PREFIX):
                    users.append(principal)
                else:
                    groups.append(principal)
            if payload.get("done", True):
                break
            url, params = payload.get("nextRecordsUrl"), None
        # Sharing is additive in Salesforce, so deny stays empty.
        return AccessControl(allow_users=users, allow_groups=groups, principal_ns=self.instance_id)

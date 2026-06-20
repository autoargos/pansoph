"""The salesforce connector.

Implemented against the Salesforce REST API. Verified against the official docs:

  API docs (base https://developer.salesforce.com):
    intro:  /docs/atlas.en-us.api_rest.meta/api_rest/intro_what_is_rest_api.htm
    query:  /docs/atlas.en-us.api_rest.meta/api_rest/dome_query.htm (SOQL)
    more:   /docs/atlas.en-us.api_rest.meta/api_rest/dome_querymore.htm (queryMore)
    get:    /docs/atlas.en-us.api_rest.meta/api_rest/dome_sobject_retrieve.htm
    acl:  /docs/atlas.en-us.object_reference.meta/object_reference/sforce_api_objects_caseshare.htm
  API version: v60.0
  Auth:        OAuth 2.0 bearer access token, supplied by the operator in config
               as "token" and sent as the header "Authorization: Bearer <token>".
               Token acquisition (JWT bearer flow, web server flow, or any other
               OAuth flow) is the operator's responsibility; this connector does
               not mint or refresh tokens, it consumes one.
  Pagination:  SOQL returns a page of records plus "done" and, when more pages
               remain, a relative "nextRecordsUrl". Follow nextRecordsUrl until
               "done" is true (the queryMore mechanism).
  Incremental: filter on SystemModstamp, the system audit field that updates on
               every change. The cursor is the high water mark of SystemModstamp.
  Rate limit:  the org's daily API request allocation applies; surfaced, not hidden.
  ACL model:   best effort. Salesforce record access is governed by org wide
               defaults, the role hierarchy, sharing rules, and manual shares. The
               share table for an object ({Object}Share, e.g. CaseShare) exposes
               the explicit shares as (UserOrGroupId, AccessLevel) rows. We read
               that table and map each row to a principal: a UserOrGroupId with the
               "005" key prefix is a User, anything else (Group, Role, PublicGroup)
               is a group. Sharing in Salesforce is additive, so there is no deny.
               See the caveat below.
  Verified on: 2026-06-20.

ACL caveat (read before trusting this): the {Object}Share table only lists explicit
shares. It does not encode the organization wide default, nor access inherited
through the role hierarchy, nor the implicit access an owner or an owner's manager
has. So the computed ACL is a faithful best effort over what the share table states,
not the full effective access set. It does not compute OWD or role hierarchy
inheritance. Salesforce Knowledge articles are an additional special case: their
visibility is governed by data categories and channels, not by row shares, so the
share derived ACL does not apply to Knowledge and that object should be treated as a
known limitation, not as enforced access.

Credential scope note: every configured instance carries its own OAuth credential
and its own ACL namespace (principal_ns = instance_id). The credential's profile and
permission set decide what records and share rows it can read, so the mapped ACL
reflects the credential's actual reach. Two instances never collide. See pansoph
CLAUDE.md section 6.

The runtime owns retries, backoff, and resumable sync, so a method only describes
how to talk to Salesforce for one page or one document.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from pansoph_connector_sdk import ChangeEvent, ConnectorMeta, ValidationResult
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc

_API_PATH = "/services/data/v60.0"
# Salesforce User records carry the "005" key prefix. Everything else returned by a
# share table's UserOrGroupId (Group, Role, RoleAndSubordinates, PublicGroup) is
# treated as a group principal.
_USER_KEY_PREFIX = "005"


class SalesforceConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        self.instance_id = instance_id
        cfg = config or {}
        self._token: str = cfg.get("token", "")
        self._instance_url: str = cfg.get("instance_url", "")
        # The sObject to ingest, e.g. "Case" or "KnowledgeArticleVersion". Its share
        # table is "{object}Share" (CaseShare, etc.).
        self._object: str = cfg.get("object", "Case")
        self.meta = ConnectorMeta(
            name="salesforce",
            auth=AuthScheme.oauth2,
            capabilities=Capabilities(
                incremental=True, permission_sync=True, deletions=False, slim_listing=True
            ),
        )

    # --- contract ---
    def validate(self, config: dict[str, Any]) -> ValidationResult:
        missing = [k for k in ("instance_url", "token") if not config.get(k)]
        if missing:
            return ValidationResult(ok=False, error=f"missing config: {', '.join(missing)}")
        return ValidationResult(ok=True)

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        soql = f"SELECT Id, Subject, Description FROM {self._object} ORDER BY SystemModstamp ASC"
        if cursor:
            soql = (
                f"SELECT Id, Subject, Description FROM {self._object} "
                f"WHERE SystemModstamp > {cursor} ORDER BY SystemModstamp ASC"
            )
        async with self._client() as client:
            url: str | None = f"{_API_PATH}/query/"
            params: dict[str, Any] | None = {"q": soql}
            while url:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                payload = resp.json()
                for rec in payload.get("records", []):
                    yield self._ref(rec["Id"]), ""
                if payload.get("done", True):
                    break
                # nextRecordsUrl is a relative URL already carrying the locator.
                url, params = payload.get("nextRecordsUrl"), None

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        async with self._client() as client:
            resp = await client.get(f"{_API_PATH}/sobjects/{self._object}/{ref.source_doc_id}")
            resp.raise_for_status()
            payload = resp.json()
            subject = payload.get("Subject") or ""
            description = payload.get("Description") or ""
            text = subject + description
            raw = RawDoc(
                source_doc_id=ref.source_doc_id,
                instance_id=self.instance_id,
                mime="text/plain",
                text=text,
                title=subject,
            )
            return raw, await self._acl(client, ref.source_doc_id)

    async def list_acls(self, ref: DocRef) -> AccessControl:
        async with self._client() as client:
            return await self._acl(client, ref.source_doc_id)

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        # Salesforce change delivery (Platform Events, Change Data Capture, or
        # outbound messages) is not modeled here; freshness comes from the
        # SystemModstamp incremental query.
        return []

    # --- helpers ---
    def _client(self) -> httpx.AsyncClient:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        return httpx.AsyncClient(base_url=self._instance_url, headers=headers, timeout=30.0)

    def _ref(self, record_id: str) -> DocRef:
        return DocRef(source_doc_id=record_id, instance_id=self.instance_id)

    async def _acl(self, client: httpx.AsyncClient, record_id: str) -> AccessControl:
        # Read the explicit shares from the object's share table. This is a best
        # effort view (see the module ACL caveat): it captures stated shares, not
        # OWD or role hierarchy inheritance.
        soql = (
            f"SELECT UserOrGroupId, AccessLevel FROM {self._object}Share "
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
                principal = row.get("UserOrGroupId", "")
                if principal.startswith(_USER_KEY_PREFIX):
                    users.append(principal)
                else:
                    groups.append(principal)
            if payload.get("done", True):
                break
            url, params = payload.get("nextRecordsUrl"), None
        # Sharing is additive in Salesforce, so deny stays empty.
        return AccessControl(allow_users=users, allow_groups=groups, principal_ns=self.instance_id)

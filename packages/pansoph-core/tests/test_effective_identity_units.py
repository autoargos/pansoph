"""The query subject is the user's email when the token carries one, and the tenant is
bound to the token.

Connectors write direct-user grants by the source ACL user's email (Drive, Zendesk,
Confluence, ...). For those grants to resolve, the query subject id must be that same
email, not the opaque IdP subject. The tenant is bound to the authenticated token, never
the request body, so an authenticated user cannot read another tenant by passing a
different tenant_id. This pins both, and the tokenless fallback for the demo switcher.
"""

from __future__ import annotations

import pytest
from pansoph_core import routes
from pansoph_core.routes import Query, _effective_identity


class _FakeUser:
    def __init__(
        self,
        id: str,
        email: str | None,
        group_ids: list[str],
        tenant_id: str = "",
    ) -> None:
        self.id = id
        self.email = email
        self.group_ids = group_ids
        self.tenant_id = tenant_id


async def test_subject_is_email_when_token_carries_one(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _FakeUser(id="c202be4d-sub", email="alice@northwind.local", group_ids=["engineering"])

    async def _fake_identity(_auth: str | None) -> _FakeUser:
        return user

    monkeypatch.setattr(routes, "_identity_from_token", _fake_identity)
    subject, groups, _tenant = await _effective_identity(Query(query="x"), "Bearer tok")
    assert subject == "alice@northwind.local", "the canonical subject is the email"
    assert groups == ["engineering"]


async def test_subject_falls_back_to_id_when_no_email(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _FakeUser(id="svc-account", email=None, group_ids=[])

    async def _fake_identity(_auth: str | None) -> _FakeUser:
        return user

    monkeypatch.setattr(routes, "_identity_from_token", _fake_identity)
    subject, _groups, _tenant = await _effective_identity(Query(query="x"), "Bearer tok")
    assert subject == "svc-account", "with no email the opaque id is the subject"


async def test_tenant_is_bound_to_the_token_not_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    # SECURITY: a token for tenant "acme" must yield tenant "acme" even when the request
    # body asks for "victim". The body tenant is ignored for an authenticated request, so
    # a user cannot read another tenant's data.
    user = _FakeUser(id="alice", email="alice@acme.com", group_ids=[], tenant_id="acme")

    async def _fake_identity(_auth: str | None) -> _FakeUser:
        return user

    monkeypatch.setattr(routes, "_identity_from_token", _fake_identity)
    _subject, _groups, tenant = await _effective_identity(
        Query(query="x", tenant_id="victim"), "Bearer tok"
    )
    assert tenant == "acme", "the tenant must come from the token, never the request body"


async def test_tokenless_token_maps_to_default_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    # A token with no tenant claim maps to the single default tenant, not the body value.
    user = _FakeUser(id="alice", email="alice@x.com", group_ids=[], tenant_id="")

    async def _fake_identity(_auth: str | None) -> _FakeUser:
        return user

    monkeypatch.setattr(routes, "_identity_from_token", _fake_identity)
    _subject, _groups, tenant = await _effective_identity(
        Query(query="x", tenant_id="victim"), "Bearer tok"
    )
    assert tenant == "default"


async def test_tokenless_request_uses_the_body_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_identity(_auth: str | None) -> None:
        return None

    monkeypatch.setattr(routes, "_identity_from_token", _no_identity)
    subject, groups, tenant = await _effective_identity(
        Query(query="x", user="demo@x.com", groups=["g1"], tenant_id="t1"), None
    )
    assert subject == "demo@x.com"
    assert groups == ["g1"]
    assert tenant == "t1", "only the tokenless demo path honors the body tenant"

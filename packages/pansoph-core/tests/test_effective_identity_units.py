"""The query subject is the user's email when the token carries one.

Connectors write direct-user grants by the source ACL user's email (Drive, Zendesk,
Confluence, ...). For those grants to resolve, the query subject id must be that same
email, not the opaque IdP subject. This pins the canonicalization, and the tokenless
fallback for the demo switcher.
"""

from __future__ import annotations

import pytest
from pansoph_core import routes
from pansoph_core.routes import Query, _effective_identity


class _FakeUser:
    def __init__(self, id: str, email: str | None, group_ids: list[str]) -> None:
        self.id = id
        self.email = email
        self.group_ids = group_ids


async def test_subject_is_email_when_token_carries_one(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _FakeUser(id="c202be4d-sub", email="alice@northwind.local", group_ids=["engineering"])

    async def _fake_identity(_auth: str | None) -> _FakeUser:
        return user

    monkeypatch.setattr(routes, "_identity_from_token", _fake_identity)
    subject, groups = await _effective_identity(Query(query="x"), "Bearer tok")
    assert subject == "alice@northwind.local", "the canonical subject is the email"
    assert groups == ["engineering"]


async def test_subject_falls_back_to_id_when_no_email(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _FakeUser(id="svc-account", email=None, group_ids=[])

    async def _fake_identity(_auth: str | None) -> _FakeUser:
        return user

    monkeypatch.setattr(routes, "_identity_from_token", _fake_identity)
    subject, _groups = await _effective_identity(Query(query="x"), "Bearer tok")
    assert subject == "svc-account", "with no email the opaque id is the subject"


async def test_tokenless_request_uses_the_body_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_identity(_auth: str | None) -> None:
        return None

    monkeypatch.setattr(routes, "_identity_from_token", _no_identity)
    subject, groups = await _effective_identity(
        Query(query="x", user="demo@x.com", groups=["g1"]), None
    )
    assert subject == "demo@x.com"
    assert groups == ["g1"]

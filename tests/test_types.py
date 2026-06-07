"""pansoph-types: id construction, ACL defaults, and the auth enum."""

from __future__ import annotations

from pansoph_types import AccessControl, AuthScheme, Document, Section


def test_make_id_joins_three_parts_with_colons() -> None:
    assert Document.make_id("github", "org1", "src/main.py") == "github:org1:src/main.py"


def test_make_id_is_used_by_document_default_construction() -> None:
    doc = Document(
        id=Document.make_id("c", "i", "s"), connector="c", instance_id="i", tenant_id="t"
    )
    assert doc.id == "c:i:s"
    assert doc.acl == AccessControl()  # default is private, no grants
    assert doc.acl_version == 0


def test_access_control_defaults_are_closed() -> None:
    acl = AccessControl()
    assert acl.public is False
    assert acl.allow_groups == [] and acl.allow_users == [] and acl.deny == []


def test_auth_scheme_is_str_enum() -> None:
    # StrEnum members compare equal to their string value, which the manifest relies on.
    assert AuthScheme.oauth2 == "oauth2"
    assert AuthScheme("api_token") is AuthScheme.api_token


def test_section_offsets_optional() -> None:
    s = Section(text="hello")
    assert s.heading is None and s.start_offset is None

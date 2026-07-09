"""Regression: a SpiceDB subject id must be encoded to the engine's legal object id
alphabet, exactly like a resource id.

When the query subject became the user's email (the canonical cross-source principal),
the raw id carried '@' and '.', which SpiceDB rejects (its object id regex is
[a-zA-Z0-9/_|=+-] or '*'). The adapter already encoded resource ids via _safe_obj but not
subject ids, so every write and check with an email subject failed with an INVALID_ARGUMENT
from the engine. This pins that the subject encoding is applied and stays legal.
"""

from __future__ import annotations

import re

from pansoph_authz import Principal
from pansoph_authz.adapters.spicedb import _ns_id, _safe_obj

# The exact object id pattern SpiceDB validates against (from its schema grammar).
_SPICEDB_OBJECT_ID = re.compile(r"^(([a-zA-Z0-9/_|\-=+]{1,})|\*)$")


def test_email_subject_encodes_to_a_legal_spicedb_object_id() -> None:
    # A tenant-namespaced email principal, the shape the query subject now takes.
    principal = Principal(type="user", id="alice@northwind.local", ns="default")
    encoded = _safe_obj(_ns_id(principal))
    assert _SPICEDB_OBJECT_ID.match(encoded), f"{encoded!r} is not a legal SpiceDB object id"
    # The illegal characters are gone, replaced by the =XX hex encoding.
    assert "@" not in encoded and "." not in encoded


def test_subject_encoding_is_injective_across_similar_emails() -> None:
    # Two distinct principals must never collide after encoding, or one user's grants
    # would resolve for another.
    a = _safe_obj(_ns_id(Principal(type="user", id="a.b@x.com", ns="t")))
    b = _safe_obj(_ns_id(Principal(type="user", id="ab@x.com", ns="t")))
    assert a != b


def test_wildcard_and_plain_ids_are_unchanged() -> None:
    # The public wildcard and an already-legal id pass through untouched, so existing
    # tuples keep resolving.
    assert _safe_obj(_ns_id(Principal(type="user", id="*"))) == "*"
    assert _safe_obj(_ns_id(Principal(type="user", id="alice", ns="default"))) == "default_alice"

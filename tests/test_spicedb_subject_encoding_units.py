"""Regression: a SpiceDB subject id must be encoded to the engine's legal object id
alphabet, exactly like a resource id, AND be injective across the namespace boundary.

When the query subject became the user's email (the canonical cross-source principal),
the raw id carried '@' and '.', which SpiceDB rejects (its object id regex is
[a-zA-Z0-9/_|=+-] or '*'). Separately, joining namespace and id with a literal underscore
was ambiguous: ns="acme_corp" id="bob" collided with ns="acme" id="corp_bob", so one
tenant's principal could resolve as another's. _safe_ns_id encodes each component (with the
separator underscore escaped inside them) so both problems are closed.
"""

from __future__ import annotations

import re

from pansoph_authz import Principal
from pansoph_authz.adapters.spicedb import _safe_ns_id

# The exact object id pattern SpiceDB validates against (from its schema grammar).
_SPICEDB_OBJECT_ID = re.compile(r"^(([a-zA-Z0-9/_|\-=+]{1,})|\*)$")


def test_email_subject_encodes_to_a_legal_spicedb_object_id() -> None:
    # A tenant-namespaced email principal, the shape the query subject now takes.
    encoded = _safe_ns_id(Principal(type="user", id="alice@northwind.local", ns="default"))
    assert _SPICEDB_OBJECT_ID.match(encoded), f"{encoded!r} is not a legal SpiceDB object id"
    # The illegal characters are gone, replaced by the =XX hex encoding.
    assert "@" not in encoded and "." not in encoded


def test_subject_encoding_is_injective_across_similar_emails() -> None:
    # Two distinct principals must never collide after encoding, or one user's grants
    # would resolve for another.
    a = _safe_ns_id(Principal(type="user", id="a.b@x.com", ns="t"))
    b = _safe_ns_id(Principal(type="user", id="ab@x.com", ns="t"))
    assert a != b


def test_ns_id_join_is_injective_across_the_underscore_boundary() -> None:
    # The core of the fix: an underscore in the namespace or the id must not let the join
    # collide. (acme_corp, bob) and (acme, corp_bob) are distinct principals in different
    # tenants and must encode to distinct subject ids.
    a = _safe_ns_id(Principal(type="user", id="bob", ns="acme_corp"))
    b = _safe_ns_id(Principal(type="user", id="corp_bob", ns="acme"))
    assert a != b, "an underscore at a different ns/id boundary must not collide across tenants"
    # And an email id with an underscore in the local part is still distinct per tenant.
    c = _safe_ns_id(Principal(type="user", id="a_b@x.com", ns="t1"))
    d = _safe_ns_id(Principal(type="user", id="a_b@x.com", ns="t2"))
    assert c != d


def test_wildcard_and_plain_ids_are_unchanged() -> None:
    # The public wildcard passes through untouched, so public grants keep resolving. A
    # plain namespaced id with no special characters keeps the readable ns_id shape.
    assert _safe_ns_id(Principal(type="user", id="*")) == "*"
    assert _safe_ns_id(Principal(type="user", id="alice", ns="default")) == "default_alice"

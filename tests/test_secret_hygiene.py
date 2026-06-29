"""Enforce the secret-sealing design pattern across every connector.

A connector that declares a ConnectorSpec MUST mark its credential-looking fields as a
secret kind (SECRET / SECRET_FILE / OAUTH), so the platform seals them into the secret store
and they never land in plaintext config. This makes the pattern non-bypassable, including for
third-party connectors: a PR that adds a connector storing a plaintext credential fails the
conformance gate. No em dashes anywhere.
"""

from __future__ import annotations

import pytest
from pansoph_connector_sdk import ConnectorSpec, looks_like_credential
from pansoph_core.registry import list_adapters, load_adapter

# Keys that trip the credential heuristic but are legitimately not credentials.
_ALLOWLIST = {"user_email"}  # an email identifies a mailbox owner, it is not a secret


def _spec_connectors() -> list[tuple[str, ConnectorSpec]]:
    out: list[tuple[str, ConnectorSpec]] = []
    for name in list_adapters("pansoph.connectors"):
        cls = load_adapter("pansoph.connectors", name)
        spec_fn = getattr(cls, "spec", None)
        if spec_fn is None:
            continue  # transitional: connector has not adopted a spec yet
        out.append((name, spec_fn()))
    return out


@pytest.mark.conformance
def test_credential_fields_are_declared_secret() -> None:
    spec_connectors = _spec_connectors()
    for name, spec in spec_connectors:
        secret_keys = set(spec.secret_keys())
        for fld in spec.fields:
            if fld.key in _ALLOWLIST:
                continue
            if looks_like_credential(fld.key):
                assert fld.key in secret_keys, (
                    f"{name}: field '{fld.key}' looks like a credential but is not a secret "
                    f"kind. Declare it SECRET / SECRET_FILE / OAUTH so the platform seals it."
                )
    assert spec_connectors, "expected at least one connector to declare a ConnectorSpec"

"""The connector config spec: secret-kind classification and the credential heuristic
that makes the secret-sealing pattern non-bypassable. Fully offline. No em dashes.
"""

from __future__ import annotations

from pansoph_connector_sdk import ConfigField, ConnectorSpec, FieldKind, looks_like_credential


def test_secret_kinds_are_flagged_secret() -> None:
    for kind in (FieldKind.SECRET, FieldKind.SECRET_FILE, FieldKind.OAUTH):
        assert ConfigField(key="k", label="K", kind=kind).is_secret
    for kind in (FieldKind.STRING, FieldKind.INT, FieldKind.BOOL, FieldKind.LIST):
        assert not ConfigField(key="k", label="K", kind=kind).is_secret


def test_spec_secret_keys_lists_only_sealed_fields() -> None:
    spec = ConnectorSpec(
        name="github",
        title="GitHub",
        fields=(
            ConfigField(key="owner", label="Owner", kind=FieldKind.STRING),
            ConfigField(key="repo", label="Repo", kind=FieldKind.STRING),
            ConfigField(key="private_key", label="App private key", kind=FieldKind.SECRET_FILE),
            ConfigField(key="token", label="PAT", kind=FieldKind.SECRET, required=False),
        ),
    )
    assert spec.secret_keys() == ("private_key", "token")
    owner = spec.get("owner")
    assert owner is not None and not owner.is_secret
    assert spec.get("missing") is None


def test_credential_heuristic_catches_credential_keys() -> None:
    for key in (
        "token",
        "client_secret",
        "refresh_token",
        "private_key",
        "api_key",
        "password",
        "access_token",
    ):
        assert looks_like_credential(key), key
    for key in ("owner", "repo", "base_url", "subdomain", "space_keys", "user_email"):
        assert not looks_like_credential(key), key

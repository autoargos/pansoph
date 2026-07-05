"""The connector configuration spec.

A connector declares a `ConnectorSpec`: the schema of its instance config, with a `kind`
per field. The platform reads it to (a) render the onboarding form, (b) route SECRET /
SECRET_FILE / OAUTH fields into the secret store and keep them out of plaintext config, and
(c) run the in-app OAuth flow for OAUTH fields. This declaration is the design pattern every
connector follows, including third-party ones, so a community connector cannot store a
plaintext credential by construction (the conformance gate enforces it).

This module is pure data, no I/O. No em dashes anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class FieldKind(StrEnum):
    """What a config field is, and therefore how the platform stores and renders it.

    The kind is the security boundary: SECRET, SECRET_FILE, and OAUTH values are always
    sealed in the secret store and replaced in the stored config by a handle. The plain
    kinds (STRING, INT, BOOL, LIST) are stored as ordinary config.
    """

    STRING = "string"  # plain text: owner, repo, base_url, subdomain
    INT = "int"
    BOOL = "bool"
    LIST = "list"  # a list value (csv or newline entered): space_keys, allowed_hosts
    SECRET = "secret"  # noqa: S105  a typed credential value (a token, a client secret)
    SECRET_FILE = "secret_file"  # noqa: S105  a file credential, not a secret literal
    OAUTH = "oauth"  # obtained via an in-app OAuth flow, never typed by hand


# The kinds whose values are sealed in the secret store, never stored as plaintext config.
SECRET_KINDS = frozenset({FieldKind.SECRET, FieldKind.SECRET_FILE, FieldKind.OAUTH})


@dataclass(frozen=True)
class ConfigField:
    """One field in a connector's instance config."""

    key: str
    label: str
    kind: FieldKind = FieldKind.STRING
    required: bool = True
    help: str = ""
    placeholder: str = ""
    # For kind=OAUTH: which provider flow mints this field's value, and the scopes.
    oauth_provider: str | None = None
    oauth_scopes: tuple[str, ...] = ()
    # When set, this field binds to a resource picker instead of free text: its value
    # is chosen from the connector's list_resources of this kind (github owner/repo,
    # drive folder_id/drive_id, confluence space_key, slack channel_ids). The field
    # stays a free-text fallback when the connector declares no resource_discovery, so
    # this is optional and back-compatible for air-gapped or PAT users.
    resource_kind: str | None = None

    @property
    def is_secret(self) -> bool:
        """True when this field's value must be sealed in the secret store."""
        return self.kind in SECRET_KINDS


@dataclass(frozen=True)
class ConnectorSpec:
    """A connector's declared config schema, surfaced to the UI and the secret router."""

    name: str
    title: str
    description: str = ""
    fields: tuple[ConfigField, ...] = field(default_factory=tuple)

    def secret_keys(self) -> tuple[str, ...]:
        """The keys whose values the platform seals in the secret store."""
        return tuple(f.key for f in self.fields if f.is_secret)

    def get(self, key: str) -> ConfigField | None:
        """Return the field with this key, or None."""
        return next((f for f in self.fields if f.key == key), None)


# Substrings that mark a config key as credential-bearing. The conformance gate fails any
# connector whose field with such a key is NOT declared a secret kind, so the sealing
# pattern cannot be bypassed by a connector author, including a third-party one.
_CREDENTIAL_MARKERS = (
    "token",
    "secret",
    "password",
    "passwd",
    "private_key",
    "api_key",
    "apikey",
    "client_secret",
    "refresh_token",
    "access_token",
    "credential",
)


def looks_like_credential(key: str) -> bool:
    """Heuristic: does this field key look like it holds a credential?

    Used by the secret-hygiene conformance check to force credential fields to be declared
    SECRET / SECRET_FILE / OAUTH. A deliberate non-credential key that trips the heuristic
    can be allowlisted in that check, but the default is strict.
    """
    k = key.lower()
    return any(marker in k for marker in _CREDENTIAL_MARKERS)

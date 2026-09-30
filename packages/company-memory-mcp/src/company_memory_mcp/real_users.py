"""The real-user local POC profile: configured DOOR3 identities for the local Keycloak.

Configuration is data, not code. `deploy/company-memory/users.local.json` (gitignored,
real emails) or, when it is absent, nothing (the committed `users.example.json` holds
placeholders and is only a template). Format:

    {"tenant": "door3",
     "users": [{"email": "someone@example.com", "groups": ["everyone", "it-support-publishers"]}]}

Rules enforced here:

- the email is the identity; it is stored lowercased and must be a plain address;
- every configured user is in `everyone` (that is what "configured" means to the
  Company Memory identity adapter); group names are plain `[a-z0-9-]` names;
- passwords are never in this file. The CLI generates one per user into
  `.secrets/real-user-passwords.json` (owner only) and renders them into the realm.

Real users get the Keycloak `tenant` attribute (admin editable only), so their tokens
carry the configured tenant and they never share a tenant with the synthetic users.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .identity import canonical_email

EVERYONE = "everyone"
_GROUP = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_TENANT = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
# Groups that must never be handed to a person through this profile.
_RESERVED = frozenset({"cm-service-accounts", "__pansoph_everyone", "public"})


class UserConfigError(ValueError):
    pass


@dataclass
class RealUser:
    email: str
    groups: list[str] = field(default_factory=lambda: [EVERYONE])


@dataclass
class RealUserProfile:
    tenant: str
    users: list[RealUser]

    def find(self, email: str) -> RealUser | None:
        key = canonical_email(email)
        return next((u for u in self.users if u.email == key), None)

    def to_json(self) -> dict[str, Any]:
        return {
            "tenant": self.tenant,
            "users": [{"email": u.email, "groups": list(u.groups)} for u in self.users],
        }


def check_group(group: str) -> str:
    if not isinstance(group, str) or not _GROUP.match(group) or group in _RESERVED:
        raise UserConfigError(f"bad or reserved group name: {group!r}")
    return group


def parse_profile(data: object) -> RealUserProfile:
    if not isinstance(data, dict):
        raise UserConfigError("users config must be a JSON object")
    tenant = data.get("tenant")
    if not isinstance(tenant, str) or not _TENANT.match(tenant) or tenant == "default":
        # `default` is the synthetic tenant; real identities never share it.
        raise UserConfigError("tenant must be a plain name other than 'default'")
    users: list[RealUser] = []
    for entry in data.get("users") or []:
        if not isinstance(entry, dict):
            raise UserConfigError("each user must be an object")
        email = canonical_email(entry.get("email"))
        if email is None:
            raise UserConfigError(f"bad email: {entry.get('email')!r}")
        if any(u.email == email for u in users):
            raise UserConfigError(f"duplicate user: {email}")
        groups = [check_group(g) for g in entry.get("groups") or []]
        users.append(RealUser(email, list(dict.fromkeys([EVERYONE, *groups]))))
    return RealUserProfile(tenant, users)


def load_profile(local: Path, example: Path) -> RealUserProfile:
    """The local profile, or an empty one with the example's tenant."""
    if local.exists():
        return parse_profile(json.loads(local.read_text(encoding="utf-8")))
    tenant = json.loads(example.read_text(encoding="utf-8")).get("tenant", "door3")
    return RealUserProfile(tenant, [])


def save_profile(profile: RealUserProfile, local: Path) -> None:
    body = {
        "_comment": "Real-user local POC profile (gitignored). Managed by `company-memory user`.",
        **profile.to_json(),
    }
    local.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def ensure_passwords(profile: RealUserProfile, store: dict[str, str]) -> dict[str, str]:
    """A generated password for every configured user; drops removed users."""
    out = {u.email: store.get(u.email) or secrets.token_urlsafe(24) for u in profile.users}
    return out


def keycloak_user(user: RealUser, tenant: str, password: str) -> dict[str, Any]:
    """The Keycloak user representation for realm import."""
    local = user.email.split("@", 1)[0]
    parts = [p.capitalize() for p in re.split(r"[._-]+", local) if p]
    rep: dict[str, Any] = {
        "username": user.email,
        "enabled": True,
        "email": user.email,
        "emailVerified": True,
        "firstName": parts[0] if parts else local,
        "attributes": {"tenant": [tenant]},
        "credentials": [{"type": "password", "value": password, "temporary": False}],
        "groups": [f"/{g}" for g in user.groups],
        "realmRoles": ["member"],
    }
    if parts[1:]:
        rep["lastName"] = " ".join(parts[1:])
    return rep


def merge_into_realm(
    realm: dict[str, Any], profile: RealUserProfile, passwords: dict[str, str]
) -> dict[str, Any]:
    """Add the configured users (and any group they need) to a rendered realm."""
    names = {g["name"] for g in realm.get("groups", [])}
    for user in profile.users:
        for g in user.groups:
            if g not in names:
                realm.setdefault("groups", []).append({"name": g})
                names.add(g)
    taken = {u.get("username") for u in realm.get("users", [])}
    for user in profile.users:
        if user.email in taken:
            raise UserConfigError(f"{user.email} collides with a synthetic demo user")
        realm.setdefault("users", []).append(
            keycloak_user(user, profile.tenant, passwords[user.email])
        )
    return realm

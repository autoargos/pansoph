"""Refuse to start the Company Memory services with an unsafe configuration.

The upstream defaults favor a frictionless demo: a tokenless body identity, the builtin
identity adapter (which authenticates nobody), and literal default secrets. For Company
Memory each of those is a way around the permission boundary (CM-B1, CM-B2), so the
services built from this package check them at startup and exit instead of serving.
"""

from __future__ import annotations

import os

from pansoph_authz import TENANT_EVERYONE

# Secret values shipped as defaults anywhere in the repo, or obviously placeholder.
_KNOWN_DEFAULTS = {"", "change-me", "changeme", "pansoph", "pansoph_local_dev", "admin"}
_SECRET_ENVS = ("POSTGRES_PASSWORD", "SPICEDB_PRESHARED_KEY")
_MIN_SECRET_CHARS = 24


class UnsafeConfigError(RuntimeError):
    """The environment would weaken the Company Memory security boundary."""


def unsafe_config_reasons(env: dict[str, str] | None = None) -> list[str]:
    e = dict(os.environ) if env is None else env
    reasons: list[str] = []
    if e.get("PANSOPH_REQUIRE_AUTH", "").strip().lower() not in ("1", "true", "yes"):
        reasons.append("PANSOPH_REQUIRE_AUTH must be true (no body identity fall back)")
    if e.get("PANSOPH_IDENTITY_ADAPTER", "builtin") != "company_memory":
        reasons.append(
            "PANSOPH_IDENTITY_ADAPTER must be company_memory (OIDC with a verified email "
            "principal and configured users only; builtin authenticates nobody)"
        )
    if not [g for g in e.get("CM_REQUIRED_GROUPS", "").split(",") if g.strip()]:
        reasons.append("CM_REQUIRED_GROUPS must name the configured-user group (everyone)")
    elif TENANT_EVERYONE in e.get("CM_REQUIRED_GROUPS", ""):
        reasons.append("CM_REQUIRED_GROUPS must not be the implicit tenant group")
    if e.get("PANSOPH_AUTHZ_ADAPTER", "spicedb") in ("none", "noop"):
        reasons.append("PANSOPH_AUTHZ_ADAPTER must not disable authorization")
    for name in ("OIDC_ISSUER_URL", "OIDC_CLIENT_ID"):
        if not e.get(name):
            reasons.append(f"{name} must be set")
    for name in _SECRET_ENVS:
        value = e.get(name, "")
        if value.lower() in _KNOWN_DEFAULTS or len(value) < _MIN_SECRET_CHARS:
            reasons.append(f"{name} is a default or too short (run: company-memory init)")
    return reasons


def require_safe_config(env: dict[str, str] | None = None) -> None:
    reasons = unsafe_config_reasons(env)
    if reasons:
        raise UnsafeConfigError("refusing to start: " + "; ".join(reasons))

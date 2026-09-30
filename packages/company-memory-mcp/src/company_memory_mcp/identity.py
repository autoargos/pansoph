"""The Company Memory identity adapter: OIDC plus "configured user" semantics.

Registered as the `company_memory` adapter of the `pansoph.identity` port and selected
with `PANSOPH_IDENTITY_ADAPTER=company_memory`. It wraps the pansoph OIDC adapter and adds
two rules on top of its checks (signature, issuer, audience, expiry, subject, groups from
userinfo per request):

1. The canonical principal is the verified email, lowercased. A token without an email
   the IdP marks verified is rejected (`require_verified_email`). The same token always
   maps to the same principal, which is also the SpiceDB user id (`user:{tenant}_{email}`).
2. Only configured users authenticate. The caller must currently hold one of
   `CM_REQUIRED_GROUPS` (for people, `everyone`: every configured Company Memory user is
   put in it; for the seeder service account, `cm-service-accounts`, which grants no
   reads). A valid token for anyone else is treated exactly like an invalid one (401).
   So `everyone` means "authenticated and configured", never "any request" and never
   "any account the IdP happens to know".

Removing a user from `everyone` therefore revokes all of their access on the next request.
Replacing local Keycloak with a production SSO provider changes only the OIDC settings and
the directory group sync, not this adapter (see docs/company-memory/DEMO.md).
"""

from __future__ import annotations

import os
import re
from typing import Any

REQUIRED_GROUPS_ENV = "CM_REQUIRED_GROUPS"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def canonical_email(value: object) -> str | None:
    """Lowercased, trimmed email, or None if it is not an email address."""
    if not isinstance(value, str):
        return None
    email = value.strip().lower()
    return email if _EMAIL.match(email) else None


def required_groups_from_env() -> frozenset[str]:
    raw = os.environ.get(REQUIRED_GROUPS_ENV, "")
    return frozenset(g.strip() for g in raw.split(",") if g.strip())


class ConfiguredUserIdentity:
    def __init__(self, inner: Any = None, required_groups: frozenset[str] | None = None) -> None:
        if inner is None:
            from pansoph_core import assembly
            from pansoph_identity.adapters.oidc import OIDCIdentity

            kwargs = assembly._IDENTITY_KWARGS["oidc"]()
            kwargs["require_verified_email"] = True
            inner = OIDCIdentity(**kwargs)
        groups = required_groups_from_env() if required_groups is None else required_groups
        if not groups:
            # Fail closed: without a configured-user group every IdP account would pass.
            raise ValueError(f"{REQUIRED_GROUPS_ENV} must name at least one group")
        self._inner = inner
        self._required = frozenset(groups)

    async def authenticate(self, token: str) -> Any:
        user = await self._inner.authenticate(token)
        if user is None:
            return None
        email = canonical_email(getattr(user, "email", None))
        if email is None:
            return None
        if self._required.isdisjoint(getattr(user, "group_ids", []) or []):
            return None
        return user.model_copy(update={"email": email, "id": email})

    async def get_user(self, user_id: str) -> Any:
        return await self._inner.get_user(user_id)

    async def groups_for_user(self, user_id: str) -> set[str]:
        groups: set[str] = await self._inner.groups_for_user(user_id)
        return groups

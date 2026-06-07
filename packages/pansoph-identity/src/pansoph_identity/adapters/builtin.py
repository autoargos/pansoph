"""The built-in identity store. Zero external dependencies.

In-memory for the scaffold, with a clean surface so a Postgres backed version can
slot in behind the same methods. The real value here is correct nested group
resolution, which is the part that is easy to get wrong.
"""

from __future__ import annotations

from pansoph_identity.port import Group, User


class BuiltinIdentity:
    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._groups: dict[str, Group] = {}

    # --- population (admin api will call these; Postgres version persists) ---
    def add_user(self, user: User) -> None:
        self._users[user.id] = user

    def add_group(self, group: Group) -> None:
        self._groups[group.id] = group

    # --- IdentityPort ---
    async def get_user(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    async def groups_for_user(self, user_id: str) -> set[str]:
        user = self._users.get(user_id)
        if user is None:
            return set()
        return self._closure(user.group_ids)

    async def authenticate(self, token: str) -> User | None:
        # The builtin store does not issue tokens; treat the token as a user id for
        # local dev. Real auth uses the OIDC adapter or a session layer in agora.
        return self._users.get(token)

    def _closure(self, seed: list[str]) -> set[str]:
        """Transitive closure of group membership, cycle safe."""
        seen: set[str] = set()
        stack = list(seed)
        while stack:
            gid = stack.pop()
            if gid in seen:
                continue
            seen.add(gid)
            group = self._groups.get(gid)
            if group is not None:
                stack.extend(p for p in group.parent_ids if p not in seen)
        return seen

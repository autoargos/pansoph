# pansoph-identity

Who the principals are. Separate from authz (which decides what they can do), so the two ports compose cleanly.

The default is a built-in store of users, groups, and organizations, so a single box self hosts with zero external dependencies. It can be replaced by an external identity provider over OIDC (Zitadel recommended, also Keycloak, Authentik, Ory). IdP groups map to the same nested group machinery that connector ACLs use, so one mechanism serves both human auth and document permissions.

## Port

```python
class IdentityPort(Protocol):
    async def get_user(self, user_id: str) -> User | None: ...
    async def groups_for_user(self, user_id: str) -> set[str]:   # resolved, nested
        ...
    async def authenticate(self, token: str) -> User | None: ...  # for OIDC
```

`groups_for_user` returns the transitive closure of a user's group memberships, so the authz layer can build subject group filters without re-walking the graph.

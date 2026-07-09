# Identity and access: how pansoph decides what a user can retrieve

This is the operator's guide to the access model. pansoph's one rule is that a user can
only ever retrieve what they are already allowed to see in the source. This page explains
how a signed-in user is matched to the permissions a connector imported, what you as an
operator need to configure, and the current limits. No em dashes anywhere.

## The model in one paragraph

Every document pansoph indexes carries the source's access control list, mapped to
relationship tuples in the authz engine (SpiceDB by default). At query time pansoph takes
the signed-in user, expands their groups, runs a single bulk permission check against the
engine, and returns only the allowed subset. The check is the source of truth. The vector
pre-filter is only a speed optimization and never decides access.

## How a user is identified

- **The subject is the user's email.** When a user signs in through your IdP (OIDC), the
  query subject is their `email` claim. This is deliberate: connectors import direct-user
  grants using the source's email for that user (Google Drive, Zendesk, and others map an
  ACL entry to the person's email), so email is the one identifier that lines up across
  sources. If a token has no email, the opaque subject id is used as a fallback.
- **Groups come from the token.** The user's `groups` claim feeds the group pre-filter and
  the membership sync, so a document shared with a group is retrievable by that group's
  members. Configure your IdP to emit the `groups` claim on the access token.

Consequence you must know: an instance **scope** set to an individual, and any direct-user
grant, resolves by email. If you scope a GitHub instance to a person, set the scope
reference to their email (for example `alice@your-org.com`), not a username.

## Connecting an IdP group to a connector's source group

Some connectors cannot import a per-user list and instead grant a source-level group. A
Slack public channel is granted to the workspace's members, a Jira issue to licensed
users, a SharePoint org-link to org members. pansoph maps these to a namespaced group like
`slack:member:{instance}`, `jira:licensed`, or `sharepoint:org-members`. These grants are
**fail closed**: until you connect them to your own identity groups, no one is a member,
so the documents are indexed but reach no one. This is intentional. Over-sharing is a
security bug; under-sharing is a configuration you complete.

You connect them with a **group alias**: a declared edge from one of your IdP groups to a
connector group. Members of the IdP group are then treated as members of the connector
group and can retrieve its documents, and no one else can.

```bash
# See which connector groups your indexed documents are actually granted to.
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" \
  "http://localhost:8080/api/authz/connector-groups?tenant_id=default"

# Declare that your "engineering" IdP group can read the Slack workspace's channels.
curl -s -X POST -H "Authorization: Bearer $ADMIN_TOKEN" -H "content-type: application/json" \
  "http://localhost:8080/api/authz/group-aliases" \
  -d '{"identity_group":"engineering","connector_group":"slack:member:acme"}'

# List, and remove (removing revokes reach on the next query).
curl -s -H "Authorization: Bearer $ADMIN_TOKEN" "http://localhost:8080/api/authz/group-aliases?tenant_id=default"
curl -s -X DELETE -H "Authorization: Bearer $ADMIN_TOKEN" -H "content-type: application/json" \
  "http://localhost:8080/api/authz/group-aliases" \
  -d '{"identity_group":"engineering","connector_group":"slack:member:acme"}'
```

These routes require a connector-admin role and are audited. An alias only ever adds the
declared connector group to a user's effective groups; an IdP group with no alias reaches
nothing extra, so you can never widen access by accident.

## Current limits

Be aware of these when you plan a deployment. They are documented in each connector module
and stay conservative (they under-share or over-report, never silently leak):

- **Non-email source principals.** A grant to a Slack user id, a GitHub login, or a
  Confluence account id does not auto-resolve to a pansoph user, because those are not
  emails. Email-based direct grants and group aliases cover the common cases; per-source
  principal linking for the rest is planned.
- **Source permissions the API does not expose.** SharePoint classic Deny and broken
  inheritance, and Salesforce restriction rules, are not returned by those sources' APIs,
  so the connector cannot see them. Treat those two connectors as grant-only.
- **First query after a permission change.** Right after a membership or alias change, the
  very next query can miss the new grant while the engine settles, then resolves. Re-run
  the query.

## Turning enforcement off

Access control is a port. Set `PANSOPH_AUTHZ_ADAPTER=none` to run pansoph with no
enforcement (every indexed document is retrievable), for a single-tenant or trusted
deployment, or to plug in your own authorization stack behind the `AuthzPort`.

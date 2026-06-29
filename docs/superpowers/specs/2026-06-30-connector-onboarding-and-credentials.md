# Spec: connector onboarding, credentials, and the secret-sealing design pattern

Date: 2026-06-30
Status: proposed (direction approved: full onboarding subsystem + secret sealing for ALL connectors)
Owner: maintainer

This is the design for the connector onboarding subsystem: how an operator adds a
connector, gets the right fields or an OAuth "Connect" button, and is done, with every
secret sealed in the secret store and never stored in plaintext. The secret-handling is a
CONTRACT imposed on every connector, including third-party ones, so a community connector
cannot store a plaintext credential by construction. No em dashes anywhere, per house style.

## 1. Why (the honest current state)

An audit (2026-06-30) found onboarding is half-built:
- The lens UI renders per-connector fields but from a HARDCODED frontend map
  (`apps/lens/src/pages/Connectors.tsx:30-112`), only 7 connectors, notion and 8 others
  absent. Not backend-driven.
- No OAuth flow anywhere: every credential is a pasted token.
- Secrets are stored as PLAINTEXT JSON in Postgres (`connectors.py:134` `Json(config)`).
  This violates the security-by-construction mandate (CLAUDE.md section 8).
- The auth subsystem (`auth_requirement` / `token_source` / SecretStore, already built) is
  dead code: zero connectors consume it.
- The github App credential flow (JWT to installation token) is not implemented.

This spec closes all of that as one coherent subsystem.

## 2. The keystone: a connector DECLARES its config schema

Every connector exposes a `ConnectorSpec` (a pure data shape, in `pansoph-connector-sdk`).
The platform reads it to render the UI, route secrets, and run OAuth. A connector that does
not declare a field cannot have that field stored, and a field marked secret is ALWAYS
sealed. This is the design pattern third-party connectors inherit.

```python
class FieldKind(StrEnum):
    STRING = "string"        # plain text (owner, repo, base_url, subdomain)
    INT = "int"
    BOOL = "bool"
    LIST = "list"            # csv / newline list (space_keys, allowed_hosts)
    SECRET = "secret"        # a credential value (token, client_secret, api_token)
    SECRET_FILE = "secret_file"  # a file credential (the github App .pem)
    OAUTH = "oauth"          # obtained via an in-app OAuth flow, not typed by hand

@dataclass(frozen=True)
class ConfigField:
    key: str
    label: str
    kind: FieldKind
    required: bool = True
    help: str = ""
    placeholder: str = ""
    # For kind=OAUTH: which provider flow mints this field's value.
    oauth_provider: str | None = None     # "google" | "atlassian" | "slack" | ...
    oauth_scopes: tuple[str, ...] = ()

@dataclass(frozen=True)
class ConnectorSpec:
    name: str
    title: str
    description: str
    fields: tuple[ConfigField, ...]
    # The grant the runtime builds from these fields (maps onto the auth subsystem).
    auth: AuthScheme

class Connector(Protocol):
    # ... existing ...
    @classmethod
    def spec(cls) -> ConnectorSpec: ...   # NEW: every connector declares this
```

The `kind` is the security boundary: `SECRET` / `SECRET_FILE` / `OAUTH` values are ALWAYS
sealed; `STRING` / `INT` / `BOOL` / `LIST` are plain config. The platform routes on `kind`,
so a connector author marks a field secret and gets sealing for free, and cannot mark a
credential plain by accident without a conformance failure (see section 7).

## 3. Secret sealing (the design pattern, enforced for everyone)

On instance create (`POST /api/connectors/instances`):
1. The backend loads the connector's `ConnectorSpec`.
2. It splits the submitted config by field `kind`:
   - SECRET / SECRET_FILE / OAUTH values are written to the `SecretStore` under a
     deterministic handle `pansoph/{tenant}/{instance}/{field}`, and REPLACED in the stored
     config by a reference `{"_secret": "pansoph/{tenant}/{instance}/{field}"}` (Airbyte's
     hydration model).
   - Plain values are stored in the `connector_instances.config` jsonb as today.
3. So the Postgres `config` column holds NO plaintext secret, only handles. The SecretStore
   adapter (envelope or OpenBao) holds the sealed material.

On sync (`run_instance` / `_build_connector`):
1. The runtime reads the stored config, finds `{"_secret": handle}` references, resolves
   them from the SecretStore just-in-time, and either
   (a) builds the connector's `AuthRequirement` and hands it a refreshing `token_source`
       (the path already built in `auth.py`), for connectors whose credential is a
       token/grant, or
   (b) injects the resolved plaintext into the connector's runtime config for non-grant
       secrets, wiped after the sync step.
2. Connectors stop reading `cfg["token"]` directly; they read their credential from the
   provided `token_source` (preferred) or the resolved secret. This is the contract change
   that makes the auth subsystem live.

Enforcement so third-party connectors comply: see section 7 (a conformance check fails any
connector whose spec marks a credential-looking field as non-secret, and any connector that
reads a raw secret from config instead of the provided credential).

## 4. The config-spec endpoint (backend-driven UI)

`GET /api/connectors/{name}/spec` returns the connector's `ConnectorSpec` as JSON. The lens
UI fetches this and renders the form dynamically: a labeled input per field, a password
input for SECRET, a file picker for SECRET_FILE, and a "Connect with {provider}" button for
OAUTH fields. The hardcoded `CONNECTORS` map in lens is deleted. A new or community connector
gets a correct UI with zero frontend changes, restoring the API-first principle.

`GET /api/connectors` is extended to return `{name, title, auth, needs_oauth}` so the
dropdown can show titles and badge OAuth connectors.

## 5. The OAuth subsystem (in-app, per provider)

For OAUTH fields, the platform runs the flow so the operator never pastes a refresh token.

- Per-provider OAuth config (authorize URL, token URL, default scopes, PKCE on/off),
  registered centrally (`pansoph_core.oauth.providers`), parameterized by the connector
  spec's `oauth_provider` + `oauth_scopes` and the instance's client_id/client_secret
  (themselves SECRET fields the operator supplies once per provider app).
- `GET /api/connectors/oauth/{provider}/authorize?instance={id}` builds the authorize URL
  (state + PKCE, `access_type=offline&prompt=consent` for Google, `offline_access` for
  Atlassian, token rotation for Slack) and 302-redirects.
- `GET /api/connectors/oauth/{provider}/callback?code=...&state=...` validates state,
  exchanges the code at the provider token endpoint, captures the REFRESH token, seals it in
  the SecretStore as the instance's refresh-token secret, and resolves cloud_id /
  instance_url where the provider returns it.
- The UI "Connect with {provider}" button opens the authorize endpoint (popup or redirect);
  on callback success the instance's OAuth field is marked connected.
- From then on the `CredentialProvider` REFRESH_TOKEN grant mints and rotates access tokens
  automatically (already built). Rotation re-seals the new refresh token.

This is the Nango / Airbyte OAuth model adapted to pansoph's ports.

## 6. The github App token source (a distinct grant)

The github App flow is not standard OAuth: sign a short-lived RS256 JWT (iss=app_id, exp
<=10m) with the App private key, then POST `/app/installations/{installation_id}/access_tokens`
with that JWT as the bearer to get a ~1h installation token. Add a `GITHUB_APP` grant to the
auth subsystem with its own minter: resolve the .pem (SECRET_FILE) from the store, sign the
JWT, exchange for the installation token, cache it. The github connector declares
`auth=GITHUB_APP` and config fields app_id (string), installation_id (string), owner/repo
(string), private_key (secret_file), with a fine-grained PAT (secret) as an alternate.

## 7. Enforcement: the pattern is mandatory, including for third parties

A connector cannot opt out of secret sealing:
- Conformance check `check_secret_hygiene`: every connector must declare a `ConnectorSpec`;
  any field whose key matches a credential heuristic (token, secret, key, password,
  api_token, private_key, refresh_token, client_secret) MUST have kind SECRET/SECRET_FILE/
  OAUTH, or the check fails. A connector that reads a raw credential from its config dict
  (static analysis / a runtime guard that the resolved config carries no `_secret`-shaped
  leftovers) fails. This makes the pattern non-bypassable for community connectors.
- The conformance gate (`pytest -m conformance`) runs it for every connector, so a PR adding
  a connector that stores a plaintext secret does not merge.

## 8. Phased build (each phase committed and green)

- **Phase 1: the ConnectorSpec contract + secret sealing.** Add ConnectorSpec/ConfigField to
  the SDK; the backend splits config by kind on create and seals secrets to the SecretStore;
  the runtime resolves handles on sync; wire one connector (weblink, trivial) + one secret
  connector (notion, static token) end to end through the sealed path. `check_secret_hygiene`
  conformance check. This makes notion testable with a SEALED token.
- **Phase 2: the spec endpoint + spec-driven UI.** `GET /api/connectors/{name}/spec`; lens
  renders forms from the spec, deletes the hardcoded map, adds the tenant selector. All 15
  connectors appear once they declare specs.
- **Phase 3: github App grant + token source.** Makes the user's App credentials work
  natively; github testable with the App.
- **Phase 4: the OAuth subsystem.** authorize/callback endpoints + per-provider config + the
  "Connect with {provider}" UI; the captured refresh token feeds the REFRESH_TOKEN grant.
  Makes Google/Slack/Atlassian/Notion-OAuth/Dropbox/Linear/Zendesk onboard without pasting.
- **Phase 5: all 15 connectors declare specs and read credentials from token_source** (folds
  into the production rewrites). Removes every `cfg["token"]` read.

Testable milestones: after Phase 1, weblink + notion (sealed). After Phase 3, github (App).
After Phase 4, the OAuth connectors. A clean full-stack boot for live testing happens at each
milestone.

## 9. Sources (patterns this follows)

- Airbyte connector spec + `airbyte_secret` hydration ({"_secret": coordinate}) and external
  secret stores (AWS/GCP/Azure/Vault): the reference for declare-and-seal.
- Nango / Airbyte OAuth: platform-run authorize + callback + refresh-token capture for
  connector credentials.
- GitHub Apps auth (JWT to installation token): docs.github.com/apps authentication.
- The pansoph auth subsystem already built (`auth.py`, `auth_minters.py`): TokenSource,
  CredentialProvider, the REFRESH_TOKEN / CLIENT_CREDENTIALS / JWT_BEARER / STATIC grants,
  and the SecretStore port (envelope + OpenBao).

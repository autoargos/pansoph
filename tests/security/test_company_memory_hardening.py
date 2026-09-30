"""Company Memory deployment and code hardening (CM-B2, CM-H4, CM-H5, CM-H6, CM-M3, CM-M8).

Static checks on the local POC compose file, the startup guard, the agora wrapper, the
OIDC adapter's token and userinfo handling, and instance deletion purging content.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _fakes import FakeAuthz, FakeEmbedder, FakeVectorStore  # noqa: E402
from company_memory_mcp.guards import UnsafeConfigError, require_safe_config  # noqa: E402
from pansoph_connector_sdk import InMemoryConnector  # noqa: E402
from pansoph_identity.adapters.oidc import OIDCIdentity  # noqa: E402
from pansoph_ingestion.acl_log import InMemoryAclLog  # noqa: E402
from pansoph_ingestion.adapters_parser import DoclingParser  # noqa: E402
from pansoph_ingestion.pipeline import IngestionPipeline, purge_documents  # noqa: E402
from pansoph_types import AccessControl  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "deploy" / "company-memory" / "compose.yaml"

pytestmark = pytest.mark.permission


# ---------------------------------------------------------------- CM-B2 compose


def test_compose_publishes_only_loopback_and_only_three_ports() -> None:
    text = COMPOSE.read_text()
    published = re.findall(r'^\s+- "([^"]+:\d+:\d+)"\s*$', text, flags=re.M)
    assert sorted(published) == [
        "127.0.0.1:8080:8080",
        "127.0.0.1:8090:8080",
        "127.0.0.1:8765:8765",
    ]
    assert "0.0.0.0:" not in text.replace("--host=0.0.0.0", "").replace("CM_MCP_HOST: 0.0.0.0", "")


def test_compose_has_no_default_secrets_and_requires_generated_ones() -> None:
    text = COMPOSE.read_text()
    for bad in ("pansoph_local_dev", "change-me", "admin/admin", "PASSWORD: admin", ":-"):
        assert bad not in text, bad
    for name in ("POSTGRES_PASSWORD", "SPICEDB_PRESHARED_KEY", "KC_ADMIN_PASSWORD"):
        assert f"${{{name}:?" in text, f"{name} must be required, not defaulted"
    assert 'PANSOPH_REQUIRE_AUTH: "true"' in text
    assert "PANSOPH_IDENTITY_ADAPTER: company_memory" in text
    assert 'OIDC_REQUIRE_VERIFIED_EMAIL: "true"' in text
    assert "CM_REQUIRED_GROUPS: everyone,cm-service-accounts" in text
    assert 'OIDC_GROUPS_FROM_USERINFO: "true"' in text


def test_secrets_and_state_are_gitignored_and_kept_out_of_the_image() -> None:
    ignore = (REPO / "deploy" / "company-memory" / ".gitignore").read_text()
    assert ".secrets/" in ignore and ".state/" in ignore
    dockerignore = (REPO / "deploy" / "company-memory" / "Dockerfile.dockerignore").read_text()
    assert dockerignore.splitlines()[3] == "*" or "\n*\n" in dockerignore
    dockerfile = (REPO / "deploy" / "company-memory" / "Dockerfile").read_text()
    assert "--frozen" in dockerfile and "||" not in dockerfile


def test_realm_template_holds_no_secret_values() -> None:
    raw = (REPO / "deploy/company-memory/keycloak/realm-company-memory.template.json").read_text()
    realm = json.loads(raw)
    for user in realm["users"]:
        for cred in user.get("credentials", []):
            assert re.fullmatch(r"\$\{CM_PASSWORD_[A-Z]+\}", cred["value"])
    for client in realm["clients"]:
        assert client.get("secret", "${X}").startswith("${")
        assert "*" not in client.get("webOrigins", [])


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"PANSOPH_REQUIRE_AUTH": "false"}, "PANSOPH_REQUIRE_AUTH"),
        ({"PANSOPH_IDENTITY_ADAPTER": "builtin"}, "PANSOPH_IDENTITY_ADAPTER"),
        ({"PANSOPH_IDENTITY_ADAPTER": "oidc"}, "PANSOPH_IDENTITY_ADAPTER"),
        ({"CM_REQUIRED_GROUPS": ""}, "CM_REQUIRED_GROUPS"),
        ({"CM_REQUIRED_GROUPS": "__pansoph_everyone"}, "CM_REQUIRED_GROUPS"),
        ({"PANSOPH_AUTHZ_ADAPTER": "none"}, "PANSOPH_AUTHZ_ADAPTER"),
        ({"POSTGRES_PASSWORD": "pansoph_local_dev"}, "POSTGRES_PASSWORD"),
        ({"SPICEDB_PRESHARED_KEY": "short"}, "SPICEDB_PRESHARED_KEY"),
    ],
)
def test_startup_guard_refuses_unsafe_config(override: dict[str, str], reason: str) -> None:
    safe = {
        "PANSOPH_REQUIRE_AUTH": "true",
        "PANSOPH_IDENTITY_ADAPTER": "company_memory",
        "CM_REQUIRED_GROUPS": "everyone,cm-service-accounts",
        "PANSOPH_AUTHZ_ADAPTER": "spicedb",
        "OIDC_ISSUER_URL": "http://localhost:8090/realms/company-memory",
        "OIDC_CLIENT_ID": "company-memory",
        "POSTGRES_PASSWORD": "x" * 40,
        "SPICEDB_PRESHARED_KEY": "y" * 40,
    }
    require_safe_config(safe)
    with pytest.raises(UnsafeConfigError, match=reason):
        require_safe_config({**safe, **override})


def test_agora_wrapper_drops_grab_and_docs() -> None:
    from company_memory_mcp.agora_app import create_app

    paths = {getattr(r, "path", "") for r in create_app(check_config=False).router.routes}
    assert "/api/search" in paths
    assert paths.isdisjoint({"/api/grab", "/docs", "/redoc", "/openapi.json"})


# ---------------------------------------------------------------- CM-M3, userinfo


_ISSUER = "https://idp.example.test/realms/company-memory"
_JWKS = "https://idp.example.test/certs"
_USERINFO = "https://idp.example.test/userinfo"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks() -> dict[str, object]:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(_KEY.public_key()))
    return {"keys": [{**jwk, "kid": "k1", "alg": "RS256", "use": "sig"}]}


def _token(**claims: object) -> str:
    base = {
        "iss": _ISSUER,
        "aud": "company-memory",
        "sub": "u-1",
        "exp": int(time.time()) + 300,
        "email": "alice@company-memory.test",
        "email_verified": True,
        "groups": ["project-atlas"],
    }
    base.update(claims)
    base = {k: v for k, v in base.items() if v is not None}
    return jwt.encode(base, _KEY, algorithm="RS256", headers={"kid": "k1"})


class _StaticJwks:
    """Stands in for PyJWKClient (which fetches with urllib, outside respx)."""

    def get_signing_key_from_jwt(self, token: str) -> object:
        return jwt.PyJWK.from_dict(_jwks()["keys"][0])  # type: ignore[index]


def _identity(**kw: object) -> OIDCIdentity:
    ident = OIDCIdentity(_ISSUER, "company-memory", jwks_url=_JWKS, **kw)  # type: ignore[arg-type]
    ident._jwks_client = _StaticJwks()
    return ident


def _mock_jwks(router: respx.MockRouter) -> None:
    """Userinfo calls go through httpx; keys are served by _StaticJwks."""


@respx.mock
async def test_groups_come_from_userinfo_not_the_frozen_token() -> None:
    _mock_jwks(respx.mock)
    respx.get(_USERINFO).mock(
        return_value=httpx.Response(200, json={"sub": "u-1", "groups": ["everyone"]})
    )
    user = await _identity(userinfo_url=_USERINFO, groups_from_userinfo=True).authenticate(_token())
    assert user is not None and user.group_ids == ["everyone"]


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error": "invalid_token"}),
        httpx.Response(200, json={"sub": "someone-else", "groups": ["leadership"]}),
    ],
)
async def test_failed_or_mismatched_userinfo_denies(response: httpx.Response) -> None:
    _mock_jwks(respx.mock)
    respx.get(_USERINFO).mock(return_value=response)
    ident = _identity(userinfo_url=_USERINFO, groups_from_userinfo=True)
    assert await ident.authenticate(_token()) is None


@respx.mock
async def test_token_without_expiry_or_wrong_audience_is_rejected() -> None:
    _mock_jwks(respx.mock)
    ident = _identity()
    assert await ident.authenticate(_token()) is not None
    assert await ident.authenticate(_token(exp=None)) is None
    assert await ident.authenticate(_token(aud="another-client")) is None
    assert await ident.authenticate(_token(exp=int(time.time()) - 10)) is None


@respx.mock
async def test_unverified_email_is_not_used_as_the_subject() -> None:
    _mock_jwks(respx.mock)
    user = await _identity().authenticate(_token(email_verified=False))
    assert user is not None and user.email is None


def test_userinfo_mode_requires_a_url() -> None:
    with pytest.raises(ValueError, match="userinfo_url"):
        _identity(groups_from_userinfo=True)


@respx.mock
@pytest.mark.parametrize(
    "claims",
    [{"email_verified": False}, {"email_verified": None}, {"email": None}, {"email": " "}],
)
async def test_required_verified_email_denies_missing_or_unverified(claims: dict) -> None:  # type: ignore[type-arg]
    ident = _identity(require_verified_email=True)
    assert await ident.authenticate(_token()) is not None
    assert await ident.authenticate(_token(**claims)) is None


def _configured(groups: frozenset[str] = frozenset({"everyone"})) -> object:
    from company_memory_mcp.identity import ConfiguredUserIdentity

    return ConfiguredUserIdentity(_identity(require_verified_email=True), required_groups=groups)


@respx.mock
async def test_configured_identity_maps_the_verified_email_deterministically() -> None:
    ident = _configured()
    token = _token(email="Dana.Reader@Example.COM", groups=["everyone"], sub="kc-123")
    first = await ident.authenticate(token)  # type: ignore[attr-defined]
    second = await ident.authenticate(token)  # type: ignore[attr-defined]
    assert first is not None and second is not None
    # One principal, whatever the IdP's casing or the username: the lowercased email.
    assert first.email == first.id == second.email == "dana.reader@example.com"
    assert first.tenant_id == "default"
    tenant = await ident.authenticate(  # type: ignore[attr-defined]
        _token(email="x@example.com", groups=["everyone"], tenant="door3")
    )
    assert tenant is not None and tenant.tenant_id == "door3"


@respx.mock
async def test_configured_identity_rejects_users_outside_the_configured_group() -> None:
    ident = _configured()
    # A validly signed, verified token for an account nobody configured: denied, like an
    # invalid token. Holding other groups (even a publisher group) does not help.
    for groups in ([], ["it-support-publishers"], ["__pansoph_everyone"]):
        assert await ident.authenticate(_token(groups=groups)) is None  # type: ignore[attr-defined]
    assert await ident.authenticate(_token(groups=["everyone"])) is not None  # type: ignore[attr-defined]


def test_configured_identity_needs_a_required_group() -> None:
    from company_memory_mcp.identity import ConfiguredUserIdentity

    with pytest.raises(ValueError, match="CM_REQUIRED_GROUPS"):
        ConfiguredUserIdentity(object(), required_groups=frozenset())


# ---------------------------------------------------------------- CM-H5 purge


async def test_purging_an_instance_removes_chunks_tuples_and_acl_records() -> None:
    store, authz, log = FakeVectorStore(), FakeAuthz(), InMemoryAclLog()
    for instance in ("keep", "gone"):
        conn = InMemoryConnector(instance_id=instance)
        conn.put("d1", f"{instance} doc", AccessControl(allow_groups=["g"]))
        pipeline = IngestionPipeline(
            connector=conn,
            parser=DoclingParser(),
            embedder=FakeEmbedder(),
            vector_store=store,
            authz=authz,
            acl_log=log,
        )
        await pipeline.sync(connector_name="memory", tenant_id="t")
    purged = await purge_documents(
        prefix="memory:gone:", vector_store=store, authz=authz, acl_log=log
    )
    assert purged == 1
    assert {r.doc_id for r in store.rows} == {"memory:keep:d1"}
    assert all(obj != "memory:gone:d1" for _, _, obj in authz.readers)
    assert await log.get("memory:gone:d1") is None
    assert await log.get("memory:keep:d1") is not None
    with pytest.raises(ValueError):
        await purge_documents(prefix="memory", vector_store=store, authz=authz, acl_log=log)

# Connector Verification Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make pansoph connectors trustworthy and cheap to author by adding a CAT-style verification engine with a gated ACL-correctness category, plus a working declarative manifest compiler.

**Architecture:** Everything lands in `pansoph-connector-sdk` (depends only on `pansoph-types`). A `verify/` package runs per-connector conformance from a `conformance.yaml`; a `manifest/` package compiles declarative manifests into objects satisfying the existing `Connector` protocol. HTTP replay is set up by the calling test (respx for hand-authored hermetic fixtures, VCR for live-recorded ones), so the engine stays transport-agnostic.

**Tech Stack:** Python 3.12, pydantic v2, httpx, pytest + pytest-asyncio, `jsonpath-ng` (runtime), `respx` + `vcrpy` + `pytest-recording` + `syrupy` (dev/test).

**Working directory for all commands:** `/Users/yash/work/autoargos/pansoph`. Run `export PATH="$HOME/.local/bin:$PATH"` first so `uv` is found. No em dashes in any code, comment, doc, or commit message. No co-author trailer.

---

### Task 1: Add dependencies

**Files:**
- Modify: `packages/pansoph-connector-sdk/pyproject.toml:9`
- Modify: `pyproject.toml:14-20` (root dependency-groups dev)

- [ ] **Step 1: Add the runtime JSONPath dependency**

In `packages/pansoph-connector-sdk/pyproject.toml`, change line 9 to:

```toml
dependencies = ["pansoph-types>=0.0.1", "pydantic>=2.7", "jsonpath-ng>=1.6"]
```

- [ ] **Step 2: Add dev/test dependencies**

In root `pyproject.toml`, replace the `dev` list (lines 15-20) with:

```toml
dev = [
  "ruff>=0.6",
  "mypy>=1.11",
  "pytest>=8",
  "pytest-asyncio>=0.24",
  "respx>=0.21",
  "vcrpy>=6",
  "pytest-recording>=0.13",
  "syrupy>=4.7",
]
```

- [ ] **Step 3: Add the conformance pytest marker**

In root `pyproject.toml`, add one line to the `markers` list under `[tool.pytest.ini_options]`:

```toml
markers = [
  "permission: document level access control red team tests that gate releases",
  "integration: tests that need live services (postgres, spicedb, ...)",
  "conformance: connector conformance suite, hermetic and offline",
]
```

- [ ] **Step 4: Sync and verify**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv sync --all-packages --dev`
Expected: resolves and installs jsonpath-ng, respx, vcrpy, pytest-recording, syrupy.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml packages/pansoph-connector-sdk/pyproject.toml uv.lock
git commit -m "build: add verification engine dependencies and conformance marker"
```

---

### Task 2: ConformanceConfig model

**Files:**
- Create: `packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/__init__.py`
- Create: `packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/config.py`
- Test: `packages/pansoph-connector-sdk/tests/test_verify_config.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the conformance config model."""

from __future__ import annotations

from pathlib import Path

from pansoph_connector_sdk.verify.config import ConformanceConfig, load_config


def test_load_minimal_config(tmp_path: Path) -> None:
    p = tmp_path / "conformance.yaml"
    p.write_text(
        "connector: memory\n"
        "categories:\n"
        "  spec: {}\n"
        "  permissions:\n"
        "    source_acl: fixtures/permissions/source_acl.json\n"
        "    principals: fixtures/permissions/principals.json\n"
        "    expected: fixtures/permissions/expected_acl.json\n"
    )
    cfg = load_config(p)
    assert cfg.connector == "memory"
    assert cfg.categories.spec is not None
    assert cfg.categories.permissions is not None
    assert cfg.categories.permissions.source_acl == "fixtures/permissions/source_acl.json"
    assert cfg.base_dir == tmp_path


def test_resolve_makes_paths_absolute(tmp_path: Path) -> None:
    p = tmp_path / "conformance.yaml"
    p.write_text(
        "connector: memory\n"
        "categories:\n"
        "  read: { expected_records: fixtures/expected.jsonl }\n"
        "  permissions:\n"
        "    source_acl: a.json\n"
        "    principals: b.json\n"
        "    expected: c.json\n"
    )
    cfg = load_config(p)
    assert cfg.resolve(cfg.categories.read.expected_records) == tmp_path / "fixtures/expected.jsonl"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_verify_config.py -q`
Expected: FAIL with `ModuleNotFoundError: pansoph_connector_sdk.verify.config`

- [ ] **Step 3: Create the package init**

Create `verify/__init__.py`:

```python
"""The connector verification engine: CAT-style conformance, hermetic and gated."""

from __future__ import annotations
```

- [ ] **Step 4: Write the config model**

Create `verify/config.py`:

```python
"""The per-connector conformance configuration, parsed from conformance.yaml.

A conformance.yaml separates config (how to instantiate and reach the source) from
expectations (the fixture files). The engine reads this and runs each declared
category. Capability gated categories are skipped when the connector does not
declare the capability; the permission category is never skipped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class ReadCategory(BaseModel):
    expected_records: str | None = None


class IncrementalCategory(BaseModel):
    enabled: bool = True
    abnormal_state: str | None = None


class DeletionsCategory(BaseModel):
    enabled: bool = True


class ConnectionCategory(BaseModel):
    expect_ok: bool = True


class PermissionsCategory(BaseModel):
    source_acl: str
    principals: str
    expected: str


class Categories(BaseModel):
    spec: dict[str, Any] | None = None
    connection: ConnectionCategory | None = None
    discovery: dict[str, Any] | None = None
    read: ReadCategory | None = None
    incremental: IncrementalCategory | None = None
    deletions: DeletionsCategory | None = None
    permissions: PermissionsCategory | None = None


class ConformanceConfig(BaseModel):
    connector: str
    hermetic_config: str | None = None
    live_config: str | None = None
    fixtures: str = "fixtures/"
    categories: Categories
    base_dir: Path

    def resolve(self, rel: str) -> Path:
        """Resolve a fixture path relative to the conformance.yaml directory."""
        return self.base_dir / rel


def load_config(path: Path) -> ConformanceConfig:
    """Load and validate a conformance.yaml. base_dir is the file's directory."""
    data = yaml.safe_load(path.read_text())
    data["base_dir"] = path.parent
    return ConformanceConfig.model_validate(data)
```

Note: `yaml` (PyYAML) is already a transitive dependency in the workspace. If `uv run` reports it missing, add `pyyaml>=6` to the connector-sdk dependencies and re-sync.

- [ ] **Step 5: Run tests to verify they pass**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_verify_config.py -q`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/ packages/pansoph-connector-sdk/tests/test_verify_config.py
git commit -m "feat: conformance config model for the verification engine"
```

---

### Task 3: Principal roster and allow/deny resolver

**Files:**
- Create: `packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/principals.py`
- Test: `packages/pansoph-connector-sdk/tests/test_verify_principals.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the synthetic principal roster and the visibility resolver."""

from __future__ import annotations

from pansoph_types import AccessControl
from pansoph_connector_sdk.verify.principals import Principal, can_see


def test_public_doc_visible_to_everyone() -> None:
    acl = AccessControl(public=True)
    assert can_see(acl, Principal(user_id="anyone", groups=[]))


def test_group_member_sees_group_doc() -> None:
    acl = AccessControl(allow_groups=["eng"])
    assert can_see(acl, Principal(user_id="alice", groups=["eng"]))
    assert not can_see(acl, Principal(user_id="bob", groups=["sales"]))


def test_user_grant() -> None:
    acl = AccessControl(allow_users=["alice"])
    assert can_see(acl, Principal(user_id="alice", groups=[]))
    assert not can_see(acl, Principal(user_id="bob", groups=[]))


def test_deny_overrides_group_grant() -> None:
    acl = AccessControl(allow_groups=["eng"], deny=["alice"])
    assert not can_see(acl, Principal(user_id="alice", groups=["eng"]))
    assert can_see(acl, Principal(user_id="carol", groups=["eng"]))


def test_deny_overrides_public() -> None:
    acl = AccessControl(public=True, deny=["alice"])
    assert not can_see(acl, Principal(user_id="alice", groups=[]))
    assert can_see(acl, Principal(user_id="bob", groups=[]))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_verify_principals.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `verify/principals.py`:

```python
"""Synthetic principals and the visibility resolver used by the permission category.

The resolver mirrors the retrieval enforcement rule: a grant (public, group, or
user) makes a document visible, and an explicit deny is evaluated last and always
wins. Deny is a final filter here, never an authz exclusion tuple, matching how the
real retrieval path applies it.
"""

from __future__ import annotations

from pansoph_types import AccessControl
from pydantic import BaseModel, Field


class Principal(BaseModel):
    user_id: str
    groups: list[str] = Field(default_factory=list)


def can_see(acl: AccessControl, principal: Principal) -> bool:
    """Return whether `principal` may retrieve a document with this `acl`."""
    if principal.user_id in acl.deny:
        return False
    if acl.public:
        return True
    if principal.user_id in acl.allow_users:
        return True
    return any(g in acl.allow_groups for g in principal.groups)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_verify_principals.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/principals.py packages/pansoph-connector-sdk/tests/test_verify_principals.py
git commit -m "feat: synthetic principals and allow/deny visibility resolver"
```

---

### Task 4: Contract categories (spec, connection, discovery)

**Files:**
- Create: `packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/categories/__init__.py`
- Create: `packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/categories/contract.py`
- Test: `packages/pansoph-connector-sdk/tests/test_verify_contract.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the spec, connection, and discovery categories."""

from __future__ import annotations

import pytest
from pansoph_connector_sdk import InMemoryConnector
from pansoph_types import AccessControl
from pansoph_connector_sdk.verify.categories.contract import (
    check_connection,
    check_discovery,
    check_spec,
)


def _seeded() -> InMemoryConnector:
    c = InMemoryConnector(instance_id="mem")
    c.put("doc1", "hello", AccessControl(public=True))
    return c


def test_spec_passes_for_valid_meta() -> None:
    check_spec(_seeded())  # must not raise


def test_connection_passes() -> None:
    check_connection(_seeded(), config={})


@pytest.mark.asyncio
async def test_discovery_collects_refs() -> None:
    refs = await check_discovery(_seeded())
    assert refs and refs[0].source_doc_id == "doc1"
    assert all(r.instance_id for r in refs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_verify_contract.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create categories package init**

Create `verify/categories/__init__.py`:

```python
"""Conformance categories. Each is a small, independently testable check."""

from __future__ import annotations
```

- [ ] **Step 4: Write the contract categories**

Create `verify/categories/contract.py`:

```python
"""The contract categories: spec, connection, discovery.

These exercise the parts of the Connector protocol that need no fixtures beyond a
seeded or reachable connector. discovery returns the collected DocRefs so later
categories (read, incremental) can reuse them.
"""

from __future__ import annotations

from typing import Any

from pansoph_types import DocRef

from ...contract import Connector, ValidationResult


def check_spec(connector: Connector) -> None:
    meta = connector.meta
    assert meta.name, "connector meta must have a name"
    assert meta.version, "connector meta must have a version"
    assert meta.auth is not None, "connector meta must declare an auth scheme"


def check_connection(connector: Connector, config: dict[str, Any]) -> None:
    vr = connector.validate(config)
    assert isinstance(vr, ValidationResult), "validate must return a ValidationResult"
    assert vr.ok, f"connection check failed: {vr.error}"


async def check_discovery(connector: Connector) -> list[DocRef]:
    refs: list[DocRef] = []
    async for ref, cursor in connector.list_changes(None):
        assert isinstance(ref, DocRef), "list_changes must yield DocRef"
        assert isinstance(cursor, str), "list_changes must yield a string cursor"
        assert ref.instance_id, "every DocRef must carry its instance_id for isolation"
        refs.append(ref)
    assert refs, "seed the connector before conformance: list_changes returned nothing"
    return refs
```

Note: the existing `assert_conforms` requires a non-empty cursor; the declarative weblink connector yields `""`. The discovery category therefore relaxes to "string cursor" and the incremental category (Task 6) enforces non-empty only when the connector declares `incremental`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_verify_contract.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/categories/ packages/pansoph-connector-sdk/tests/test_verify_contract.py
git commit -m "feat: spec, connection, and discovery conformance categories"
```

---

### Task 5: Read category with normalized records

**Files:**
- Create: `packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/categories/read.py`
- Test: `packages/pansoph-connector-sdk/tests/test_verify_read.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the read category."""

from __future__ import annotations

import pytest
from pansoph_connector_sdk import InMemoryConnector
from pansoph_types import AccessControl
from pansoph_connector_sdk.verify.categories.contract import check_discovery
from pansoph_connector_sdk.verify.categories.read import check_read, normalize_record


@pytest.mark.asyncio
async def test_read_returns_normalized_records() -> None:
    c = InMemoryConnector(instance_id="mem")
    c.put("doc1", "hello world", AccessControl(public=True))
    refs = await check_discovery(c)
    records = await check_read(c, refs)
    assert records == [
        {"source_doc_id": "doc1", "mime": "text/plain", "text": "hello world"}
    ]


def test_normalize_strips_volatile_fields() -> None:
    from pansoph_types import RawDoc

    raw = RawDoc(source_doc_id="d", instance_id="i", mime="text/plain", text="x", source_url="http://h")
    rec = normalize_record(raw)
    assert "instance_id" not in rec and "source_url" not in rec
    assert rec == {"source_doc_id": "d", "mime": "text/plain", "text": "x"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_verify_read.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `verify/categories/read.py`:

```python
"""The read category: fetch each discovered ref and normalize it for snapshotting.

normalize_record drops fields that vary between runs or hosts (instance_id, urls)
so a committed snapshot stays stable. The full snapshot comparison against an
expected_records file is done in the engine via syrupy or a direct file compare.
"""

from __future__ import annotations

from typing import Any

from pansoph_types import AccessControl, DocRef, RawDoc

from ...contract import Connector


def normalize_record(raw: RawDoc) -> dict[str, Any]:
    rec: dict[str, Any] = {"source_doc_id": raw.source_doc_id, "mime": raw.mime}
    if raw.title is not None:
        rec["title"] = raw.title
    if raw.text is not None:
        rec["text"] = raw.text
    return rec


async def check_read(connector: Connector, refs: list[DocRef]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for ref in refs:
        raw, acl = await connector.fetch(ref)
        assert isinstance(raw, RawDoc), "fetch must return a RawDoc"
        assert isinstance(acl, AccessControl), "fetch must return an AccessControl"
        assert raw.source_doc_id == ref.source_doc_id, "fetched doc id must match the ref"
        records.append(normalize_record(raw))
    return records
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_verify_read.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/categories/read.py packages/pansoph-connector-sdk/tests/test_verify_read.py
git commit -m "feat: read conformance category with record normalization"
```

---

### Task 6: Incremental and deletions categories

**Files:**
- Create: `packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/categories/sync.py`
- Test: `packages/pansoph-connector-sdk/tests/test_verify_sync.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the incremental and deletions categories."""

from __future__ import annotations

import pytest
from pansoph_connector_sdk import InMemoryConnector
from pansoph_types import AccessControl
from pansoph_connector_sdk.verify.categories.sync import check_incremental


@pytest.mark.asyncio
async def test_incremental_cursor_excludes_seen() -> None:
    c = InMemoryConnector(instance_id="mem")
    c.put("a", "1", AccessControl(public=True))
    c.put("b", "2", AccessControl(public=True))
    await check_incremental(c, abnormal_state="999999")  # must not raise


@pytest.mark.asyncio
async def test_incremental_abnormal_state_yields_nothing() -> None:
    c = InMemoryConnector(instance_id="mem")
    c.put("a", "1", AccessControl(public=True))
    # An abnormally large cursor must produce zero changes.
    seen = [ref async for ref, _ in c.list_changes("999999")]
    assert seen == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_verify_sync.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `verify/categories/sync.py`:

```python
"""Incremental and deletions categories.

incremental: a full pass collects refs and the last cursor; replaying with that
cursor must yield no already-seen doc, and an abnormally large state must yield
zero records. deletions: removing a source doc surfaces as a delete change, which
the caller verifies through the connector's own removal helper or webhook path.
"""

from __future__ import annotations

from ...contract import Connector


async def check_incremental(connector: Connector, abnormal_state: str | None) -> None:
    refs = []
    last_cursor: str | None = None
    async for ref, cursor in connector.list_changes(None):
        assert cursor, "an incremental connector must yield a non-empty cursor"
        refs.append(ref.source_doc_id)
        last_cursor = cursor
    assert refs, "seed the connector before the incremental check"

    seen = set(refs)
    if last_cursor is not None:
        async for ref, _ in connector.list_changes(last_cursor):
            assert ref.source_doc_id not in seen, (
                "incremental cursor leaked an already-synced document"
            )

    if abnormal_state is not None:
        async for _ref, _cursor in connector.list_changes(abnormal_state):
            raise AssertionError("abnormally large state must yield zero records")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_verify_sync.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/categories/sync.py packages/pansoph-connector-sdk/tests/test_verify_sync.py
git commit -m "feat: incremental and deletions conformance categories"
```

---

### Task 7: Permission category (gated)

**Files:**
- Create: `packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/categories/permissions.py`
- Test: `packages/pansoph-connector-sdk/tests/test_verify_permissions.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the gated permission category."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pansoph_connector_sdk import InMemoryConnector
from pansoph_types import AccessControl
from pansoph_connector_sdk.verify.categories.permissions import check_permissions


def _write(tmp_path: Path) -> tuple[Path, Path, Path]:
    principals = tmp_path / "principals.json"
    principals.write_text(json.dumps([
        {"user_id": "alice", "groups": ["eng"]},
        {"user_id": "bob", "groups": ["sales"]},
    ]))
    expected = tmp_path / "expected.json"
    expected.write_text(json.dumps({
        "doc1": {"public": False, "allow_groups": ["eng"], "allow_users": [], "deny": []},
    }))
    source = tmp_path / "source.json"
    source.write_text("{}")
    return source, principals, expected


@pytest.mark.asyncio
async def test_permissions_pass_on_correct_mapping(tmp_path: Path) -> None:
    c = InMemoryConnector(instance_id="mem")
    c.put("doc1", "secret", AccessControl(allow_groups=["eng"]))
    src, pr, exp = _write(tmp_path)
    await check_permissions(c, source_acl=src, principals=pr, expected=exp)  # must not raise


@pytest.mark.asyncio
async def test_permissions_fail_on_oversharing(tmp_path: Path) -> None:
    c = InMemoryConnector(instance_id="mem")
    c.put("doc1", "secret", AccessControl(public=True))  # oversharing vs expected eng-only
    src, pr, exp = _write(tmp_path)
    with pytest.raises(AssertionError, match="set equality"):
        await check_permissions(c, source_acl=src, principals=pr, expected=exp)


@pytest.mark.asyncio
async def test_permissions_fail_when_denied_principal_can_see(tmp_path: Path) -> None:
    c = InMemoryConnector(instance_id="mem")
    c.put("doc1", "secret", AccessControl(allow_groups=["eng"]))
    src = tmp_path / "s.json"; src.write_text("{}")
    pr = tmp_path / "p.json"
    pr.write_text(json.dumps([{"user_id": "alice", "groups": ["eng"]}]))
    exp = tmp_path / "e.json"
    # Expected claims alice is denied, but the connector grants her via group: mismatch.
    exp.write_text(json.dumps({
        "doc1": {"public": False, "allow_groups": ["eng"], "allow_users": [], "deny": ["alice"]},
    }))
    with pytest.raises(AssertionError, match="set equality"):
        await check_permissions(c, source_acl=src, principals=pr, expected=exp)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_verify_permissions.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `verify/categories/permissions.py`:

```python
"""The gated permission category.

For every document in the expected file: the connector's produced AccessControl must
exactly equal the expected one (set equality on public, allow_groups, allow_users,
deny), and the resolved visibility against the principal roster must match what the
expected ACL implies for every principal, including explicit negatives. Oversharing
(a superset) fails as hard as under-sharing. This is the source boundary that
protects the whole ReBAC guarantee.
"""

from __future__ import annotations

import json
from pathlib import Path

from pansoph_types import AccessControl, DocRef

from ...contract import Connector
from ..principals import Principal, can_see


def _acl_key(acl: AccessControl) -> tuple:
    return (
        bool(acl.public),
        frozenset(acl.allow_groups),
        frozenset(acl.allow_users),
        frozenset(acl.deny),
    )


async def check_permissions(
    connector: Connector, source_acl: Path, principals: Path, expected: Path
) -> None:
    roster = [Principal.model_validate(p) for p in json.loads(principals.read_text())]
    expected_map = {
        doc_id: AccessControl.model_validate(acl)
        for doc_id, acl in json.loads(expected.read_text()).items()
    }
    assert expected_map, "permission fixture must assert at least one document"

    for doc_id, want in expected_map.items():
        ref = DocRef(source_doc_id=doc_id, instance_id=connector.instance_id)
        got = await connector.list_acls(ref)
        assert _acl_key(got) == _acl_key(want), (
            f"ACL set equality failed for {doc_id}: got {got!r}, want {want!r}"
        )
        for principal in roster:
            want_see = can_see(want, principal)
            got_see = can_see(got, principal)
            assert got_see == want_see, (
                f"visibility mismatch for {principal.user_id} on {doc_id}: "
                f"got {got_see}, want {want_see}"
            )
```

Note: `connector.instance_id` is read in the test connectors; the `Connector` protocol does not declare it, so the engine (Task 8) passes the configured instance id explicitly. Here the InMemoryConnector exposes it, so this direct call works for the unit test.

- [ ] **Step 4: Run tests to verify they pass**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_verify_permissions.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/categories/permissions.py packages/pansoph-connector-sdk/tests/test_verify_permissions.py
git commit -m "feat: gated permission conformance category with set equality"
```

---

### Task 8: The engine orchestrator

**Files:**
- Create: `packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/engine.py`
- Modify: `packages/pansoph-connector-sdk/src/pansoph_connector_sdk/__init__.py`
- Test: `packages/pansoph-connector-sdk/tests/test_verify_engine.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the conformance engine orchestrator."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pansoph_connector_sdk import InMemoryConnector
from pansoph_types import AccessControl
from pansoph_connector_sdk.verify.engine import run_conformance


@pytest.mark.asyncio
async def test_run_conformance_for_memory(tmp_path: Path) -> None:
    (tmp_path / "fixtures" / "permissions").mkdir(parents=True)
    (tmp_path / "fixtures" / "permissions" / "principals.json").write_text(
        json.dumps([{"user_id": "alice", "groups": ["eng"]}])
    )
    (tmp_path / "fixtures" / "permissions" / "expected.json").write_text(
        json.dumps({"doc1": {"public": True, "allow_groups": [], "allow_users": [], "deny": []}})
    )
    (tmp_path / "fixtures" / "permissions" / "source.json").write_text("{}")
    (tmp_path / "conformance.yaml").write_text(
        "connector: memory\n"
        "categories:\n"
        "  spec: {}\n"
        "  connection: { expect_ok: true }\n"
        "  discovery: {}\n"
        "  incremental: { enabled: true }\n"
        "  permissions:\n"
        "    source_acl: fixtures/permissions/source.json\n"
        "    principals: fixtures/permissions/principals.json\n"
        "    expected: fixtures/permissions/expected.json\n"
    )

    def factory(instance_id: str, config: dict) -> InMemoryConnector:
        c = InMemoryConnector(instance_id=instance_id)
        c.put("doc1", "hello", AccessControl(public=True))
        return c

    result = await run_conformance(tmp_path / "conformance.yaml", factory)
    assert result.passed
    assert "spec" in result.ran and "permissions" in result.ran
```

- [ ] **Step 2: Run test to verify it fails**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_verify_engine.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the engine**

Create `verify/engine.py`:

```python
"""The conformance engine.

run_conformance loads a conformance.yaml, instantiates the connector through a
factory (so the calling test controls config and any HTTP replay context), and runs
each declared category. Capability gated categories (incremental, deletions) are
skipped when the connector does not declare them. The permission category always
runs. The result records which categories ran so a test can assert coverage.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..contract import Connector
from .categories.contract import check_connection, check_discovery, check_spec
from .categories.permissions import check_permissions
from .categories.read import check_read
from .categories.sync import check_incremental
from .config import ConformanceConfig, load_config

Factory = Callable[[str, dict[str, Any]], Connector]


class ConformanceResult(BaseModel):
    connector: str
    ran: list[str]
    passed: bool


def _load_hermetic_config(cfg: ConformanceConfig) -> dict[str, Any]:
    if cfg.hermetic_config:
        return json.loads(cfg.resolve(cfg.hermetic_config).read_text())
    return {}


async def run_conformance(
    config_path: Path, factory: Factory, instance_id: str = "test"
) -> ConformanceResult:
    cfg = load_config(config_path)
    config = _load_hermetic_config(cfg)
    connector = factory(instance_id, config)
    caps = connector.meta.capabilities
    cats = cfg.categories
    ran: list[str] = []

    if cats.spec is not None:
        check_spec(connector)
        ran.append("spec")
    if cats.connection is not None:
        check_connection(connector, config)
        ran.append("connection")

    refs = await check_discovery(connector)
    ran.append("discovery")

    if cats.read is not None:
        records = await check_read(connector, refs)
        if cats.read.expected_records:
            expected = [
                json.loads(line)
                for line in cfg.resolve(cats.read.expected_records).read_text().splitlines()
                if line.strip()
            ]
            assert records == expected, "read records did not match expected_records"
        ran.append("read")

    if cats.incremental is not None and cats.incremental.enabled and caps.incremental:
        abnormal = None
        if cats.incremental.abnormal_state:
            abnormal = cfg.resolve(cats.incremental.abnormal_state).read_text().strip()
        await check_incremental(connector, abnormal)
        ran.append("incremental")

    assert cats.permissions is not None, "the permission category is mandatory for every connector"
    await check_permissions(
        connector,
        source_acl=cfg.resolve(cats.permissions.source_acl),
        principals=cfg.resolve(cats.permissions.principals),
        expected=cfg.resolve(cats.permissions.expected),
    )
    ran.append("permissions")

    return ConformanceResult(connector=cfg.connector, ran=ran, passed=True)
```

- [ ] **Step 4: Export run_conformance and ConformanceResult**

In `__init__.py`, add to the imports and `__all__`:

```python
from .verify.engine import ConformanceResult, run_conformance
```

and add `"run_conformance"` and `"ConformanceResult"` to `__all__`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_verify_engine.py -q`
Expected: PASS (1 passed)

- [ ] **Step 6: Run the whole connector-sdk suite to confirm no regression**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk -q`
Expected: PASS (all existing tests plus the new ones)

- [ ] **Step 7: Commit**

```bash
git add packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/engine.py packages/pansoph-connector-sdk/src/pansoph_connector_sdk/__init__.py packages/pansoph-connector-sdk/tests/test_verify_engine.py
git commit -m "feat: conformance engine orchestrator over all categories"
```

---

### Task 9: VCR config and secret scrubbing helpers

**Files:**
- Create: `packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/fixtures.py`
- Test: `packages/pansoph-connector-sdk/tests/test_verify_fixtures.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for VCR config and scrubbing."""

from __future__ import annotations

from pansoph_connector_sdk.verify.fixtures import scrub_response, vcr_config


def test_vcr_config_filters_secrets() -> None:
    cfg = vcr_config()
    assert "authorization" in [h.lower() for h in cfg["filter_headers"]]
    assert cfg["record_mode"] == "none"


def test_scrub_response_redacts_known_token_fields() -> None:
    response = {"body": {"string": b'{"access_token": "supersecret", "name": "ok"}'}}
    scrubbed = scrub_response(response)
    assert b"supersecret" not in scrubbed["body"]["string"]
    assert b"ok" in scrubbed["body"]["string"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_verify_fixtures.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

Create `verify/fixtures.py`:

```python
"""Helpers for hermetic HTTP replay and secret scrubbing.

vcr_config returns a config dict for pytest-recording / vcrpy that filters auth
material and replays offline (record_mode none). scrub_response redacts known
secret-bearing JSON fields from a recorded response body before it is committed.
Tests that prefer hand-authored fixtures use respx instead and do not need this.
"""

from __future__ import annotations

import json
import re
from typing import Any

_SECRET_FIELDS = ("access_token", "refresh_token", "token", "client_secret", "api_key")
_REDACTED = "REDACTED"


def vcr_config() -> dict[str, Any]:
    return {
        "record_mode": "none",
        "filter_headers": ["authorization", "Authorization", "cookie", "Cookie"],
        "filter_query_parameters": ["token", "key", "access_token"],
        "before_record_response": scrub_response,
    }


def scrub_response(response: dict[str, Any]) -> dict[str, Any]:
    body = response.get("body", {})
    raw = body.get("string")
    if isinstance(raw, bytes):
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            body["string"] = _scrub_text(raw)
            return response
        body["string"] = json.dumps(_scrub_obj(data)).encode("utf-8")
    return response


def _scrub_obj(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: (_REDACTED if k.lower() in _SECRET_FIELDS else _scrub_obj(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_scrub_obj(v) for v in obj]
    return obj


def _scrub_text(raw: bytes) -> bytes:
    text = raw.decode("utf-8", "replace")
    for field in _SECRET_FIELDS:
        text = re.sub(rf'("{field}"\s*:\s*)"[^"]*"', rf'\1"{_REDACTED}"', text)
    return text.encode("utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_verify_fixtures.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/pansoph-connector-sdk/src/pansoph_connector_sdk/verify/fixtures.py packages/pansoph-connector-sdk/tests/test_verify_fixtures.py
git commit -m "feat: VCR config and secret scrubbing for hermetic replay"
```

---

### Task 10: InMemoryConnector conformance fixtures and gated test

**Files:**
- Create: `packages/pansoph-connector-sdk/tests/conformance/memory/conformance.yaml`
- Create: `packages/pansoph-connector-sdk/tests/conformance/memory/fixtures/permissions/{source.json,principals.json,expected.json}`
- Test: `packages/pansoph-connector-sdk/tests/test_conformance_memory.py`

- [ ] **Step 1: Write the fixtures**

`tests/conformance/memory/conformance.yaml`:

```yaml
connector: memory
categories:
  spec: {}
  connection: { expect_ok: true }
  discovery: {}
  incremental: { enabled: true }
  permissions:
    source_acl: fixtures/permissions/source.json
    principals: fixtures/permissions/principals.json
    expected: fixtures/permissions/expected.json
```

`fixtures/permissions/source.json`: `{}`

`fixtures/permissions/principals.json`:

```json
[
  {"user_id": "alice", "groups": ["eng"]},
  {"user_id": "bob", "groups": ["sales"]}
]
```

`fixtures/permissions/expected.json`:

```json
{
  "handbook": {"public": true, "allow_groups": [], "allow_users": [], "deny": []},
  "secret": {"public": false, "allow_groups": ["eng"], "allow_users": [], "deny": []}
}
```

- [ ] **Step 2: Write the test (gated)**

`tests/test_conformance_memory.py`:

```python
"""End-to-end conformance for the in-memory reference connector, fully offline."""

from __future__ import annotations

from pathlib import Path

import pytest
from pansoph_connector_sdk import InMemoryConnector, run_conformance
from pansoph_types import AccessControl

_CONF = Path(__file__).parent / "conformance" / "memory" / "conformance.yaml"


def _factory(instance_id: str, config: dict) -> InMemoryConnector:
    c = InMemoryConnector(instance_id=instance_id)
    c.put("handbook", "public handbook", AccessControl(public=True))
    c.put("secret", "eng only", AccessControl(allow_groups=["eng"]))
    return c


@pytest.mark.conformance
@pytest.mark.permission
@pytest.mark.asyncio
async def test_memory_conforms() -> None:
    result = await run_conformance(_CONF, _factory)
    assert result.passed
    assert set(result.ran) >= {"spec", "connection", "discovery", "incremental", "permissions"}
```

- [ ] **Step 3: Run the test**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_conformance_memory.py -q`
Expected: PASS (1 passed)

- [ ] **Step 4: Confirm the permission marker selects it**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest -m permission -q`
Expected: the existing permission red-team tests plus `test_memory_conforms` all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/pansoph-connector-sdk/tests/conformance/ packages/pansoph-connector-sdk/tests/test_conformance_memory.py
git commit -m "test: gated offline conformance for the in-memory reference connector"
```

---

### Task 11: Move and extend the manifest schema

**Files:**
- Create: `packages/pansoph-connector-sdk/src/pansoph_connector_sdk/manifest/__init__.py`
- Create: `packages/pansoph-connector-sdk/src/pansoph_connector_sdk/manifest/schema.py`
- Modify: `packages/pansoph-connector-sdk/src/pansoph_connector_sdk/manifest.py` (becomes a thin re-export shim)
- Test: `packages/pansoph-connector-sdk/tests/test_manifest_schema.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the extended manifest schema."""

from __future__ import annotations

from pansoph_connector_sdk.manifest.schema import (
    AclMapping,
    ConnectorManifest,
    ListSpec,
    PaginatorSpec,
)


def test_paginator_defaults_to_none() -> None:
    spec = ListSpec(path="/items")
    assert spec.paginator is None


def test_paginator_link_header() -> None:
    spec = ListSpec(path="/items", paginator=PaginatorSpec(strategy="link_header"))
    assert spec.paginator.strategy == "link_header"


def test_acl_mapping_has_deny() -> None:
    m = AclMapping.model_validate({"from": "restrictions", "deny": "$.deny[*].id"})
    assert m.from_ == "restrictions"
    assert m.deny == "$.deny[*].id"


def test_manifest_round_trips() -> None:
    m = ConnectorManifest.model_validate({
        "connector": "demo",
        "auth": {"type": "api_token"},
        "capabilities": {"incremental": True, "permission_sync": True},
        "base_url": "https://api.demo.test",
        "streams": [{
            "name": "pages",
            "list": {"path": "/pages", "paginator": {"strategy": "page", "page_param": "page"}},
            "fetch": {"path": "/pages/{id}"},
            "acl": {"from": "perms", "groups": "$.groups[*]", "public": "$.is_public"},
        }],
    })
    assert m.streams[0].list.paginator.page_param == "page"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_manifest_schema.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create the manifest package init**

Create `manifest/__init__.py`:

```python
"""Declarative connector manifests: schema and compiler."""

from __future__ import annotations

from .compiler import compile_manifest
from .schema import (
    AclMapping,
    AuthSpec,
    ConnectorManifest,
    FetchSpec,
    ListSpec,
    PaginatorSpec,
    StreamSpec,
)

__all__ = [
    "AclMapping",
    "AuthSpec",
    "ConnectorManifest",
    "FetchSpec",
    "ListSpec",
    "PaginatorSpec",
    "StreamSpec",
    "compile_manifest",
]
```

- [ ] **Step 4: Write the schema**

Create `manifest/schema.py`:

```python
"""The declarative manifest schema, close to Airbyte's low code CDK.

A simple REST source is described here; only awkward sources drop to Python. The
runtime owns pagination, retries, backoff, and incremental cursors, so a manifest
only describes how to talk to the source for one page or one document.
"""

from __future__ import annotations

from typing import Literal

from pansoph_types import AuthScheme, Capabilities
from pydantic import BaseModel, Field


class AuthSpec(BaseModel):
    type: AuthScheme
    scopes: list[str] = Field(default_factory=list)


class AclMapping(BaseModel):
    """How to extract an ACL from a fetched payload, as JSONPath expressions."""

    from_: str = Field(alias="from")
    groups: str | None = None
    users: str | None = None
    public: str | None = None
    deny: str | None = None

    model_config = {"populate_by_name": True}


class PaginatorSpec(BaseModel):
    """One of three strategies. page/offset use the param names; cursor reads a body
    field; link_header follows the RFC 5988 next link."""

    strategy: Literal["page", "offset", "cursor", "link_header"]
    page_param: str = "page"
    size_param: str = "per_page"
    page_size: int = 100
    cursor_path: str | None = None  # JSONPath to the next cursor in the body
    cursor_param: str | None = None  # query param to send the cursor back in


class ListSpec(BaseModel):
    path: str
    since: str | None = None  # JSONPath to the change timestamp
    cursor: str | None = None  # cursor field name
    records: str | None = None  # JSONPath to the record array, default whole body
    id_field: str = "id"  # field on each record that becomes source_doc_id
    paginator: PaginatorSpec | None = None


class FetchSpec(BaseModel):
    path: str
    text: str | None = None  # JSONPath to the document text in the payload


class StreamSpec(BaseModel):
    name: str
    list: ListSpec
    fetch: FetchSpec
    acl: AclMapping | None = None


class ConnectorManifest(BaseModel):
    connector: str
    auth: AuthSpec
    capabilities: Capabilities
    streams: list[StreamSpec]
    base_url: str | None = None
```

- [ ] **Step 5: Replace the old manifest.py with a shim**

Replace the contents of `manifest.py` with:

```python
"""Back-compat shim. The manifest schema and compiler moved to the manifest package."""

from __future__ import annotations

from .manifest.compiler import compile_manifest
from .manifest.schema import (
    AclMapping,
    AuthSpec,
    ConnectorManifest,
    FetchSpec,
    ListSpec,
    StreamSpec,
)

__all__ = [
    "AclMapping",
    "AuthSpec",
    "ConnectorManifest",
    "FetchSpec",
    "ListSpec",
    "StreamSpec",
    "compile_manifest",
]
```

Note: `manifest.py` and the `manifest/` package cannot coexist as the same import name. Delete `manifest.py` instead and rely on the package, unless an existing import depends on `...manifest import X`. Search first: `grep -rn "from .manifest import\|import manifest" packages/`. If nothing depends on it, delete `manifest.py`. If something does, keep the package and update those imports to `from .manifest.schema import X`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_manifest_schema.py -q`
Expected: PASS (4 passed). The compiler import resolves once Task 12 lands; if running before Task 12, temporarily import only from `.schema` in the test.

- [ ] **Step 7: Commit**

```bash
git add packages/pansoph-connector-sdk/src/pansoph_connector_sdk/manifest/ packages/pansoph-connector-sdk/tests/test_manifest_schema.py
git rm packages/pansoph-connector-sdk/src/pansoph_connector_sdk/manifest.py 2>/dev/null || true
git commit -m "feat: move and extend the declarative manifest schema"
```

---

### Task 12: Manifest components

**Files:**
- Create: `packages/pansoph-connector-sdk/src/pansoph_connector_sdk/manifest/components.py`
- Test: `packages/pansoph-connector-sdk/tests/test_manifest_components.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the manifest runtime components."""

from __future__ import annotations

from pansoph_types import AccessControl
from pansoph_connector_sdk.manifest.components import build_acl, extract
from pansoph_connector_sdk.manifest.schema import AclMapping


def test_extract_jsonpath_list() -> None:
    payload = {"results": [{"id": "a"}, {"id": "b"}]}
    assert extract(payload, "$.results[*].id") == ["a", "b"]


def test_build_acl_public_true() -> None:
    mapping = AclMapping.model_validate({"from": "x", "public": "$.is_public"})
    acl = build_acl({"is_public": True}, mapping, principal_ns="inst")
    assert acl == AccessControl(public=True, principal_ns="inst")


def test_build_acl_groups_and_deny() -> None:
    mapping = AclMapping.model_validate(
        {"from": "x", "groups": "$.read.groups[*]", "deny": "$.blocked[*]"}
    )
    acl = build_acl(
        {"read": {"groups": ["eng", "sales"]}, "blocked": ["mallory"]},
        mapping,
        principal_ns="inst",
    )
    assert acl.allow_groups == ["eng", "sales"]
    assert acl.deny == ["mallory"]
    assert acl.public is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_manifest_components.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the components**

Create `manifest/components.py`:

```python
"""Runtime components a compiled manifest is built from.

extract evaluates a JSONPath against a payload. build_acl turns an AclMapping plus a
payload into an AccessControl. The Requester, Paginator, and Cursor behaviors are
expressed as small helpers the compiled connector composes; the durable runtime
still owns the actual retry and checkpoint loop.
"""

from __future__ import annotations

from typing import Any

from jsonpath_ng.ext import parse as jsonpath_parse
from pansoph_types import AccessControl


def extract(payload: Any, expr: str) -> list[Any]:
    """Return all values matching a JSONPath expression."""
    return [m.value for m in jsonpath_parse(expr).find(payload)]


def extract_one(payload: Any, expr: str | None, default: Any = None) -> Any:
    if not expr:
        return default
    values = extract(payload, expr)
    return values[0] if values else default


def build_acl(payload: Any, mapping: Any, principal_ns: str) -> AccessControl:
    groups = extract(payload, mapping.groups) if mapping.groups else []
    users = extract(payload, mapping.users) if mapping.users else []
    deny = extract(payload, mapping.deny) if mapping.deny else []
    public = bool(extract_one(payload, mapping.public, default=False))
    return AccessControl(
        public=public,
        allow_groups=[str(g) for g in groups],
        allow_users=[str(u) for u in users],
        deny=[str(d) for d in deny],
        principal_ns=principal_ns,
    )
```

Note: `jsonpath_ng.ext` enables the extended syntax (filters, `[*]`). The dependency was added in Task 1.

- [ ] **Step 4: Run tests to verify they pass**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_manifest_components.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add packages/pansoph-connector-sdk/src/pansoph_connector_sdk/manifest/components.py packages/pansoph-connector-sdk/tests/test_manifest_components.py
git commit -m "feat: manifest runtime components (jsonpath extract, acl builder)"
```

---

### Task 13: The manifest compiler

**Files:**
- Create: `packages/pansoph-connector-sdk/src/pansoph_connector_sdk/manifest/compiler.py`
- Test: `packages/pansoph-connector-sdk/tests/test_manifest_compiler.py`

- [ ] **Step 1: Write the failing test (respx-mocked, no creds)**

```python
"""Tests for compile_manifest, exercised against a respx-mocked REST source."""

from __future__ import annotations

import httpx
import pytest
import respx
from pansoph_connector_sdk.manifest import compile_manifest
from pansoph_connector_sdk.manifest.schema import ConnectorManifest

_MANIFEST = ConnectorManifest.model_validate({
    "connector": "demo",
    "auth": {"type": "none"},
    "capabilities": {"incremental": False, "permission_sync": True},
    "base_url": "https://api.demo.test",
    "streams": [{
        "name": "pages",
        "list": {"path": "/pages", "records": "$.items[*]", "id_field": "id"},
        "fetch": {"path": "/pages/{id}", "text": "$.body"},
        "acl": {"from": "acl", "groups": "$.acl.groups[*]", "public": "$.acl.public"},
    }],
})


@pytest.mark.asyncio
@respx.mock
async def test_compiled_connector_lists_and_fetches() -> None:
    respx.get("https://api.demo.test/pages").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "p1"}, {"id": "p2"}]})
    )
    respx.get("https://api.demo.test/pages/p1").mock(
        return_value=httpx.Response(200, json={"body": "hello", "acl": {"public": False, "groups": ["eng"]}})
    )
    Compiled = compile_manifest(_MANIFEST)
    conn = Compiled(instance_id="demo", config={})
    refs = [ref async for ref, _ in conn.list_changes(None)]
    assert [r.source_doc_id for r in refs] == ["p1", "p2"]
    raw, acl = await conn.fetch(refs[0])
    assert raw.text == "hello"
    assert acl.allow_groups == ["eng"] and acl.public is False
    assert conn.meta.name == "demo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_manifest_compiler.py -q`
Expected: FAIL with `NotImplementedError` (today's stub) or `ModuleNotFoundError`

- [ ] **Step 3: Write the compiler**

Create `manifest/compiler.py`:

```python
"""Compile a declarative manifest into a class satisfying the Connector protocol.

The compiled connector only describes how to talk to the source for one page or one
document. The durable runtime still owns the retry, backoff, and cursor checkpoint
loop. A single-stream manifest is supported in this first cut; multi-stream support
extends the same factory by iterating streams.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
from pansoph_types import AccessControl, AuthScheme, DocRef, RawDoc

from ..contract import ChangeEvent, ConnectorMeta, ValidationResult
from .components import build_acl, extract, extract_one
from .schema import ConnectorManifest


def compile_manifest(manifest: ConnectorManifest) -> type:
    """Return a Connector class for this manifest. Validate-on-construct already
    happened when the ConnectorManifest model was built."""
    if len(manifest.streams) != 1:
        raise NotImplementedError("the first compiler cut supports a single stream")
    stream = manifest.streams[0]

    class _Compiled:
        def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
            self.instance_id = instance_id
            cfg = config or {}
            self._token: str = cfg.get("token", "")
            self._base_url: str = cfg.get("base_url", manifest.base_url or "")
            self.meta = ConnectorMeta(
                name=manifest.connector,
                auth=manifest.auth.type,
                capabilities=manifest.capabilities,
            )

        def _client(self) -> httpx.AsyncClient:
            headers: dict[str, str] = {}
            if self._token and manifest.auth.type != AuthScheme.none:
                headers["Authorization"] = f"Bearer {self._token}"
            return httpx.AsyncClient(base_url=self._base_url, headers=headers, timeout=30.0)

        def validate(self, config: dict[str, Any]) -> ValidationResult:
            return ValidationResult(ok=True)

        async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
            async with self._client() as client:
                resp = await client.get(stream.list.path)
                resp.raise_for_status()
                payload = resp.json()
                records = extract(payload, stream.list.records) if stream.list.records else payload
                for rec in records:
                    doc_id = str(rec.get(stream.list.id_field))
                    yield DocRef(source_doc_id=doc_id, instance_id=self.instance_id), doc_id

        async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
            async with self._client() as client:
                path = stream.fetch.path.replace("{id}", ref.source_doc_id)
                resp = await client.get(path)
                resp.raise_for_status()
                payload = resp.json()
                text = extract_one(payload, stream.fetch.text, default="")
                raw = RawDoc(
                    source_doc_id=ref.source_doc_id,
                    instance_id=self.instance_id,
                    mime="text/plain",
                    text=str(text),
                )
                return raw, await self._acl(payload)

        async def list_acls(self, ref: DocRef) -> AccessControl:
            async with self._client() as client:
                path = stream.fetch.path.replace("{id}", ref.source_doc_id)
                resp = await client.get(path)
                resp.raise_for_status()
                return await self._acl(resp.json())

        async def _acl(self, payload: Any) -> AccessControl:
            if stream.acl is None:
                return AccessControl(public=True, principal_ns=self.instance_id)
            return build_acl(payload, stream.acl, principal_ns=self.instance_id)

        async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
            return []

    return _Compiled
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_manifest_compiler.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Run the whole connector-sdk suite**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add packages/pansoph-connector-sdk/src/pansoph_connector_sdk/manifest/compiler.py packages/pansoph-connector-sdk/tests/test_manifest_compiler.py
git commit -m "feat: implement the declarative manifest compiler"
```

---

### Task 14: A compiled connector passes full conformance

**Files:**
- Test: `packages/pansoph-connector-sdk/tests/test_conformance_compiled.py`

- [ ] **Step 1: Write the gated test**

```python
"""A manifest-compiled connector passes the same conformance suite, incl permissions."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_sdk import run_conformance
from pansoph_connector_sdk.manifest import compile_manifest
from pansoph_connector_sdk.manifest.schema import ConnectorManifest

_MANIFEST = ConnectorManifest.model_validate({
    "connector": "demo",
    "auth": {"type": "none"},
    "capabilities": {"permission_sync": True},
    "base_url": "https://api.demo.test",
    "streams": [{
        "name": "pages",
        "list": {"path": "/pages", "records": "$.items[*]", "id_field": "id"},
        "fetch": {"path": "/pages/{id}", "text": "$.body"},
        "acl": {"from": "acl", "groups": "$.acl.groups[*]", "public": "$.acl.public"},
    }],
})


@pytest.mark.conformance
@pytest.mark.permission
@pytest.mark.asyncio
@respx.mock
async def test_compiled_connector_conforms(tmp_path: Path) -> None:
    respx.get("https://api.demo.test/pages").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "p1"}]})
    )
    respx.get("https://api.demo.test/pages/p1").mock(
        return_value=httpx.Response(200, json={"body": "hi", "acl": {"public": False, "groups": ["eng"]}})
    )
    perms = tmp_path / "fixtures" / "permissions"
    perms.mkdir(parents=True)
    (perms / "source.json").write_text("{}")
    (perms / "principals.json").write_text(json.dumps([{"user_id": "alice", "groups": ["eng"]}]))
    (perms / "expected.json").write_text(json.dumps(
        {"p1": {"public": False, "allow_groups": ["eng"], "allow_users": [], "deny": []}}
    ))
    (tmp_path / "conformance.yaml").write_text(
        "connector: demo\n"
        "categories:\n"
        "  spec: {}\n"
        "  connection: { expect_ok: true }\n"
        "  discovery: {}\n"
        "  permissions:\n"
        "    source_acl: fixtures/permissions/source.json\n"
        "    principals: fixtures/permissions/principals.json\n"
        "    expected: fixtures/permissions/expected.json\n"
    )
    Compiled = compile_manifest(_MANIFEST)
    result = await run_conformance(tmp_path / "conformance.yaml", lambda i, c: Compiled(i, c))
    assert result.passed and "permissions" in result.ran
```

- [ ] **Step 2: Run the test**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest packages/pansoph-connector-sdk/tests/test_conformance_compiled.py -q`
Expected: PASS (1 passed)

- [ ] **Step 3: Commit**

```bash
git add packages/pansoph-connector-sdk/tests/test_conformance_compiled.py
git commit -m "test: a compiled manifest connector passes full conformance"
```

---

### Task 15: GitHub connector conformance (respx hermetic)

**Files:**
- Create: `connectors/github/tests/fixtures/permissions/{principals.json,expected.json}`
- Modify: `connectors/github/tests/test_github_conformance.py`

- [ ] **Step 1: Write the permission fixtures**

`connectors/github/tests/fixtures/permissions/principals.json`:

```json
[
  {"user_id": "octocat", "groups": ["core-team"]},
  {"user_id": "outsider", "groups": []}
]
```

`connectors/github/tests/fixtures/permissions/expected.json`:

```json
{
  "README.md": {"public": false, "allow_groups": ["core-team"], "allow_users": ["octocat"], "deny": []}
}
```

- [ ] **Step 2: Replace the skipped test with a respx-driven conformance test**

Replace `connectors/github/tests/test_github_conformance.py` with:

```python
"""Hermetic conformance for the github connector, driven by respx mocks.

No live credentials: every GitHub REST call the connector makes is mocked with a
representative payload. The permission category asserts the private-repo ACL maps to
the collaborators (users) and teams (groups) the credential can see.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_github.connector import GithubConnector
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions

_BASE = "https://api.github.com"
_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> GithubConnector:
    return GithubConnector(
        instance_id="octocat",
        config={"owner": "octocat", "repo": "Hello-World", "branch": "main", "token": "x"},
    )


def _mock_repo(private: bool) -> None:
    respx.get(f"{_BASE}/repos/octocat/Hello-World").mock(
        return_value=httpx.Response(200, json={"default_branch": "main", "private": private})
    )


@pytest.mark.conformance
@pytest.mark.asyncio
@respx.mock
async def test_github_discovery_and_spec() -> None:
    _mock_repo(private=False)
    respx.get(f"{_BASE}/repos/octocat/Hello-World/commits").mock(
        return_value=httpx.Response(200, json=[{"commit": {"committer": {"date": "2026-01-01T00:00:00Z"}}}])
    )
    respx.get(f"{_BASE}/repos/octocat/Hello-World/git/trees/main").mock(
        return_value=httpx.Response(200, json={"tree": [{"type": "blob", "path": "README.md"}]})
    )
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert [r.source_doc_id for r in refs] == ["README.md"]


@pytest.mark.conformance
@pytest.mark.permission
@pytest.mark.asyncio
@respx.mock
async def test_github_permissions_private_repo() -> None:
    _mock_repo(private=True)
    respx.get(f"{_BASE}/repos/octocat/Hello-World/collaborators").mock(
        return_value=httpx.Response(200, json=[{"login": "octocat"}])
    )
    respx.get(f"{_BASE}/repos/octocat/Hello-World/teams").mock(
        return_value=httpx.Response(200, json=[{"slug": "core-team"}])
    )
    conn = _factory()
    await check_permissions(
        conn,
        source_acl=_FIX / "principals.json",  # source state unused here; ACL comes from the API mock
        principals=_FIX / "principals.json",
        expected=_FIX / "expected.json",
    )
```

Note: the github connector's `_acl` ignores `principal_ns` differences only if the expected fixture omits it. Confirm `expected.json` keys match `source_doc_id` values the connector uses for `list_acls` (the connector's `list_acls` ignores the ref id and returns the repo-level ACL, so any key works; use "README.md" for clarity). If the connector sets `principal_ns="octocat"`, the permission category compares only the four sharing fields, so it passes regardless.

- [ ] **Step 3: Run the github conformance tests**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest connectors/github -q`
Expected: PASS (2 passed)

- [ ] **Step 4: Commit**

```bash
git add connectors/github/tests/
git commit -m "test: hermetic respx conformance for the github connector, incl permissions"
```

---

### Task 16: weblink connector conformance

**Files:**
- Create: `connectors/weblink/tests/__init__.py` (if needed) and `connectors/weblink/tests/test_weblink_conformance.py`
- Create: `connectors/weblink/tests/fixtures/permissions/{principals.json,expected.json}`

- [ ] **Step 1: Write the permission fixtures**

`fixtures/permissions/principals.json`:

```json
[{"user_id": "anyone", "groups": []}]
```

`fixtures/permissions/expected.json`:

```json
{"https://example.test/a": {"public": true, "allow_groups": [], "allow_users": [], "deny": []}}
```

- [ ] **Step 2: Write the test**

`connectors/weblink/tests/test_weblink_conformance.py`:

```python
"""Hermetic conformance for the weblink connector (public-only)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pansoph_connector_weblink.connector import WeblinkConnector
from pansoph_connector_sdk.verify.categories.contract import check_discovery, check_spec
from pansoph_connector_sdk.verify.categories.permissions import check_permissions

_FIX = Path(__file__).parent / "fixtures" / "permissions"


def _factory() -> WeblinkConnector:
    return WeblinkConnector(instance_id="web", config={"urls": ["https://example.test/a"]})


@pytest.mark.conformance
@pytest.mark.asyncio
async def test_weblink_discovery() -> None:
    conn = _factory()
    check_spec(conn)
    refs = await check_discovery(conn)
    assert refs[0].source_doc_id == "https://example.test/a"


@pytest.mark.conformance
@pytest.mark.permission
@pytest.mark.asyncio
async def test_weblink_permissions_public_only() -> None:
    conn = _factory()
    await check_permissions(
        conn, source_acl=_FIX / "principals.json", principals=_FIX / "principals.json",
        expected=_FIX / "expected.json",
    )
```

- [ ] **Step 3: Run**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest connectors/weblink -q`
Expected: PASS (2 passed)

- [ ] **Step 4: Commit**

```bash
git add connectors/weblink/tests/
git commit -m "test: hermetic conformance for the weblink connector"
```

---

### Task 17: Google Drive and Confluence conformance (respx hermetic)

**Files:**
- Modify: `connectors/google_drive/tests/test_google_drive_conformance.py`
- Modify: `connectors/confluence/tests/test_confluence_conformance.py`
- Create: matching `fixtures/permissions/{principals.json,expected.json}` under each

- [ ] **Step 1: Read each connector to get exact endpoints and ACL fields**

Run: `sed -n '1,200p' connectors/google_drive/src/pansoph_connector_google_drive/connector.py` and the confluence equivalent. Record the list endpoint, the fetch endpoint, and the permission/ACL endpoint each connector calls, and the exact JSON fields it reads.

- [ ] **Step 2: Write respx-driven conformance tests mirroring Task 15**

For each connector, write `test_<name>_conformance.py` with a `@pytest.mark.conformance` discovery test and a `@pytest.mark.permission` ACL test, mocking the endpoints found in Step 1 with representative payloads, and a `fixtures/permissions/expected.json` whose ACL exactly matches what the connector produces from those payloads. Use the same structure as the github test in Task 15. Replace the `@pytest.mark.skip` body entirely.

- [ ] **Step 3: Run**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest connectors/google_drive connectors/confluence -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add connectors/google_drive/tests/ connectors/confluence/tests/
git commit -m "test: hermetic respx conformance for the drive and confluence connectors"
```

Note on Granola: it subclasses `MCPConnector` from `pansoph-mcp-bridge`. Its conformance belongs with the bridge, exercised through a fake in-memory MCP session, and is tracked as a follow-up task in sub-project B. Until then its conformance test stays skipped with a reason that names that follow-up, so it is not a silently-skipped shipped connector but an explicitly-deferred one.

---

### Task 18: Regression test and CI wiring

**Files:**
- Create: `tests/permission/test_connector_acl_regression.py`
- Modify: `.github/workflows/ci.yml`
- Create: `.github/workflows/connector-live-smoke.yml`

- [ ] **Step 1: Write the oversharing regression test**

`tests/permission/test_connector_acl_regression.py`:

```python
"""Regression: an oversharing connector ACL mapping must fail the permission gate.

If this test ever passes silently (no AssertionError), the permission category has
regressed and oversharing would ship. Named so the bug cannot return unnoticed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pansoph_connector_sdk import InMemoryConnector
from pansoph_types import AccessControl
from pansoph_connector_sdk.verify.categories.permissions import check_permissions


@pytest.mark.permission
@pytest.mark.asyncio
async def test_oversharing_acl_fails_the_gate(tmp_path: Path) -> None:
    c = InMemoryConnector(instance_id="mem")
    c.put("doc1", "secret", AccessControl(public=True))  # connector overshares
    (tmp_path / "s.json").write_text("{}")
    (tmp_path / "p.json").write_text(json.dumps([{"user_id": "bob", "groups": []}]))
    (tmp_path / "e.json").write_text(json.dumps(
        {"doc1": {"public": False, "allow_groups": ["eng"], "allow_users": [], "deny": []}}
    ))
    with pytest.raises(AssertionError):
        await check_permissions(
            c, source_acl=tmp_path / "s.json", principals=tmp_path / "p.json",
            expected=tmp_path / "e.json",
        )
```

- [ ] **Step 2: Run it**

Run: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest tests/permission/test_connector_acl_regression.py -q`
Expected: PASS (1 passed)

- [ ] **Step 3: Add the hermetic conformance CI job**

In `.github/workflows/ci.yml`, add a third job after `permission-gate`:

```yaml
  conformance-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          version: "0.5.x"
          enable-cache: true
      - run: uv python install 3.12
      - run: uv sync --all-packages --dev
      - name: Connector conformance (hermetic, offline)
        run: uv run pytest -m conformance -q
```

- [ ] **Step 4: Add the nightly live-smoke workflow**

Create `.github/workflows/connector-live-smoke.yml`:

```yaml
name: connector-live-smoke

on:
  schedule:
    - cron: "0 7 * * *"
  workflow_dispatch:

jobs:
  live-smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          version: "0.5.x"
          enable-cache: true
      - run: uv python install 3.12
      - run: uv sync --all-packages --dev
      - name: Live smoke (re-record fixtures)
        env:
          PANSOPH_LIVE_SMOKE: "1"
          GITHUB_SMOKE_TOKEN: ${{ secrets.GITHUB_SMOKE_TOKEN }}
        run: uv run pytest -m integration -q || true
      - name: Open a PR if fixtures drifted
        run: |
          if [ -n "$(git status --porcelain)" ]; then
            echo "Fixtures drifted. A maintainer should review the regenerated cassettes."
            git status --porcelain
          fi
```

Note: the live-smoke job is non-blocking by design (`|| true` plus a drift report). Wiring the auto-PR creation uses the standard `peter-evans/create-pull-request` action and is left as a small follow-up so this task stays offline-verifiable.

- [ ] **Step 5: Commit**

```bash
git add tests/permission/test_connector_acl_regression.py .github/workflows/
git commit -m "test: oversharing regression; ci: hermetic conformance gate and live smoke"
```

---

### Task 19: Cleanup and docs

**Files:**
- Modify: any remaining `@pytest.mark.skip` conformance tests for shipped connectors
- Modify: `packages/pansoph-connector-sdk/README.md` (document conformance.yaml and `run_conformance`)

- [ ] **Step 1: Confirm no shipped connector has a silently skipped conformance test**

Run: `grep -rn "pytest.mark.skip" connectors/ packages/`
Expected: only Granola remains, with a reason naming the MCP-bridge follow-up. Everything else is gone.

- [ ] **Step 2: Document the authoring flow in the SDK README**

Add a section to `packages/pansoph-connector-sdk/README.md` describing: write a `conformance.yaml`, provide permission fixtures, run `run_conformance`, and that the permission category is mandatory. Keep it short and accurate.

- [ ] **Step 3: Full gates green**

Run:
```bash
export PATH="$HOME/.local/bin:$PATH"
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
uv run pytest -m permission -q
uv run pytest -m conformance -q
```
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs: document connector conformance authoring; remove skipped tests"
```

---

## Self-Review

**Spec coverage:**
- Verification engine (CAT-style, two modes): Tasks 2-10, 18 (CI), live-smoke workflow Task 18.
- Seven categories incl gated permissions with set equality + negatives + deny: Tasks 4-7, 10.
- Manifest compiler (pydantic models to factory, components, escape hatch via Python connectors): Tasks 11-14.
- ACL correctness gated for every connector: Tasks 7, 10, 14, 15, 16, 17, 18 (regression).
- CI three gates: Task 18 (conformance-gate, permission-gate extended by new tests, nightly live-smoke).
- Rollout order (in-memory, compiled, github, weblink, drive/confluence): Tasks 10, 14, 15, 16, 17.
- Success criteria (run_conformance exists; one real connector passes incl permissions; compile_manifest no longer raises; CI blocks a broken ACL; zero skipped for shipped connectors): Tasks 8, 13, 15, 18, 19.

**Known follow-ups (explicitly deferred, not gaps):** Granola/MCP conformance via a fake MCP session; auto-PR creation in live-smoke; multi-stream manifests; pagination beyond the single-page list in the first compiler cut (the Paginator strategies are in the schema; wiring them into the compiled list loop is a fast follow once a connector needs more than one page).

**Type consistency:** `AccessControl` four sharing fields used consistently; `Principal(user_id, groups)`; `can_see(acl, principal)`; `check_*` signatures match between category modules and the engine; `run_conformance(config_path, factory, instance_id="test")` matches all call sites.

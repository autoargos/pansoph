"""pansoph-connector: a small CLI that scaffolds a connector.

It generates the body structure and the API contract stubs so a contributor starts
from a correctly shaped connector and only fills in the source specific calls. It
does not implement any source API; it gives you the skeleton and the conformance
test, with a header that reminds you to implement against the latest official docs.

Usage:
    pansoph-connector new github
    pansoph-connector new google-drive --dir connectors --kind code
    pansoph-connector new confluence --kind declarative
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    if not s:
        raise SystemExit("connector name must contain letters or digits")
    return s


def _module(slug: str) -> str:
    return f"pansoph_connector_{slug}"


def _class(slug: str) -> str:
    return "".join(part.capitalize() for part in slug.split("_")) + "Connector"


def _pyproject(slug: str, module: str, cls: str) -> str:
    return f"""\
[project]
name = "pansoph-connector-{slug.replace("_", "-")}"
version = "0.0.1"
description = "pansoph connector for {slug}. Implement against the latest official docs."
readme = "README.md"
requires-python = ">=3.12"
license = {{ text = "Apache-2.0" }}
dependencies = ["pansoph-types>=0.0.1", "pansoph-connector-sdk>=0.0.1", "httpx>=0.27"]

[project.entry-points."pansoph.connectors"]
{slug} = "{module}.connector:{cls}"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/{module}"]
"""


def _connector_py(slug: str, cls: str) -> str:
    return f'''\
"""The {slug} connector.

IMPLEMENTATION RULE (see pansoph CLAUDE.md section 6): implement against the latest
official {slug} API documentation. Do not implement from memory. Before filling in
the methods below, verify and then record here:

  API docs:        <official docs URL>
  API version:     <version or date>
  Auth and scopes: <method and required scopes>
  Pagination:      <how list pages work>
  Incremental:     <cursor, change feed, or webhook mechanism>
  Rate limits:     <limits and how this connector respects them>
  ACL model:       <how source permissions map to AccessControl>
  Verified on:     <YYYY-MM-DD by whom>

Getting the ACL mapping wrong is a security bug. The runtime owns pagination,
retries, backoff, and resumable sync, so a method only describes how to talk to the
source for one page or one document.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pansoph_connector_sdk import ChangeEvent, ConnectorMeta, ValidationResult
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc


class {cls}:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        self.instance_id = instance_id
        self._config = config or {{}}
        self.meta = ConnectorMeta(
            name="{slug}",
            auth=AuthScheme.oauth2,
            capabilities=Capabilities(
                incremental=True, permission_sync=True, deletions=True, slim_listing=True
            ),
        )

    def validate(self, config: dict[str, Any]) -> ValidationResult:
        """Cheaply verify the config and credentials, ideally with one light call."""
        raise NotImplementedError("validate: implement against the latest {slug} API docs")

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        """Yield (DocRef, next_cursor) for everything changed since `cursor`.

        Persist nothing yourself; the runtime checkpoints the cursor you yield.
        """
        raise NotImplementedError("list_changes: implement against the latest {slug} API docs")
        yield  # pragma: no cover  (marks this as an async generator)

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        """Fetch one document and its source ACL together."""
        raise NotImplementedError("fetch: implement against the latest {slug} API docs")

    async def list_acls(self, ref: DocRef) -> AccessControl:
        """Return just the ACL for a document. Used when permission_sync is set."""
        raise NotImplementedError("list_acls: implement against the latest {slug} API docs")

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        """Optional. Normalize a source webhook into change events for freshness."""
        return []
'''


def _manifest_yaml(slug: str) -> str:
    return f"""\
# Declarative manifest for the {slug} connector.
# Use this instead of connector.py for simple REST sources. The runtime compiles
# it into a Connector. Verify every path, scope, and JSONPath against the latest
# official {slug} API docs before relying on it.
connector: {slug}
# base_url: https://api.example.com
auth: {{ type: oauth2, scopes: [] }}
capabilities: {{ incremental: true, permission_sync: true, deletions: true }}
streams:
  - name: items
    list:
      path: /items
      since: "$.items[*].updated_at"
      cursor: updated_at
    fetch:
      path: /items/{{id}}
    acl:
      from: permissions
      groups: "$.permissions.groups[*].id"
      users: "$.permissions.users[*].id"
"""


def _test_py(slug: str, module: str, cls: str) -> str:
    # Emit the third-party from-imports already sorted so the generated file is
    # ruff-clean regardless of the slug.
    froms = sorted(
        [
            "from pansoph_connector_sdk import assert_conforms",
            f"from {module}.connector import {cls}",
        ],
        key=lambda line: line.split()[1],
    )
    import_block = "import pytest\n" + "\n".join(froms)
    return f'''\
"""Conformance test for the {slug} connector.

Once the connector is implemented, seed it and assert it conforms. Until then this
test documents the expectation. Run with: pytest (from this package).
"""

from __future__ import annotations

{import_block}


@pytest.mark.skip(reason="implement {slug} against its latest API docs, then enable")
async def test_{slug}_conforms() -> None:
    connector = {cls}(instance_id="test")
    # TODO seed the connector or point it at a recorded fixture, then:
    await assert_conforms(connector)
'''


def _readme(slug: str, cls: str) -> str:
    return f"""\
# pansoph-connector-{slug.replace("_", "-")}

A pansoph connector for {slug}.

Implement it against the latest official {slug} API documentation. See the header of
`src/{_module(slug)}/connector.py` for the checklist of what to verify and record
(endpoints, auth and scopes, pagination, incremental sync, rate limits, ACL model).

It must pass the shared conformance suite (`assert_conforms` from
`pansoph-connector-sdk`) before it merges, and it must map the source permission
model to ACLs faithfully.
"""


def _write(path: Path, content: str, force: bool) -> None:
    if path.exists() and not force:
        raise SystemExit(f"refusing to overwrite {path} (use --force)")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def cmd_new(args: argparse.Namespace) -> int:
    slug = _slug(args.name)
    module = _module(slug)
    cls = _class(slug)
    root = Path(args.dir) / slug
    src = root / "src" / module

    _write(root / "pyproject.toml", _pyproject(slug, module, cls), args.force)
    _write(root / "README.md", _readme(slug, cls), args.force)
    _write(src / "__init__.py", f'"""pansoph connector for {slug}."""\n', args.force)
    if args.kind in ("code", "both"):
        _write(src / "connector.py", _connector_py(slug, cls), args.force)
    if args.kind in ("declarative", "both"):
        _write(root / "manifest.yaml", _manifest_yaml(slug), args.force)
    _write(root / "tests" / f"test_{slug}_conformance.py", _test_py(slug, module, cls), args.force)

    print(f"created connector scaffold at {root}")
    print(f"  module: {module}")
    print(f"  class:  {cls}")
    print(f"  entry point: pansoph.connectors -> {slug}")
    print("next: implement against the latest official API docs, then run its conformance test.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pansoph-connector", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    new = sub.add_parser("new", help="scaffold a new connector")
    new.add_argument("name", help="connector name, e.g. github or google-drive")
    new.add_argument("--dir", default="connectors", help="base directory (default: connectors)")
    new.add_argument(
        "--kind",
        choices=["code", "declarative", "both"],
        default="code",
        help="code stub, declarative manifest, or both (default: code)",
    )
    new.add_argument("--force", action="store_true", help="overwrite existing files")
    new.set_defaults(func=cmd_new)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

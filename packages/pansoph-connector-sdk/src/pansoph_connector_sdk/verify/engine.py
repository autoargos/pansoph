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
from typing import Any, cast

from pydantic import BaseModel

from ..contract import Connector
from .categories.contract import check_connection, check_discovery, check_spec
from .categories.permissions import check_permissions
from .categories.read import check_read
from .categories.sync import check_deletions, check_incremental
from .config import ConformanceConfig, load_config

Factory = Callable[[str, dict[str, Any]], Connector]


class ConformanceResult(BaseModel):
    connector: str
    ran: list[str]
    passed: bool


def _load_hermetic_config(cfg: ConformanceConfig) -> dict[str, Any]:
    if cfg.hermetic_config:
        return cast(dict[str, Any], json.loads(cfg.resolve(cfg.hermetic_config).read_text()))
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

    if cats.deletions is not None and cats.deletions.enabled and caps.deletions:
        await check_deletions(connector, cats.deletions.deleted_state)
        ran.append("deletions")

    assert cats.permissions is not None, "the permission category is mandatory for every connector"
    await check_permissions(
        connector,
        source_acl=cfg.resolve(cats.permissions.source_acl),
        principals=cfg.resolve(cats.permissions.principals),
        expected=cfg.resolve(cats.permissions.expected),
    )
    ran.append("permissions")

    return ConformanceResult(connector=cfg.connector, ran=ran, passed=True)

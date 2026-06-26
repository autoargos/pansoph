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
    # A cursor recorded just before a source document was removed. Listing from it
    # must yield that document as a deleted DocRef. None lists from the start.
    deleted_state: str | None = None


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

"""Tests for the conformance config model."""

from __future__ import annotations

from pathlib import Path

from pansoph_connector_sdk.verify.config import load_config


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
    assert cfg.categories.read is not None
    assert cfg.categories.read.expected_records is not None
    assert cfg.resolve(cfg.categories.read.expected_records) == tmp_path / "fixtures/expected.jsonl"

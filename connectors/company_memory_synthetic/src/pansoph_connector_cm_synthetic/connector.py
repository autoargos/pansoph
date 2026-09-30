"""Synthetic corpus connector: fictional documents from a local directory, for the DOOR3
Company Memory demo and permission tests. Never for real data.

It implements the ordinary connector contract, so a demo seed goes through the same path
a real source does: list_changes, fetch with ACL, the ingestion pipeline, the ACL diff
(revocations first), SpiceDB tuples, and pgvector chunks. No network, no credentials.

Safety: only regular `*.md` files directly inside `corpus_dir` are read (no symlinks, no
subdirectories), the stem must match `[a-z0-9-]+`, and each file is capped in size.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from pansoph_connector_sdk import (
    ChangeEvent,
    ConfigField,
    ConnectorMeta,
    ConnectorSpec,
    FieldKind,
    ValidationResult,
)
from pansoph_types import AccessControl, AuthScheme, Capabilities, DocRef, RawDoc

MAX_FILE_BYTES = 256 * 1024
_STEM = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")
_LIST_KEYS = ("allow_groups", "allow_users", "deny")


class CorpusError(ValueError):
    """A corpus file is malformed, unsafe, or too large."""


def parse_document(raw: str) -> tuple[dict[str, Any], str]:
    """Split a corpus file into (front matter, body). Front matter is `key: value` lines
    between two `---` lines, and the list keys are comma separated."""
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        raise CorpusError("corpus file must start with a '---' front matter block")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration as exc:
        raise CorpusError("unterminated front matter block") from exc
    meta: dict[str, Any] = {k: [] for k in _LIST_KEYS}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise CorpusError(f"bad front matter line: {line!r}")
        key, value = key.strip(), value.strip()
        if key in _LIST_KEYS:
            meta[key] = [v.strip() for v in value.split(",") if v.strip()]
        elif key == "title":
            meta["title"] = value
        else:
            raise CorpusError(f"unknown front matter key: {key!r}")
    if not meta.get("title"):
        raise CorpusError("front matter needs a title")
    return meta, "\n".join(lines[end + 1 :]).strip()


def _encode_cursor(state: dict[str, str]) -> str:
    return base64.urlsafe_b64encode(json.dumps(state, sort_keys=True).encode()).decode()


def _decode_cursor(cursor: str | None) -> dict[str, str]:
    if not cursor:
        return {}
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except (ValueError, json.JSONDecodeError) as exc:
        # A corrupt cursor would silently skip tombstones; refuse instead.
        raise CorpusError("unreadable cursor, reset the instance cursor") from exc
    if not isinstance(data, dict):
        raise CorpusError("unreadable cursor, reset the instance cursor")
    return {str(k): str(v) for k, v in data.items()}


class SyntheticCorpusConnector:
    def __init__(self, instance_id: str, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.instance_id = instance_id
        self._dir = Path(str(cfg.get("corpus_dir", ""))) if cfg.get("corpus_dir") else None
        self.meta = ConnectorMeta(
            name="cm_synthetic",
            auth=AuthScheme.none,
            capabilities=Capabilities(incremental=True, permission_sync=True, deletions=True),
        )

    @classmethod
    def spec(cls) -> ConnectorSpec:
        return ConnectorSpec(
            name="cm_synthetic",
            title="Synthetic corpus (Company Memory demo)",
            description="Fictional documents with per-document ACLs from a local directory.",
            fields=(
                ConfigField(
                    key="corpus_dir",
                    label="Corpus directory",
                    kind=FieldKind.STRING,
                    help="Directory of *.md corpus files readable by agora.",
                ),
            ),
        )

    def validate(self, config: dict[str, Any]) -> ValidationResult:
        path = config.get("corpus_dir")
        if not path or not Path(str(path)).is_dir():
            return ValidationResult(ok=False, error="corpus_dir must be an existing directory")
        return ValidationResult(ok=True)

    def _root(self) -> Path:
        if self._dir is None:
            raise CorpusError("corpus_dir is not configured")
        return self._dir.resolve()

    def _files(self) -> dict[str, Path]:
        root = self._root()
        out: dict[str, Path] = {}
        for path in sorted(root.iterdir()):
            if path.suffix != ".md" or path.is_symlink() or not path.is_file():
                continue
            if path.name.lower() == "readme.md":
                continue
            if not _STEM.match(path.stem):
                raise CorpusError(f"invalid corpus file name: {path.name!r}")
            out[path.stem] = path
        return out

    def _read(self, stem: str) -> str:
        if not _STEM.match(stem):
            raise CorpusError(f"invalid source id: {stem!r}")
        path = self._root() / f"{stem}.md"
        if path.is_symlink() or not path.is_file() or path.resolve().parent != self._root():
            raise CorpusError(f"no such corpus document: {stem!r}")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise CorpusError(f"corpus document too large: {stem!r}")
        return path.read_text(encoding="utf-8")

    async def list_changes(self, cursor: str | None) -> AsyncIterator[tuple[DocRef, str]]:
        state = _decode_cursor(cursor)
        current = {
            stem: hashlib.sha256(self._read(stem).encode()).hexdigest() for stem in self._files()
        }
        for stem in sorted(set(state) - set(current)):
            state.pop(stem)
            ref = DocRef(source_doc_id=stem, instance_id=self.instance_id, deleted=True)
            yield ref, _encode_cursor(state)
        for stem, digest in sorted(current.items()):
            if state.get(stem) == digest:
                continue
            state[stem] = digest
            yield DocRef(source_doc_id=stem, instance_id=self.instance_id), _encode_cursor(state)

    def _acl(self, meta: dict[str, Any]) -> AccessControl:
        return AccessControl(
            public=False,
            allow_groups=list(meta["allow_groups"]),
            allow_users=list(meta["allow_users"]),
            deny=list(meta["deny"]),
        )

    async def fetch(self, ref: DocRef) -> tuple[RawDoc, AccessControl]:
        meta, body = parse_document(self._read(ref.source_doc_id))
        text = f"Title: {meta['title']}\n\n{body}" if body else ""
        raw = RawDoc(
            source_doc_id=ref.source_doc_id,
            instance_id=self.instance_id,
            mime="text/plain",
            text=text,
            title=meta["title"],
            source_url=f"synthetic://{self.instance_id}/{ref.source_doc_id}",
        )
        return raw, self._acl(meta)

    async def list_acls(self, ref: DocRef) -> AccessControl:
        meta, _ = parse_document(self._read(ref.source_doc_id))
        return self._acl(meta)

    async def on_webhook(self, event: dict[str, Any]) -> list[ChangeEvent]:
        return []

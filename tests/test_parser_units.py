"""Docling parser fast path: markdown sectioning."""

from __future__ import annotations

from pansoph_ingestion.adapters_parser import DoclingParser
from pansoph_types import RawDoc


def _raw(text: str | None = None, content: bytes | None = None) -> RawDoc:
    return RawDoc(
        source_doc_id="d", instance_id="i", mime="text/markdown", text=text, content=content
    )


async def test_splits_on_headings_keeping_body() -> None:
    p = DoclingParser()
    parsed = await p.parse(_raw("# Title\nbody line\n## Sub\nmore text"))
    assert [s.heading for s in parsed.sections] == ["Title", "Sub"]
    assert "body line" in parsed.sections[0].text
    assert "more text" in parsed.sections[1].text


async def test_content_before_first_heading_is_its_own_section() -> None:
    p = DoclingParser()
    parsed = await p.parse(_raw("intro paragraph\n# Heading\nbody"))
    assert parsed.sections[0].heading is None
    assert "intro paragraph" in parsed.sections[0].text
    assert parsed.sections[1].heading == "Heading"


async def test_no_headings_is_a_single_section() -> None:
    p = DoclingParser()
    parsed = await p.parse(_raw("just one block of text\nwith two lines"))
    assert len(parsed.sections) == 1
    assert parsed.sections[0].heading is None


async def test_binary_without_content_yields_no_sections() -> None:
    p = DoclingParser()
    parsed = await p.parse(_raw(text=None, content=None))
    assert parsed.sections == []

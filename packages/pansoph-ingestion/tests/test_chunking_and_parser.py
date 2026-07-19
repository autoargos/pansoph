"""Regression tests for the ingestion bug where a whole web page became one giant chunk.

An unparsed HTML page was stored as a single 400k-character chunk of raw markup, which
wrecked retrieval and flooded the answer prompt so the model produced erratic output.
These tests pin the two fixes: the parser strips HTML to text, and the chunker splits any
section larger than the target. No em dashes anywhere.
"""

from __future__ import annotations

import asyncio

from pansoph_ingestion.adapters_parser import DoclingParser, _html_to_text, _looks_like_html
from pansoph_ingestion.chunking import chunk_sections
from pansoph_types import RawDoc, Section


def test_chunk_splits_oversized_section() -> None:
    # One section far larger than the target must become several bounded chunks, not one.
    big = Section(text=" ".join(["word"] * 3000))
    chunks = chunk_sections([big], target_tokens=512)
    assert len(chunks) >= 5
    assert all(len(c.split()) <= 512 for c in chunks)


def test_chunk_groups_small_sections() -> None:
    sections = [Section(text="alpha beta"), Section(text="gamma delta")]
    chunks = chunk_sections(sections, target_tokens=512)
    assert chunks == ["alpha beta\n\ngamma delta"]


def test_chunk_skips_empty_sections() -> None:
    chunks = chunk_sections([Section(text="   "), Section(text="real content")])
    assert chunks == ["real content"]


def test_html_to_text_drops_script_and_style() -> None:
    html = (
        "<html><head><style>.a{color:red}</style>"
        "<script>alert('x')</script></head>"
        "<body><h1>Title</h1><p>Hello world.</p></body></html>"
    )
    text = _html_to_text(html)
    assert "Hello world." in text
    assert "Title" in text
    assert "alert(" not in text
    assert "color:red" not in text
    assert "<" not in text


def test_looks_like_html_detects_pages() -> None:
    assert _looks_like_html(RawDoc(source_doc_id="u", instance_id="i", mime="text/html", text="x"))
    assert _looks_like_html(
        RawDoc(source_doc_id="u", instance_id="i", mime="", text="<!DOCTYPE html><html>")
    )
    assert not _looks_like_html(
        RawDoc(source_doc_id="u", instance_id="i", mime="text/plain", text="just text")
    )


def test_parser_html_page_yields_bounded_clean_chunks() -> None:
    html = (
        "<!DOCTYPE html><html><head><script>junk()</script></head><body>"
        "<h1>Wiki</h1><p>A wiki is a website.</p><p>" + ("word " * 3000) + "</p></body></html>"
    )
    raw = RawDoc(source_doc_id="u", instance_id="i", mime="text/html", text=html, title="Wiki")
    parsed = asyncio.run(DoclingParser().parse(raw))
    chunks = chunk_sections(list(parsed.sections))
    assert len(chunks) >= 5
    assert max(len(c) for c in chunks) < 4000
    joined = "\n".join(chunks)
    assert "junk()" not in joined
    assert "A wiki is a website." in joined


def test_parser_strips_nul_and_control_bytes() -> None:
    # A file read as text can carry NUL (0x00) and other C0 control bytes. Postgres text
    # columns reject NUL outright, so the parser must strip it (and its control siblings)
    # while keeping tabs and newlines, or one odd file aborts the whole sync.
    raw = RawDoc(
        source_doc_id="u",
        instance_id="i",
        mime="text/plain",
        text="clean\x00text\x07here\tand\nnewline",
        title="t",
    )
    parsed = asyncio.run(DoclingParser().parse(raw))
    joined = "\n".join(s.text for s in parsed.sections)
    assert "\x00" not in joined and "\x07" not in joined
    assert "cleantexthere" in joined  # NUL and BEL removed, characters around them kept
    assert "\t" in joined and "newline" in joined  # tab and newline preserved

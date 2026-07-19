"""Parser adapter: Docling, with a fast path for already-textual documents.

Many connectors (GitHub, public link, MCP) already deliver text, so the fast path
turns that straight into a single section without invoking Docling. Binary
documents (PDF, Office) go through Docling, which preserves layout and section
hierarchy. Docling is an optional dependency (install pansoph-ingestion[docling]).
"""

from __future__ import annotations

import io
import re
from html.parser import HTMLParser

from pansoph_types import RawDoc, Section

from .ports import ParsedDoc


# C0 control characters, except tab/newline/carriage-return, are stripped from all parsed
# text. NUL (0x00) in particular is never meaningful document content (it shows up when a
# binary or mis-decoded file is read as text), and a Postgres text column rejects it
# outright ("text fields cannot contain NUL (0x00) bytes"), which would abort the whole
# sync over one odd file. Removing the control range keeps such a file from poisoning the
# run while leaving normal whitespace intact.
_CONTROL_STRIP = {c: None for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)}
_CONTROL_STRIP[0x7F] = None


def _sanitize(text: str) -> str:
    return text.translate(_CONTROL_STRIP)


class DoclingParser:
    async def parse(self, raw: RawDoc) -> ParsedDoc:
        # Fast path: the connector already gave us text.
        if raw.text is not None:
            text = raw.text
            # HTML is not "already textual": a raw web page is mostly markup, script, and
            # style. Left as is it becomes one enormous chunk of tags that poisons both
            # retrieval and the answer prompt. Strip it to readable text first.
            if _looks_like_html(raw):
                text = _html_to_text(text)
            sections = _split_markdown(_sanitize(text))
            return ParsedDoc(title=raw.title, lang=raw.lang, sections=sections)

        if raw.content is None:
            return ParsedDoc(title=raw.title, lang=raw.lang, sections=[])

        # Binary path: parse with Docling and export to markdown, then section it. Docling
        # is a heavy optional dependency (install pansoph-ingestion[docling]); when it is not
        # present, skip the binary document gracefully (an empty parse, no sections) rather
        # than aborting the whole sync, so a single PDF does not stop a Dropbox or SharePoint
        # run from indexing every text file around it.
        try:
            from docling.datamodel.base_models import DocumentStream
            from docling.document_converter import DocumentConverter
        except ImportError:
            return ParsedDoc(title=raw.title, lang=raw.lang, sections=[])

        stream = DocumentStream(name=raw.source_doc_id, stream=io.BytesIO(raw.content))
        result = DocumentConverter().convert(stream)
        markdown = result.document.export_to_markdown()
        return ParsedDoc(
            title=raw.title, lang=raw.lang, sections=_split_markdown(_sanitize(markdown))
        )


def _split_markdown(text: str) -> list[Section]:
    """Split markdown into sections at headings, keeping each heading with its body.

    A document with no headings becomes a single section. This is layout aware in
    the cheap sense: it respects the author's structure.
    """
    sections: list[Section] = []
    heading: str | None = None
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if body or heading:
            sections.append(Section(text=body, heading=heading))

    for line in text.splitlines():
        if line.startswith("#"):
            flush()
            heading = line.lstrip("#").strip()
            buf = []
        else:
            buf.append(line)
    flush()
    return sections or [Section(text=text.strip())]


def _looks_like_html(raw: RawDoc) -> bool:
    """True when the connector handed us HTML rather than plain text or markdown."""
    mime = (getattr(raw, "mime", "") or "").lower()
    if "html" in mime:
        return True
    head = (raw.text or "")[:512].lstrip().lower()
    return head.startswith(("<!doctype html", "<html")) or ("<body" in head and "<" in head)


# Elements whose text content is never document content (scripts, styling, metadata).
_DROP_TAGS = frozenset(
    {"script", "style", "noscript", "head", "svg", "template", "iframe", "canvas"}
)
# Block-level tags: emit a newline so words from adjacent blocks do not run together.
_BLOCK_TAGS = frozenset(
    {"p", "div", "br", "li", "tr", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}
)


class _TextExtractor(HTMLParser):
    """Pull readable text out of an HTML document, dropping script/style/head noise.

    Stdlib only (no new dependency): good enough to turn a fetched web page into clean
    text for chunking and embedding. Not a full readability extractor, but it removes the
    markup and non-content that was flooding the model.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._drop_depth = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in _DROP_TAGS:
            self._drop_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_TAGS and self._drop_depth > 0:
            self._drop_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._drop_depth == 0:
            piece = data.strip()
            if piece:
                self.parts.append(piece + " ")


def _html_to_text(html: str) -> str:
    """Strip HTML to readable text, collapsing runs of whitespace."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        text = "".join(parser.parts)
    except Exception:
        # Never let a malformed page break ingestion: fall back to a crude tag strip.
        text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()

"""Parser adapter: Docling, with a fast path for already-textual documents.

Many connectors (GitHub, public link, MCP) already deliver text, so the fast path
turns that straight into a single section without invoking Docling. Binary
documents (PDF, Office) go through Docling, which preserves layout and section
hierarchy. Docling is an optional dependency (install pansoph-ingestion[docling]).
"""

from __future__ import annotations

import io

from pansoph_types import RawDoc, Section

from .ports import ParsedDoc


class DoclingParser:
    async def parse(self, raw: RawDoc) -> ParsedDoc:
        # Fast path: the connector already gave us text.
        if raw.text is not None:
            sections = _split_markdown(raw.text)
            return ParsedDoc(title=raw.title, lang=raw.lang, sections=sections)

        if raw.content is None:
            return ParsedDoc(title=raw.title, lang=raw.lang, sections=[])

        # Binary path: parse with Docling and export to markdown, then section it.
        from docling.datamodel.base_models import DocumentStream
        from docling.document_converter import DocumentConverter

        stream = DocumentStream(name=raw.source_doc_id, stream=io.BytesIO(raw.content))
        result = DocumentConverter().convert(stream)
        markdown = result.document.export_to_markdown()
        return ParsedDoc(title=raw.title, lang=raw.lang, sections=_split_markdown(markdown))


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

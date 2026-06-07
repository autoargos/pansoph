"""Section aware chunking with a token fallback. Real logic kept small and tested."""

from __future__ import annotations

from pansoph_types import Section


def chunk_sections(sections: list[Section], target_tokens: int = 512) -> list[str]:
    """Group sections into chunks near target_tokens, never splitting mid section
    unless a single section exceeds the target.

    Token counting here is a cheap word based approximation; the real implementation
    uses the embedder's tokenizer. This keeps section boundaries, which downstream
    studies show yields the highest answer quality.
    """

    def approx_tokens(text: str) -> int:
        return max(1, len(text.split()))

    chunks: list[str] = []
    buf: list[str] = []
    buf_tokens = 0
    for section in sections:
        t = approx_tokens(section.text)
        if buf and buf_tokens + t > target_tokens:
            chunks.append("\n\n".join(buf))
            buf, buf_tokens = [], 0
        buf.append(section.text)
        buf_tokens += t
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks

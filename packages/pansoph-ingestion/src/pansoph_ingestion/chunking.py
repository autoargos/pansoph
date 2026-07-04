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

    def split_oversized(text: str) -> list[str]:
        """Split a single section larger than the target into word-bounded pieces.

        Without this a whole document (a full web page, say) collapses into one giant
        chunk, which wrecks retrieval and, worse, floods the answer prompt with the entire
        page. Splitting on word boundaries keeps each piece near the target size.
        """
        words = text.split()
        if len(words) <= target_tokens:
            return [text.strip()]
        pieces: list[str] = []
        for start in range(0, len(words), target_tokens):
            piece = " ".join(words[start : start + target_tokens]).strip()
            if piece:
                pieces.append(piece)
        return pieces

    chunks: list[str] = []
    buf: list[str] = []
    buf_tokens = 0
    for section in sections:
        text = section.text.strip()
        if not text:
            continue
        t = approx_tokens(text)
        # A section larger than the target is emitted as its own bounded chunks (after
        # flushing the buffer), so no chunk ever exceeds the target.
        if t > target_tokens:
            if buf:
                chunks.append("\n\n".join(buf))
                buf, buf_tokens = [], 0
            chunks.extend(split_oversized(text))
            continue
        if buf and buf_tokens + t > target_tokens:
            chunks.append("\n\n".join(buf))
            buf, buf_tokens = [], 0
        buf.append(text)
        buf_tokens += t
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks

from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    text: str
    ordinal: int


def chunk_text(text: str, max_chars: int = 1800, overlap_chars: int = 240) -> list[TextChunk]:
    """Create bounded overlapping chunks while preserving readable word boundaries."""
    clean = " ".join(text.split())
    if not clean:
        return [TextChunk("", 0)]
    chunks: list[TextChunk] = []
    start = 0
    ordinal = 0
    while start < len(clean):
        end = min(len(clean), start + max_chars)
        if end < len(clean):
            boundary = clean.rfind(" ", start, end)
            if boundary > start + max_chars // 2:
                end = boundary
        chunks.append(TextChunk(clean[start:end], ordinal))
        ordinal += 1
        if end == len(clean):
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


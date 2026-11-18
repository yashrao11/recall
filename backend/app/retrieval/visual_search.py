from __future__ import annotations

import sqlite3

from app.ai.providers import EmbeddingProvider, ProviderUnavailable
from app.config import Settings
from app.retrieval.semantic_search import vector_search


def visual_search(
    connection: sqlite3.Connection,
    settings: Settings,
    provider: EmbeddingProvider,
    query: str,
    top_k: int = 50,
) -> list[dict]:
    """Search visual-unit vectors with a text query in a shared embedding space."""
    if not getattr(provider, "supports_visual", False):
        raise ProviderUnavailable(
            f"Visual retrieval is unavailable: embedding provider '{provider.name}' is text-only"
        )
    return vector_search(connection, settings, provider, query, "visual", top_k)

from __future__ import annotations

import hashlib
import sqlite3

from app.ai.providers import EmbeddingProvider
from app.config import Settings
from app.db.database import get_connection
from app.retrieval.fts_search import _result
from app.retrieval.vector_store import FaissVectorStore


def vector_search(
    connection: sqlite3.Connection,
    settings: Settings,
    provider: EmbeddingProvider,
    query: str,
    mode: str,
    top_k: int = 50,
) -> list[dict]:
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
    embed_query = getattr(provider, "embed_query", None)
    vector = (
        embed_query(query, query_hash, connection)
        if callable(embed_query)
        else provider.embed_text(query, query_hash, connection)
    )
    index_name = "visual.index" if mode == "visual" else "text.index"
    store = FaissVectorStore(settings.data_dir / index_name, provider.dimension)
    ranked = store.search(vector, top_k)
    if not ranked:
        return []
    ids = [unit_id for unit_id, _ in ranked]
    placeholders = ",".join("?" for _ in ids)
    rows = connection.execute(
        "SELECT u.unit_id, u.source_id, u.page_or_slide, u.short_comment, u.tags_json, "
        "s.title, s.source_type, s.original_path, u.thumbnail_path "
        f"FROM document_units u JOIN sources s ON s.source_id = u.source_id "
        f"WHERE s.indexing_status IN ('done', 'partial') AND s.excluded = 0 "
        f"AND u.unit_id IN ({placeholders})",
        ids,
    ).fetchall()
    by_id = {int(row["unit_id"]): row for row in rows}
    results = []
    for unit_id, score in ranked:
        row = by_id.get(unit_id)
        if row is not None:
            results.append(_result(dict(row), mode, score))
    return results


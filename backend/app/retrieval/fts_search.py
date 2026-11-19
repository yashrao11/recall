from __future__ import annotations

import re
import sqlite3
from pathlib import Path


def sanitise_fts_query(query: str) -> str:
    """Convert user text into a safe FTS5 AND query; operators cannot escape."""
    phrases = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', query)
    plain = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', " ", query)
    tokens = _query_tokens(plain)
    parts = [f'"{token.replace(chr(34), "")}"' for token in tokens]
    parts.extend(f'"{phrase.replace(chr(34), " ").strip()}"' for phrase in phrases if phrase.strip())
    return " AND ".join(part for part in parts if part != '""')


def _query_tokens(query: str) -> list[str]:
    operator_words = {"and", "or", "not", "near"}
    query_stopwords = {
        "a", "an", "are", "be", "does", "do", "for", "how", "i", "in", "is", "it",
        "me", "my", "of", "on", "please", "tell", "that", "the", "this", "to", "was",
        "what", "which", "who", "why", "with",
    }
    return [
        token for token in re.findall(r"[A-Za-z0-9_]+", query)
        if token.lower() not in operator_words and token.lower() not in query_stopwords
    ]


def keyword_search(
    connection: sqlite3.Connection,
    query: str,
    limit: int = 50,
    source_type: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict]:
    match_query = sanitise_fts_query(query)
    if not match_query:
        return []
    metadata_tokens = _query_tokens(query)
    source_clauses = ["s.indexing_status IN ('done', 'partial')", "s.excluded = 0"]
    source_params: list[object] = []
    if source_type:
        source_clauses.append("s.source_type = ?")
        source_params.append(source_type)
    if from_date:
        source_clauses.append("s.imported_at >= ?")
        source_params.append(from_date)
    if to_date:
        source_clauses.append("s.imported_at < ?")
        source_params.append(to_date)

    rows = connection.execute(
        "SELECT u.unit_id, u.source_id, u.page_or_slide, u.short_comment, u.tags_json, "
        "s.title, s.source_type, s.original_path, s.capture_url, u.thumbnail_path, "
        "snippet(fts_units, 2, '<mark>', '</mark>', '…', 18) AS snippet, "
        "bm25(fts_units, 1.0, 1.0, 0.4, 0.3) AS raw_score "
        "FROM fts_units JOIN document_units u ON u.unit_id = fts_units.unit_id "
        "JOIN sources s ON s.source_id = u.source_id "
        f"WHERE fts_units MATCH ? AND {' AND '.join(source_clauses)} ORDER BY raw_score LIMIT ?",
        [match_query, *source_params, limit],
    ).fetchall()
    results = [_result(dict(row), "keyword", _normalise_bm25(row["raw_score"])) for row in rows]
    seen_units = {int(result["unit_id"]) for result in results}
    if metadata_tokens and len(results) < limit:
        metadata_clauses = [
            "(" + " OR ".join(
                ["LOWER(s.title) LIKE ?", "LOWER(s.original_path) LIKE ?"] * len(metadata_tokens)
            ) + ")",
            *source_clauses,
        ]
        metadata_params: list[object] = []
        for token in metadata_tokens:
            metadata_params.extend([f"%{token.lower()}%", f"%{token.lower()}%"])
        metadata_rows = connection.execute(
            "SELECT u.unit_id, u.source_id, u.page_or_slide, u.short_comment, u.tags_json, "
            "s.title, s.source_type, s.original_path, s.capture_url, u.thumbnail_path, "
            "'' AS snippet, 0.0 AS raw_score "
            "FROM document_units u JOIN sources s ON s.source_id = u.source_id "
            f"WHERE {' AND '.join(metadata_clauses)} ORDER BY s.imported_at DESC, u.unit_id LIMIT ?",
            [*metadata_params, *source_params, limit],
        ).fetchall()
        for row in metadata_rows:
            if int(row["unit_id"]) not in seen_units:
                results.append(_result(dict(row), "filename", 1.0))
                seen_units.add(int(row["unit_id"]))
                if len(results) >= limit:
                    break
    return results[:limit]


def _normalise_bm25(value: float) -> float:
    return 1.0 / (1.0 + max(float(value), 0.0))


def _result(row: dict, match_type: str, score: float) -> dict:
    import json

    return {
        "unit_id": row["unit_id"],
        "source_id": row["source_id"],
        "title": row["title"],
        "source_type": row["source_type"],
        "filename": Path(row["original_path"]).name if row.get("original_path") else None,
        "capture_url": row.get("capture_url"),
        "page": row["page_or_slide"],
        "snippet": row.get("snippet") or row.get("short_comment", ""),
        "short_comment": row.get("short_comment", ""),
        "tags": json.loads(row.get("tags_json", "[]")),
        "thumbnail_url": f"/units/{row['unit_id']}/thumbnail" if row.get("thumbnail_path") else None,
        "match_type": [match_type],
        "visual_match": match_type == "visual",
        "score": float(score),
    }


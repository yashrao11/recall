from __future__ import annotations

import re
import sqlite3


def sanitise_fts_query(query: str) -> str:
    """Convert user text into a safe FTS5 AND query; operators cannot escape."""
    phrases = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', query)
    plain = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', " ", query)
    operator_words = {"and", "or", "not", "near"}
    tokens = [
        token for token in re.findall(r"[A-Za-z0-9_]+", plain)
        if token.lower() not in operator_words
    ]
    parts = [f'"{token.replace(chr(34), "")}"' for token in tokens]
    parts.extend(f'"{phrase.replace(chr(34), " ").strip()}"' for phrase in phrases if phrase.strip())
    return " AND ".join(part for part in parts if part != '""')


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
    clauses = ["fts_units MATCH ?"]
    params: list[object] = [match_query]
    if source_type:
        clauses.append("s.source_type = ?")
        params.append(source_type)
    if from_date:
        clauses.append("s.imported_at >= ?")
        params.append(from_date)
    if to_date:
        clauses.append("s.imported_at < ?")
        params.append(to_date)
    params.append(limit)
    rows = connection.execute(
        "SELECT u.unit_id, u.source_id, u.page_or_slide, u.short_comment, u.tags_json, "
        "s.title, s.source_type, s.original_path, u.thumbnail_path, "
        "snippet(fts_units, 2, '<mark>', '</mark>', '…', 18) AS snippet, "
        "bm25(fts_units, 1.0, 1.0, 0.4, 0.3) AS raw_score "
        "FROM fts_units JOIN document_units u ON u.unit_id = fts_units.unit_id "
        "JOIN sources s ON s.source_id = u.source_id "
        f"WHERE {' AND '.join(clauses)} ORDER BY raw_score LIMIT ?",
        params,
    ).fetchall()
    return [_result(dict(row), "keyword", _normalise_bm25(row["raw_score"])) for row in rows]


def _normalise_bm25(value: float) -> float:
    return 1.0 / (1.0 + max(float(value), 0.0))


def _result(row: dict, match_type: str, score: float) -> dict:
    import json

    return {
        "unit_id": row["unit_id"],
        "source_id": row["source_id"],
        "title": row["title"],
        "source_type": row["source_type"],
        "page": row["page_or_slide"],
        "snippet": row.get("snippet") or row.get("short_comment", ""),
        "short_comment": row.get("short_comment", ""),
        "tags": json.loads(row.get("tags_json", "[]")),
        "thumbnail_url": f"/units/{row['unit_id']}/thumbnail" if row.get("thumbnail_path") else None,
        "match_type": [match_type],
        "score": float(score),
    }


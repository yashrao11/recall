from __future__ import annotations

from pathlib import Path
import json
import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.db.database import get_connection
from app.db.database import _normalize_entity_title


router = APIRouter(prefix="/connections", tags=["connections"])
MAX_LIMIT = 100
_VISIBLE_STATUSES = {"unreviewed", "confirmed"}
_REVIEW_STATUSES = {"confirmed", "rejected", "unsupported"}


class RelationshipCreateRequest(BaseModel):
    from_object_id: int = Field(ge=1)
    to_object_id: int = Field(ge=1)
    relationship_type: str = Field(min_length=1, max_length=100)


class RelationshipReviewRequest(BaseModel):
    reviewed_status: Literal["confirmed", "rejected", "unsupported"]


@router.get("/entities")
def list_entities(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    type: str | None = Query(default=None, max_length=50),
    status_filter: str | None = Query(default=None, alias="status", max_length=20),
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> dict:
    if status_filter is not None and status_filter not in {"active", "inactive", "needs-review"}:
        raise HTTPException(status_code=422, detail="invalid entity status")
    normalized_query = _normalize_entity_title(q) if q else ""
    where = []
    params: list[object] = []
    if status_filter is None:
        where.append("o.status = 'active'")
    else:
        where.append("o.status = ?")
        params.append(status_filter)
    if type:
        where.append("o.object_type = ?")
        params.append(type)
    if normalized_query:
        where.append("(o.normalized_title LIKE ? OR LOWER(o.aliases_json) LIKE ?)")
        pattern = f"%{normalized_query.lower()}%"
        params.extend([pattern, pattern])
    clause = " AND ".join(where)
    with get_connection(request.app.state.settings.database_path) as connection:
        total = connection.execute(
            f"SELECT COUNT(*) FROM objects o WHERE {clause}", params
        ).fetchone()[0]
        rows = connection.execute(
            "SELECT o.*, "
            "(SELECT COUNT(*) FROM object_evidence e WHERE e.object_id = o.object_id) AS evidence_count, "
            "(SELECT COUNT(*) FROM relationships r WHERE (r.from_object_id = o.object_id OR r.to_object_id = o.object_id) "
            "AND r.reviewed_status NOT IN ('rejected', 'unsupported')) AS relationship_count "
            f"FROM objects o WHERE {clause} ORDER BY o.object_type, o.normalized_title, o.object_id "
            "LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return {
        "entities": [_entity_summary(row) for row in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(rows) < total,
    }


@router.get("/sources/{source_id}")
def source_connections(
    source_id: int,
    request: Request,
    include_unsupported: bool = False,
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
) -> dict:
    with get_connection(request.app.state.settings.database_path) as connection:
        source = connection.execute(
            "SELECT source_id, title, source_type, original_path, indexing_status "
            "FROM sources WHERE source_id = ?", (source_id,)
        ).fetchone()
        if source is None:
            raise HTTPException(status_code=404, detail="source not found")
        entity_total = connection.execute(
            "SELECT COUNT(DISTINCT e.object_id) FROM object_evidence e "
            "JOIN objects o ON o.object_id = e.object_id "
            "WHERE e.source_id = ? AND o.status <> 'inactive'", (source_id,)
        ).fetchone()[0]
        entity_rows = connection.execute(
            "SELECT o.*, e.evidence_id, e.unit_id, e.discovery_method AS evidence_method, "
            "e.confidence AS evidence_confidence, u.page_or_slide, u.extracted_text, "
            "u.short_comment "
            "FROM object_evidence e JOIN objects o ON o.object_id = e.object_id "
            "LEFT JOIN document_units u ON u.unit_id = e.unit_id "
            "WHERE e.source_id = ? AND o.status <> 'inactive' "
            "ORDER BY o.object_type, o.normalized_title, e.unit_id LIMIT ? OFFSET ?",
            (source_id, limit, offset),
        ).fetchall()
        relationship_clause = "" if include_unsupported else (
            "AND r.reviewed_status NOT IN ('rejected', 'unsupported')"
        )
        relationship_total = connection.execute(
            "SELECT COUNT(DISTINCT r.relationship_id) FROM relationship_evidence e "
            "JOIN relationships r ON r.relationship_id = e.relationship_id "
            f"WHERE e.source_id = ? {relationship_clause}", (source_id,)
        ).fetchone()[0]
        relationship_rows = connection.execute(
            "SELECT r.*, f.title AS from_title, f.object_type AS from_type, "
            "t.title AS to_title, t.object_type AS to_type, e.evidence_id, e.source_id, e.unit_id, "
            "e.discovery_method AS evidence_method, e.confidence AS evidence_confidence, "
            "u.page_or_slide, u.extracted_text, u.short_comment "
            "FROM relationship_evidence e JOIN relationships r ON r.relationship_id = e.relationship_id "
            "JOIN objects f ON f.object_id = r.from_object_id JOIN objects t ON t.object_id = r.to_object_id "
            "LEFT JOIN document_units u ON u.unit_id = e.unit_id "
            f"WHERE e.source_id = ? {relationship_clause} "
            "ORDER BY r.relationship_id, e.unit_id LIMIT ? OFFSET ?",
            (source_id, limit, offset),
        ).fetchall()
    entities = _group_source_entities(entity_rows)
    relationships = _group_relationship_rows(relationship_rows)
    return {
        "source": {
            "source_id": source["source_id"],
            "title": source["title"],
            "file_type": source["source_type"],
            "filename": Path(source["original_path"]).name,
            "indexing_status": source["indexing_status"],
        },
        "entities": entities,
        "relationships": relationships,
        "entities_total": int(entity_total),
        "relationships_total": int(relationship_total),
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(entities) < entity_total or offset + len(relationships) < relationship_total,
    }


@router.get("/entities/{object_id}")
def entity_detail(
    object_id: int,
    request: Request,
    include_unsupported: bool = False,
) -> dict:
    with get_connection(request.app.state.settings.database_path) as connection:
        entity = connection.execute(
            "SELECT o.*, "
            "(SELECT COUNT(*) FROM object_evidence e WHERE e.object_id = o.object_id) AS evidence_count, "
            "(SELECT COUNT(*) FROM relationships r WHERE (r.from_object_id = o.object_id OR r.to_object_id = o.object_id) "
            "AND r.reviewed_status NOT IN ('rejected', 'unsupported')) AS relationship_count "
            "FROM objects o WHERE o.object_id = ?", (object_id,)
        ).fetchone()
        if entity is None:
            raise HTTPException(status_code=404, detail="entity not found")
        evidence_rows = connection.execute(
            "SELECT e.*, s.title AS source_title, s.source_type, u.page_or_slide, "
            "u.extracted_text, u.short_comment "
            "FROM object_evidence e JOIN sources s ON s.source_id = e.source_id "
            "LEFT JOIN document_units u ON u.unit_id = e.unit_id "
            "WHERE e.object_id = ? ORDER BY e.source_id, e.unit_id", (object_id,)
        ).fetchall()
        relation_clause = "" if include_unsupported else (
            "AND r.reviewed_status NOT IN ('rejected', 'unsupported')"
        )
        relation_rows = connection.execute(
            "SELECT r.*, f.title AS from_title, f.object_type AS from_type, "
            "t.title AS to_title, t.object_type AS to_type "
            "FROM relationships r JOIN objects f ON f.object_id = r.from_object_id "
            "JOIN objects t ON t.object_id = r.to_object_id "
            f"WHERE (r.from_object_id = ? OR r.to_object_id = ?) {relation_clause} "
            "ORDER BY r.relationship_id",
            (object_id, object_id),
        ).fetchall()
        relation_ids = [int(row["relationship_id"]) for row in relation_rows]
        relation_evidence = _relationship_evidence(connection, relation_ids)
    relationships = []
    for row in relation_rows:
        payload = _relationship_payload(row, relation_evidence.get(int(row["relationship_id"]), []))
        if int(row["from_object_id"]) == object_id and int(row["to_object_id"]) == object_id:
            payload["direction"] = "self"
        elif int(row["is_symmetric"]) == 1:
            payload["direction"] = "symmetric"
        elif int(row["from_object_id"]) == object_id:
            payload["direction"] = "outgoing"
        else:
            payload["direction"] = "incoming"
        relationships.append(payload)
    return {
        "entity": _entity_summary(entity),
        "evidence": [_object_evidence_payload(row) for row in evidence_rows],
        "relationships": relationships,
    }


@router.post("/relationships", status_code=status.HTTP_201_CREATED)
def create_relationship(payload: RelationshipCreateRequest, request: Request) -> dict:
    if payload.from_object_id == payload.to_object_id:
        raise HTTPException(status_code=422, detail="self-relationships are not allowed")
    with get_connection(request.app.state.settings.database_path) as connection:
        endpoints = connection.execute(
            "SELECT object_id FROM objects WHERE object_id IN (?, ?)",
            (payload.from_object_id, payload.to_object_id),
        ).fetchall()
        if len(endpoints) != 2:
            raise HTTPException(status_code=404, detail="one or both entities not found")
        relation_type = connection.execute(
            "SELECT name, is_symmetric FROM relationship_types "
            "WHERE name = ? ORDER BY is_legacy LIMIT 1",
            (payload.relationship_type,),
        ).fetchone()
        if relation_type is None:
            raise HTTPException(status_code=422, detail="unknown relationship type")
        is_symmetric = int(relation_type["is_symmetric"])
        identity_a = min(payload.from_object_id, payload.to_object_id) if is_symmetric else payload.from_object_id
        identity_b = max(payload.from_object_id, payload.to_object_id) if is_symmetric else payload.to_object_id
        try:
            cursor = connection.execute(
                "INSERT INTO relationships "
                "(from_object_id, to_object_id, relationship_type, is_symmetric, identity_a, identity_b, "
                "confidence, created_by, discovery_method, reviewed_status, explanation) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, 'user', 'manual', 'confirmed', NULL)",
                (payload.from_object_id, payload.to_object_id, payload.relationship_type,
                 is_symmetric, identity_a, identity_b),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="relationship already exists or is invalid") from exc
        row = connection.execute(
            "SELECT r.*, f.title AS from_title, f.object_type AS from_type, "
            "t.title AS to_title, t.object_type AS to_type "
            "FROM relationships r JOIN objects f ON f.object_id = r.from_object_id "
            "JOIN objects t ON t.object_id = r.to_object_id WHERE r.relationship_id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return _relationship_payload(row, [])


@router.patch("/relationships/{relationship_id}")
def review_relationship(
    relationship_id: int,
    payload: RelationshipReviewRequest,
    request: Request,
) -> dict:
    with get_connection(request.app.state.settings.database_path) as connection:
        row = connection.execute(
            "SELECT r.*, f.title AS from_title, f.object_type AS from_type, "
            "t.title AS to_title, t.object_type AS to_type "
            "FROM relationships r JOIN objects f ON f.object_id = r.from_object_id "
            "JOIN objects t ON t.object_id = r.to_object_id WHERE r.relationship_id = ?",
            (relationship_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="relationship not found")
        if row["created_by"] == "user":
            raise HTTPException(status_code=409, detail="user-created relationships do not require review")
        if row["reviewed_status"] != "unreviewed":
            raise HTTPException(status_code=409, detail="relationship has already been reviewed")
        connection.execute(
            "UPDATE relationships SET reviewed_status = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE relationship_id = ?", (payload.reviewed_status, relationship_id)
        )
        updated = dict(row)
        updated["reviewed_status"] = payload.reviewed_status
        evidence = _relationship_evidence(connection, [relationship_id]).get(relationship_id, [])
    return _relationship_payload(updated, evidence)


@router.delete("/relationships/{relationship_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_relationship(relationship_id: int, request: Request) -> None:
    with get_connection(request.app.state.settings.database_path) as connection:
        row = connection.execute(
            "SELECT created_by FROM relationships WHERE relationship_id = ?", (relationship_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="relationship not found")
        if row["created_by"] != "user":
            raise HTTPException(
                status_code=409,
                detail="automatic relationships must be rejected or marked unsupported",
            )
        connection.execute("DELETE FROM relationships WHERE relationship_id = ?", (relationship_id,))


def _entity_summary(row: sqlite3.Row | dict) -> dict:
    try:
        aliases = json.loads(row["aliases_json"] or "[]")
    except (TypeError, json.JSONDecodeError):
        aliases = []
    return {
        "object_id": row["object_id"],
        "title": row["title"],
        "object_type": row["object_type"],
        "description": row["description"],
        "aliases": aliases if isinstance(aliases, list) else [],
        "created_by": row["created_by"],
        "discovery_method": row["discovery_method"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "evidence_count": int(row["evidence_count"]) if "evidence_count" in row.keys() else None,
        "relationship_count": int(row["relationship_count"]) if "relationship_count" in row.keys() else None,
    }


def _object_evidence_payload(row: sqlite3.Row | dict) -> dict:
    return {
        "evidence_id": row["evidence_id"],
        "source_id": row["source_id"],
        "source_title": row["source_title"],
        "file_type": row["source_type"],
        "unit_id": row["unit_id"],
        "page": row["page_or_slide"],
        "excerpt": _excerpt(row["extracted_text"] or row["short_comment"] or ""),
        "discovery_method": row["discovery_method"],
        "confidence": row["confidence"],
        "supported": True,
    }


def _group_source_entities(rows: list[sqlite3.Row]) -> list[dict]:
    grouped: dict[int, dict] = {}
    for row in rows:
        item = grouped.setdefault(int(row["object_id"]), {
            **_entity_summary(row),
            "evidence": [],
        })
        item["evidence"].append({
            "evidence_id": row["evidence_id"],
            "unit_id": row["unit_id"],
            "page": row["page_or_slide"],
            "excerpt": _excerpt(row["extracted_text"] or row["short_comment"] or ""),
            "discovery_method": row["evidence_method"],
            "confidence": row["evidence_confidence"],
            "supported": True,
        })
    return list(grouped.values())


def _relationship_evidence(connection: sqlite3.Connection, relationship_ids: list[int]) -> dict[int, list[dict]]:
    if not relationship_ids:
        return {}
    placeholders = ",".join("?" for _ in relationship_ids)
    rows = connection.execute(
        "SELECT e.*, s.title AS source_title, s.source_type, u.page_or_slide, "
        "u.extracted_text, u.short_comment FROM relationship_evidence e "
        "JOIN sources s ON s.source_id = e.source_id LEFT JOIN document_units u ON u.unit_id = e.unit_id "
        f"WHERE e.relationship_id IN ({placeholders}) ORDER BY e.relationship_id, e.unit_id",
        relationship_ids,
    ).fetchall()
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(int(row["relationship_id"]), []).append({
            "evidence_id": row["evidence_id"],
            "source_id": row["source_id"],
            "source_title": row["source_title"],
            "file_type": row["source_type"],
            "unit_id": row["unit_id"],
            "page": row["page_or_slide"],
            "excerpt": _excerpt(row["extracted_text"] or row["short_comment"] or ""),
            "discovery_method": row["discovery_method"],
            "confidence": row["confidence"],
            "supported": True,
        })
    return grouped


def _group_relationship_rows(rows: list[sqlite3.Row]) -> list[dict]:
    grouped: dict[int, dict] = {}
    for row in rows:
        relationship_id = int(row["relationship_id"])
        if relationship_id not in grouped:
            grouped[relationship_id] = _relationship_payload(row, [])
        grouped[relationship_id]["evidence"].append({
            "evidence_id": row["evidence_id"],
            "source_id": row["source_id"],
            "unit_id": row["unit_id"],
            "page": row["page_or_slide"],
            "excerpt": _excerpt(row["extracted_text"] or row["short_comment"] or ""),
            "discovery_method": row["evidence_method"],
            "confidence": row["evidence_confidence"],
            "supported": True,
        })
    return list(grouped.values())


def _relationship_payload(row: sqlite3.Row | dict, evidence: list[dict]) -> dict:
    status_value = row["reviewed_status"]
    return {
        "relationship_id": row["relationship_id"],
        "relationship_type": row["relationship_type"],
        "is_symmetric": bool(row["is_symmetric"]),
        "from_entity": {
            "object_id": row["from_object_id"],
            "title": row["from_title"],
            "object_type": row["from_type"],
        },
        "to_entity": {
            "object_id": row["to_object_id"],
            "title": row["to_title"],
            "object_type": row["to_type"],
        },
        "confidence": row["confidence"],
        "created_by": row["created_by"],
        "discovery_method": row["discovery_method"],
        "reviewed_status": status_value,
        "supported": status_value in _VISIBLE_STATUSES and (row["created_by"] == "user" or bool(evidence)),
        "explanation": row["explanation"],
        "evidence": evidence,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _excerpt(value: str, limit: int = 280) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[:limit - 1].rstrip() + "…"

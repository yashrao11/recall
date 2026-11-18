from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sqlite3
from pathlib import Path
from typing import Iterable

from app.db.database import _normalize_entity_title


_GENERIC_TERMS = {
    "about", "appendix", "chapter", "contents", "document", "draft", "example", "file",
    "figure", "image", "introduction", "lesson", "notes", "page", "report", "results",
    "section", "slide", "slides", "summary", "table", "test", "theory", "untitled",
}
_CONCEPT_PHRASES = (
    "active learning", "attention mechanism", "convolutional neural network", "deep learning",
    "knowledge graph", "machine learning", "neural network", "retrieval augmented generation",
    "system design", "transformer architecture", "vector database", "weighted graph",
)
_ORG_SUFFIXES = {"inc", "institute", "laboratory", "lab", "university", "company", "corporation"}


@dataclass(frozen=True)
class EntityCandidate:
    title: str
    object_type: str
    source_id: int
    unit_id: int | None
    discovery_method: str
    confidence: float | None = None


@dataclass(frozen=True)
class DiscoveryResult:
    source_id: int
    entities_created: int
    entities_reused: int
    entity_evidence_created: int
    relationships_created: int
    relationships_reused: int
    relationship_evidence_created: int

    def as_dict(self) -> dict[str, int]:
        return {
            "entities_created": self.entities_created,
            "entities_reused": self.entities_reused,
            "entity_evidence_created": self.entity_evidence_created,
            "relationships_created": self.relationships_created,
            "relationships_reused": self.relationships_reused,
            "relationship_evidence_created": self.relationship_evidence_created,
        }


def discover_source(connection: sqlite3.Connection, source_id: int) -> DiscoveryResult:
    """Discover local entities and evidence for one already-indexed source."""
    source = connection.execute(
        "SELECT source_id, title, original_path FROM sources WHERE source_id = ?", (source_id,)
    ).fetchone()
    if source is None:
        raise ValueError(f"source {source_id} does not exist")

    units = connection.execute(
        "SELECT unit_id, extracted_text, short_comment, tags_json FROM document_units "
        "WHERE source_id = ? ORDER BY unit_id", (source_id,)
    ).fetchall()
    candidates = list(_source_candidates(source))
    for unit in units:
        candidates.extend(_unit_candidates(source_id, unit))

    entities_created = entities_reused = evidence_created = 0
    unit_entities: dict[int, set[int]] = {}
    for candidate in _deduplicate_candidates(candidates):
        object_id, created = _resolve_entity(connection, candidate)
        if created:
            entities_created += 1
        else:
            entities_reused += 1
        if candidate.unit_id is not None:
            unit_entities.setdefault(candidate.unit_id, set()).add(object_id)
        cursor = connection.execute(
            "INSERT OR IGNORE INTO object_evidence "
            "(object_id, source_id, unit_id, discovery_method, confidence) "
            "VALUES (?, ?, ?, ?, ?)",
            (object_id, source_id, candidate.unit_id, candidate.discovery_method, candidate.confidence),
        )
        evidence_created += cursor.rowcount

    relationships_created = relationships_reused = relationship_evidence_created = 0
    for unit_id, object_ids in unit_entities.items():
        ordered_ids = sorted(object_ids)
        for index, from_id in enumerate(ordered_ids):
            for to_id in ordered_ids[index + 1:]:
                relationship_id, created = _ensure_related_relationship(
                    connection, from_id, to_id
                )
                if relationship_id is None:
                    continue
                if created:
                    relationships_created += 1
                else:
                    relationships_reused += 1
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO relationship_evidence "
                    "(relationship_id, source_id, unit_id, discovery_method, confidence) "
                    "VALUES (?, ?, ?, 'cooccurrence', NULL)",
                    (relationship_id, source_id, unit_id),
                )
                relationship_evidence_created += cursor.rowcount

    reconcile_automatic_knowledge(connection)
    return DiscoveryResult(
        source_id,
        entities_created,
        entities_reused,
        evidence_created,
        relationships_created,
        relationships_reused,
        relationship_evidence_created,
    )


def discover_source_by_database(database_path: Path, source_id: int) -> DiscoveryResult:
    """Run discovery for an existing indexed source in its own transaction."""
    from app.db.database import get_connection

    with get_connection(database_path) as connection:
        return discover_source(connection, source_id)


def discover_indexed_sources(
    database_path: Path, source_ids: Iterable[int] | None = None
) -> list[DiscoveryResult]:
    """Backfill selected indexed sources; never runs automatically at startup."""
    from app.db.database import get_connection

    with get_connection(database_path) as connection:
        ids = list(source_ids) if source_ids is not None else [
            int(row["source_id"])
            for row in connection.execute(
                "SELECT source_id FROM sources WHERE indexing_status IN ('done', 'partial') ORDER BY source_id"
            )
        ]
        return [discover_source(connection, source_id) for source_id in ids]


def reconcile_automatic_knowledge(connection: sqlite3.Connection) -> None:
    """Remove unsupported automatic derivatives while preserving user curation."""
    connection.execute(
        "UPDATE relationships SET reviewed_status = 'unsupported', updated_at = CURRENT_TIMESTAMP "
        "WHERE created_by IN ('system', 'llm') AND discovery_method <> 'manual' "
        "AND reviewed_status = 'unreviewed' "
        "AND NOT EXISTS (SELECT 1 FROM relationship_evidence e "
        "WHERE e.relationship_id = relationships.relationship_id)"
    )
    connection.execute(
        "UPDATE objects SET status = 'inactive', updated_at = CURRENT_TIMESTAMP "
        "WHERE created_by IN ('system', 'llm') AND status = 'active' "
        "AND NOT EXISTS (SELECT 1 FROM object_evidence e WHERE e.object_id = objects.object_id)"
    )


def clear_automatic_source_evidence(connection: sqlite3.Connection, source_id: int) -> None:
    """Remove only derived support for a source before reindexing or invalidation."""
    connection.execute(
        "DELETE FROM object_evidence WHERE source_id = ? AND discovery_method <> 'manual'",
        (source_id,),
    )
    connection.execute(
        "DELETE FROM relationship_evidence WHERE source_id = ? AND discovery_method <> 'manual'",
        (source_id,),
    )
    reconcile_automatic_knowledge(connection)


def extract_candidates(
    source_id: int,
    source_title: str | None,
    original_path: str,
    units: Iterable[sqlite3.Row],
) -> list[EntityCandidate]:
    """Expose deterministic extraction for focused unit tests and future callers."""
    source = {"source_id": source_id, "title": source_title, "original_path": original_path}
    candidates = list(_source_candidates(source))
    for unit in units:
        candidates.extend(_unit_candidates(source_id, unit))
    return _deduplicate_candidates(candidates)


def _source_candidates(source: sqlite3.Row | dict[str, object]) -> list[EntityCandidate]:
    source_id = int(source["source_id"])
    title = str(source["title"] or "")
    filename = Path(str(source["original_path"] or "")).stem.replace("_", " ").replace("-", " ")
    title_path = Path(title)
    text = title_path.stem if title_path.suffix else title
    text = text or filename
    if not text or _is_generic(text):
        return []
    project = re.search(
        r"\bProject\s+([A-Z][\w-]*(?:\s+[A-Z][\w-]*){0,3})", text
    )
    if project:
        return [EntityCandidate(project.group(0).strip(), "project", source_id, None, "title")]
    return _phrase_candidates(source_id, None, text, "title", "topic")


def _unit_candidates(source_id: int, unit: sqlite3.Row) -> list[EntityCandidate]:
    unit_id = int(unit["unit_id"])
    text = str(unit["extracted_text"] or "")
    comment = str(unit["short_comment"] or "")
    candidates: list[EntityCandidate] = []
    for phrase in _CONCEPT_PHRASES:
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text, re.IGNORECASE):
            candidates.append(EntityCandidate(phrase.title(), "concept", source_id, unit_id, "lexical"))
    for heading in re.findall(r"(?m)^\s*#{1,6}\s+(.+?)\s*$", text):
        candidates.extend(_phrase_candidates(source_id, unit_id, heading, "lexical", "topic"))
    for match in re.finditer(
        r"\b(?:Project|Team|Organization|Company|University|Institute)\s+"
        r"[A-Z][\w-]*(?:\s+[A-Z][\w-]*){0,3}",
        text,
    ):
        value = match.group(0).strip()
        kind = "project" if value.casefold().startswith("project ") else "organization"
        candidates.append(EntityCandidate(value, kind, source_id, unit_id, "lexical"))
    for match in re.finditer(
        r"\b(?:by|author|written by)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})", text
    ):
        candidates.append(EntityCandidate(match.group(1), "person", source_id, unit_id, "lexical"))
    for match in re.finditer(
        r"\b(?:Place|Location|based in|from)\s*:?\s*"
        r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})",
        text,
    ):
        candidates.append(EntityCandidate(match.group(1), "place", source_id, unit_id, "lexical"))
    for match in re.finditer(
        r"\b(?:Conference|Summit|Workshop|Meeting|Hackathon)\s+"
        r"(?:[A-Z][\w-]*|\d{4})(?:\s+(?:[A-Z][\w-]*|\d{4})){0,2}",
        text,
    ):
        candidates.append(EntityCandidate(match.group(0), "event", source_id, unit_id, "lexical"))
    for match in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text):
        value = match.group(1).strip()
        words = value.casefold().split()
        if words[0] in {"the", "this", "these", "those", "figure", "table"}:
            continue
        kind = "organization" if words[-1] in _ORG_SUFFIXES else "topic"
        candidates.append(EntityCandidate(value, kind, source_id, unit_id, "lexical"))
    for tag in _parse_tags(unit["tags_json"]):
        candidates.extend(_phrase_candidates(source_id, unit_id, tag, "tag", "topic"))
    for phrase in _CONCEPT_PHRASES:
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", comment, re.IGNORECASE):
            candidates.append(EntityCandidate(phrase.title(), "concept", source_id, unit_id, "lexical"))
    return candidates


def _phrase_candidates(
    source_id: int, unit_id: int | None, text: str, method: str, default_type: str
) -> list[EntityCandidate]:
    value = _clean_phrase(text)
    if not value or _is_generic(value):
        return []
    return [EntityCandidate(value, default_type, source_id, unit_id, method)]


def _deduplicate_candidates(candidates: Iterable[EntityCandidate]) -> list[EntityCandidate]:
    result: dict[tuple[str, str, int | None], EntityCandidate] = {}
    for candidate in candidates:
        title = _clean_phrase(candidate.title)
        normalized = _normalize_entity_title(title)
        if not title or not normalized or _is_generic(title):
            continue
        if len(normalized) < 3 or all(
            character.isdigit() or character.isspace() for character in normalized
        ):
            continue
        key = (candidate.object_type, normalized, candidate.unit_id)
        result.setdefault(
            key,
            EntityCandidate(
                title,
                candidate.object_type,
                candidate.source_id,
                candidate.unit_id,
                candidate.discovery_method,
                candidate.confidence,
            ),
        )
    return list(result.values())


def _resolve_entity(connection: sqlite3.Connection, candidate: EntityCandidate) -> tuple[int, bool]:
    normalized = _normalize_entity_title(candidate.title)
    row = connection.execute(
        "SELECT object_id, status, created_by FROM objects "
        "WHERE object_type = ? AND normalized_title = ?",
        (candidate.object_type, normalized),
    ).fetchone()
    if row is not None:
        if row["status"] == "inactive" and row["created_by"] in {"system", "llm"}:
            connection.execute(
                "UPDATE objects SET status = 'active', updated_at = CURRENT_TIMESTAMP WHERE object_id = ?",
                (row["object_id"],),
            )
        return int(row["object_id"]), False

    matches: list[sqlite3.Row] = []
    for object_row in connection.execute(
        "SELECT object_id, aliases_json FROM objects "
        "WHERE object_type = ?",
        (candidate.object_type,),
    ):
        try:
            aliases = json.loads(object_row["aliases_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            aliases = []
        if any(_normalize_entity_title(str(alias)) == normalized for alias in aliases):
            matches.append(object_row)
    if len(matches) == 1:
        if matches[0]["created_by"] in {"system", "llm"}:
            connection.execute(
                "UPDATE objects SET status = 'active', updated_at = CURRENT_TIMESTAMP WHERE object_id = ?",
                (matches[0]["object_id"],),
            )
        return int(matches[0]["object_id"]), False
    return _create_entity(connection, candidate, normalized)


def _create_entity(
    connection: sqlite3.Connection, candidate: EntityCandidate, normalized: str
) -> tuple[int, bool]:
    cursor = connection.execute(
        "INSERT INTO objects(object_type, title, normalized_title, aliases_json, "
        "created_by, discovery_method, status) "
        "VALUES (?, ?, ?, '[]', 'system', ?, 'active')",
        (candidate.object_type, candidate.title, normalized, candidate.discovery_method),
    )
    return int(cursor.lastrowid), True


def _ensure_related_relationship(
    connection: sqlite3.Connection, from_id: int, to_id: int
) -> tuple[int | None, bool]:
    existing = connection.execute(
        "SELECT relationship_id, is_symmetric FROM relationships "
        "WHERE relationship_type = 'related_to' AND identity_a = ? AND identity_b = ?",
        (from_id, to_id),
    ).fetchone()
    if existing is not None:
        if int(existing["is_symmetric"]) == 1:
            return int(existing["relationship_id"]), False
        return None, False
    explanation = "Entities co-occur in the same indexed document unit."
    cursor = connection.execute(
        "INSERT INTO relationships "
        "(from_object_id, to_object_id, relationship_type, is_symmetric, identity_a, identity_b, "
        "confidence, created_by, discovery_method, reviewed_status, explanation) "
        "VALUES (?, ?, 'related_to', 1, ?, ?, NULL, 'system', 'cooccurrence', 'unreviewed', ?)",
        (from_id, to_id, from_id, to_id, explanation),
    )
    return int(cursor.lastrowid), True


def _parse_tags(raw: object) -> list[str]:
    try:
        values = json.loads(str(raw or "[]"))
    except json.JSONDecodeError:
        return []
    return [str(value) for value in values if isinstance(value, str)] if isinstance(values, list) else []


def _clean_phrase(value: str) -> str:
    cleaned = re.sub(r"[\u0000-\u001f]+", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \t\r\n.,;:!?()[]{}\"'")
    return cleaned


def _is_generic(value: str) -> bool:
    words = _normalize_entity_title(value).split()
    return not words or all(word in _GENERIC_TERMS for word in words)

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

from app.ai.providers import (
    EmbeddingProvider,
    LocalMetadataProvider,
    ProviderUnavailable,
    UnitMetadataProvider,
)
from app.config import Settings
from app.db.database import get_connection
from app.ingestion.chunker import chunk_text
from app.ingestion.extractor import ExtractedUnit, extract_file
from app.retrieval.vector_store import FaissVectorStore, VectorStoreUnavailable


def index_source(
    source_id: int,
    settings: Settings,
    embedding_provider: EmbeddingProvider | None = None,
    metadata_provider: UnitMetadataProvider | None = None,
) -> dict[str, int | str]:
    """Extract one source, write units/FTS/vector data, and mark it complete."""
    with get_connection(settings.database_path) as connection:
        source = connection.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone()
        if source is None:
            raise ValueError(f"source {source_id} does not exist")
        connection.execute(
            "UPDATE sources SET indexing_status = 'processing', indexing_error = NULL WHERE source_id = ?",
            (source_id,),
        )
        try:
            result = _index_source_in_transaction(
                connection, source, settings, embedding_provider, metadata_provider
            )
            connection.execute(
                "UPDATE sources SET indexing_status = 'done', indexing_error = ? WHERE source_id = ?",
                (result.get("warning"), source_id),
            )
            return result
        except Exception as exc:
            connection.execute(
                "UPDATE sources SET indexing_status = 'error', indexing_error = ? WHERE source_id = ?",
                (str(exc), source_id),
            )
            raise


def _index_source_in_transaction(
    connection: sqlite3.Connection,
    source: sqlite3.Row,
    settings: Settings,
    embedding_provider: EmbeddingProvider | None,
    metadata_provider: UnitMetadataProvider | None,
) -> dict[str, int | str]:
    source_id = int(source["source_id"])
    old_units = connection.execute(
        "SELECT unit_id FROM document_units WHERE source_id = ?", (source_id,)
    ).fetchall()
    old_ids = [int(row["unit_id"]) for row in old_units]
    _remove_source_derived_rows(connection, source_id)

    text_store = visual_store = None
    warning = ""
    try:
        if embedding_provider is not None:
            text_store = FaissVectorStore(settings.data_dir / "text.index", embedding_provider.dimension)
            visual_store = FaissVectorStore(settings.data_dir / "visual.index", embedding_provider.dimension)
            text_store.remove_many(old_ids)
            visual_store.remove_many(old_ids)
    except VectorStoreUnavailable as exc:
        warning = str(exc)

    metadata_provider = metadata_provider or LocalMetadataProvider()
    source_path = Path(source["original_path"])
    extracted_units = extract_file(source_path, source["source_type"])
    unit_count = 0
    embedding_count = 0
    metadata_fallbacks = 0

    for extracted in extracted_units:
        chunks = chunk_text(extracted.text)
        for chunk in chunks:
            unit_hash = _unit_hash(source["content_hash"], extracted, chunk.ordinal, chunk.text)
            try:
                comment, tags = metadata_provider.describe(
                    chunk.text, extracted.image_bytes if chunk.ordinal == 0 else None,
                    extracted.image_mime_type, unit_hash, connection
                )
            except (ProviderUnavailable, RuntimeError, ValueError):
                comment, tags = LocalMetadataProvider().describe(
                    chunk.text, extracted.image_bytes if chunk.ordinal == 0 else None,
                    extracted.image_mime_type, unit_hash, connection
                )
                metadata_fallbacks += 1

            cursor = connection.execute(
                "INSERT INTO document_units "
                "(source_id, unit_type, page_or_slide, extracted_text, short_comment, tags_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (source_id, extracted.unit_type, extracted.page_or_slide, chunk.text, comment, json.dumps(tags)),
            )
            unit_id = int(cursor.lastrowid)
            thumbnail_path = _write_thumbnail(settings, source_id, unit_id, extracted)
            connection.execute(
                "UPDATE document_units SET thumbnail_path = ? WHERE unit_id = ?",
                (thumbnail_path, unit_id),
            )
            connection.execute(
                "INSERT INTO fts_units(unit_id, source_id, extracted_text, short_comment, tags) VALUES (?, ?, ?, ?, ?)",
                (unit_id, source_id, chunk.text, comment, " ".join(tags)),
            )
            unit_count += 1

            if embedding_provider is not None and text_store is not None:
                try:
                    text_vector = embedding_provider.embed_text(chunk.text or comment, unit_hash, connection)
                    text_store.add(unit_id, text_vector)
                    connection.execute(
                        "UPDATE document_units SET text_embedding_ref = ? WHERE unit_id = ?",
                        (f"{embedding_provider.name}:{unit_id}", unit_id),
                    )
                    if extracted.image_bytes is not None and chunk.ordinal == 0:
                        visual_hash = _hash_bytes(extracted.image_bytes)
                        visual_vector = embedding_provider.embed_image(
                            extracted.image_bytes, extracted.image_mime_type or "application/octet-stream",
                            visual_hash, connection
                        )
                        visual_store.add(unit_id, visual_vector)
                        connection.execute(
                            "UPDATE document_units SET visual_embedding_ref = ? WHERE unit_id = ?",
                            (f"{embedding_provider.name}:{unit_id}", unit_id),
                        )
                    embedding_count += 1
                except (ProviderUnavailable, VectorStoreUnavailable) as exc:
                    warning = str(exc)

    if text_store is not None:
        text_store.persist()
    if visual_store is not None:
        visual_store.persist()
    if metadata_fallbacks:
        warning = f"{metadata_fallbacks} unit metadata calls used the local fallback"
    return {"units": unit_count, "embeddings": embedding_count, "warning": warning}


def run_pending(
    settings: Settings,
    embedding_provider: EmbeddingProvider | None = None,
    metadata_provider: UnitMetadataProvider | None = None,
) -> dict[str, int]:
    with get_connection(settings.database_path) as connection:
        source_ids = [
            int(row["source_id"])
            for row in connection.execute(
                "SELECT source_id FROM sources WHERE indexing_status = 'pending' ORDER BY source_id"
            )
        ]
    completed = errors = 0
    for source_id in source_ids:
        try:
            index_source(source_id, settings, embedding_provider, metadata_provider)
            completed += 1
        except Exception:
            errors += 1
    return {"queued": len(source_ids), "completed": completed, "errors": errors}


def _remove_source_derived_rows(connection: sqlite3.Connection, source_id: int) -> None:
    connection.execute("DELETE FROM fts_units WHERE source_id = ?", (source_id,))
    connection.execute("DELETE FROM document_units WHERE source_id = ?", (source_id,))


def _write_thumbnail(settings: Settings, source_id: int, unit_id: int, extracted: ExtractedUnit) -> str | None:
    if extracted.image_bytes is None:
        return None
    relative = Path("thumbnails") / str(source_id) / f"unit_{unit_id}.png"
    destination = settings.derived_dir / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(extracted.image_bytes)
    return str(relative)


def _unit_hash(source_hash: str, extracted: ExtractedUnit, ordinal: int, text: str) -> str:
    payload = f"{source_hash}:{extracted.unit_type}:{extracted.page_or_slide}:{ordinal}:{text}".encode()
    return hashlib.sha256(payload).hexdigest()


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3

from app.ai.providers import EmbeddingProvider, LocalMetadataProvider, ProviderUnavailable, UnitMetadataProvider
from app.config import Settings
from app.db.database import get_connection
from app.db.database import record_memory_event
from app.ingestion.chunker import chunk_text
from app.ingestion.extractor import ExtractedUnit, extract_file
from app.knowledge.discovery import clear_automatic_source_evidence, discover_source
from app.retrieval.vector_store import FaissVectorStore, VectorStoreUnavailable


def index_source(
    source_id: int,
    settings: Settings,
    embedding_provider: EmbeddingProvider | None = None,
    metadata_provider: UnitMetadataProvider | None = None,
) -> dict[str, object]:
    """Index one source and persist a truthful terminal status."""
    with get_connection(settings.database_path) as connection:
        source = connection.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone()
    if source is None:
        raise ValueError(f"source {source_id} does not exist")

    _set_source_status(settings, source_id, "processing", None)
    try:
        # Commit cleanup separately so a failed re-index cannot leave stale units.
        _clear_source_derived_data(settings, source_id)
        with get_connection(settings.database_path) as connection:
            source = connection.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone()
            if source is None:
                raise ValueError(f"source {source_id} does not exist")
            result = _index_source_in_transaction(
                connection, source, settings, embedding_provider, metadata_provider
            )
            if result["status"] in {"done", "partial"}:
                _record_source_event(connection, source, result)
            connection.execute(
                "UPDATE sources SET indexing_status = ?, indexing_error = ? WHERE source_id = ?",
                (str(result["status"]), _result_detail(result), source_id),
            )
        return result
    except Exception as exc:
        # A fresh transaction makes the error status durable after rollback.
        _set_source_status(settings, source_id, "error", json.dumps({"errors": [str(exc)]}))
        raise


def _index_source_in_transaction(
    connection: sqlite3.Connection,
    source: sqlite3.Row,
    settings: Settings,
    embedding_provider: EmbeddingProvider | None,
    metadata_provider: UnitMetadataProvider | None,
) -> dict[str, object]:
    source_id = int(source["source_id"])
    warnings: list[str] = []
    errors: list[str] = []
    metadata_fallbacks = 0
    text_embeddings_created = 0
    visual_embeddings_created = 0
    unit_count = 0
    discovery_result: dict[str, int] = {}

    if embedding_provider is None:
        warnings.append(
            "Embeddings unavailable: cloud AI is disabled"
            if not settings.allow_cloud_ai
            else "Embeddings unavailable: no operational embedding provider is configured"
        )

    metadata_provider = metadata_provider or LocalMetadataProvider()
    extracted_units = _prepare_extracted_units(
        extract_file(Path(source["original_path"]), source["source_type"]),
        embedding_provider or metadata_provider,
        connection,
        warnings,
    )

    text_store = visual_store = None
    if embedding_provider is not None:
        try:
            text_store = FaissVectorStore(settings.data_dir / "text.index", embedding_provider.dimension)
            visual_store = FaissVectorStore(settings.data_dir / "visual.index", embedding_provider.dimension)
        except VectorStoreUnavailable as exc:
            errors.append(f"Embedding storage unavailable: {exc}")
            text_store = visual_store = None

    for extracted in extracted_units:
        if extracted.warning:
            warnings.append(extracted.warning)
        if not extracted.text.strip() and extracted.image_bytes is None:
            location = f" {extracted.page_or_slide}" if extracted.page_or_slide is not None else ""
            warnings.append(f"No extractable text was found for {extracted.unit_type}{location}")
            continue
        for chunk in chunk_text(extracted.text):
            unit_hash = _unit_hash(source["content_hash"], extracted, chunk.ordinal, chunk.text)
            used_local_metadata = getattr(metadata_provider, "name", "") == "local-heuristic"
            try:
                comment, tags = metadata_provider.describe(
                    chunk.text,
                    extracted.image_bytes if chunk.ordinal == 0 else None,
                    extracted.image_mime_type,
                    unit_hash,
                    connection,
                )
            except (ProviderUnavailable, RuntimeError, ValueError) as exc:
                warnings.append(f"Metadata fallback for unit {unit_hash[:8]}: {exc}")
                comment, tags = LocalMetadataProvider().describe(
                    chunk.text, extracted.image_bytes if chunk.ordinal == 0 else None,
                    extracted.image_mime_type, unit_hash, connection
                )
                used_local_metadata = True
            if used_local_metadata:
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
                "INSERT INTO fts_units(unit_id, source_id, extracted_text, short_comment, tags) "
                "VALUES (?, ?, ?, ?, ?)",
                (unit_id, source_id, chunk.text, comment, " ".join(tags)),
            )
            unit_count += 1

            if embedding_provider is None or text_store is None:
                continue
            try:
                text_vector = embedding_provider.embed_text(chunk.text or comment, unit_hash, connection)
                text_store.add(unit_id, text_vector)
                connection.execute(
                    "UPDATE document_units SET text_embedding_ref = ? WHERE unit_id = ?",
                    (f"{embedding_provider.name}:{unit_id}", unit_id),
                )
                text_embeddings_created += 1
            except (ProviderUnavailable, RuntimeError, ValueError, VectorStoreUnavailable) as exc:
                errors.append(f"Text embedding failed for unit {unit_id}: {exc}")

            if extracted.image_bytes is not None and chunk.ordinal == 0 and visual_store is not None:
                try:
                    visual_vector = embedding_provider.embed_image(
                        extracted.image_bytes,
                        extracted.image_mime_type or "application/octet-stream",
                        _hash_bytes(extracted.image_bytes),
                        connection,
                    )
                    visual_store.add(unit_id, visual_vector)
                    connection.execute(
                        "UPDATE document_units SET visual_embedding_ref = ? WHERE unit_id = ?",
                        (f"{embedding_provider.name}:{unit_id}", unit_id),
                    )
                    visual_embeddings_created += 1
                except (ProviderUnavailable, RuntimeError, ValueError, VectorStoreUnavailable) as exc:
                    errors.append(f"Visual embedding failed for unit {unit_id}: {exc}")

    discovery_result = discover_source(connection, source_id).as_dict()

    for store, label in ((text_store, "text"), (visual_store, "visual")):
        if store is not None:
            try:
                store.persist()
            except (VectorStoreUnavailable, RuntimeError, ValueError) as exc:
                errors.append(f"{label} embedding persistence failed: {exc}")

    if metadata_fallbacks:
        warnings.append(f"{metadata_fallbacks} unit metadata calls used the local fallback")
    status = "partial" if warnings or errors else "done"
    result: dict[str, object] = {
        "units_created": unit_count,
        "text_embeddings_created": text_embeddings_created,
        "visual_embeddings_created": visual_embeddings_created,
        "metadata_fallbacks": metadata_fallbacks,
        "warnings": warnings,
        "errors": errors,
        "status": status,
        # Compatibility aliases for existing callers.
        "units": unit_count,
        "embeddings": text_embeddings_created + visual_embeddings_created,
        "warning": warnings[0] if warnings else "",
        **discovery_result,
    }
    return result


def _prepare_extracted_units(
    extracted_units: list[ExtractedUnit],
    embedding_provider: object | None,
    connection: sqlite3.Connection,
    warnings: list[str],
) -> list[ExtractedUnit]:
    """Complete image/page text extraction before units enter FTS or RAG.

    The extractor performs local OCR when an OCR binary is available. A
    configured multimodal provider is an optional second boundary for scanned
    pages and images that still have no text. Provider failures become visible
    partial-indexing warnings and never abort unrelated units.
    """
    prepared: list[ExtractedUnit] = []
    extract_image_text = getattr(embedding_provider, "extract_image_text", None)
    for extracted in extracted_units:
        if extracted.text.strip() or extracted.image_bytes is None:
            prepared.append(extracted)
            continue
        if callable(extract_image_text):
            try:
                text = extract_image_text(
                    extracted.image_bytes,
                    extracted.image_mime_type or "application/octet-stream",
                    _hash_bytes(extracted.image_bytes),
                    connection,
                )
                if text and text.strip():
                    prepared.append(replace(extracted, text=text.strip(), warning=None))
                    continue
            except (ProviderUnavailable, RuntimeError, ValueError) as exc:
                warnings.append(
                    f"Visual text extraction unavailable for unit "
                    f"{extracted.page_or_slide or 'image'}: {exc}"
                )
        prepared.append(extracted)
    return prepared


def run_pending(
    settings: Settings,
    embedding_provider: EmbeddingProvider | None = None,
    metadata_provider: UnitMetadataProvider | None = None,
    mount_id: int | None = None,
) -> dict[str, object]:
    with get_connection(settings.database_path) as connection:
        query = "SELECT source_id FROM sources WHERE indexing_status = 'pending'"
        parameters: tuple[object, ...] = ()
        if mount_id is not None:
            query += " AND mount_id = ?"
            parameters = (mount_id,)
        query += " ORDER BY source_id"
        source_ids = [
            int(row["source_id"])
            for row in connection.execute(query, parameters)
        ]
    completed = partial = errors_count = 0
    results: list[dict[str, object]] = []
    warnings: list[str] = []
    errors: list[str] = []
    for source_id in source_ids:
        try:
            result = index_source(source_id, settings, embedding_provider, metadata_provider)
            results.append(result)
            if result["status"] == "partial":
                partial += 1
            else:
                completed += 1
            warnings.extend(str(item) for item in result["warnings"])
            errors.extend(str(item) for item in result["errors"])
        except Exception as exc:
            errors_count += 1
            errors.append(f"Source {source_id}: {exc}")
    return {
        "queued": len(source_ids),
        "completed": completed,
        "partial": partial,
        "errors": errors_count,
        "results": results,
        "warnings": warnings,
        "error_details": errors,
    }


def _set_source_status(settings: Settings, source_id: int, status: str, detail: str | None) -> None:
    with get_connection(settings.database_path) as connection:
        connection.execute(
            "UPDATE sources SET indexing_status = ?, indexing_error = ? WHERE source_id = ?",
            (status, detail, source_id),
        )


def _record_source_event(connection: sqlite3.Connection, source: sqlite3.Row, result: dict[str, object]) -> None:
    source_id = int(source["source_id"])
    latest = connection.execute(
        "SELECT event_type, metadata_json FROM memory_events "
        "WHERE source_id = ? AND event_type IN ('file_imported', 'file_modified', 'note_created', 'webpage_saved', 'screenshot_captured') "
        "ORDER BY event_id DESC LIMIT 1",
        (source_id,),
    ).fetchone()
    latest_hash = None
    if latest is not None:
        latest_hash = json.loads(latest["metadata_json"]).get("content_hash")
    if latest is not None and latest_hash == source["content_hash"]:
        return

    event_type = "file_imported" if latest is None or latest["event_type"] in {
        "note_created", "webpage_saved", "screenshot_captured"
    } else "file_modified"
    timestamp = datetime.now(UTC)
    date_basis = "imported_at"
    if event_type == "file_modified" and source["modified_at"]:
        timestamp = _as_utc_datetime(source["modified_at"])
        date_basis = "modified_at"
    metadata = {
        "source_title": source["title"],
        "source_type": source["source_type"],
        "content_hash": source["content_hash"],
        "original_created_at": source["created_at"],
        "original_modified_at": source["modified_at"],
        "units_created": result["units_created"],
    }
    record_memory_event(
        connection,
        event_type,
        source_id=source_id,
        timestamp=timestamp,
        date_basis=date_basis,
        metadata=metadata,
    )


def _as_utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)


def _clear_source_derived_data(settings: Settings, source_id: int) -> None:
    with get_connection(settings.database_path) as connection:
        old_units = connection.execute(
            "SELECT unit_id, thumbnail_path FROM document_units WHERE source_id = ?", (source_id,)
        ).fetchall()
        old_ids = [int(row["unit_id"]) for row in old_units]
        old_thumbnails = [row["thumbnail_path"] for row in old_units if row["thumbnail_path"]]
        _remove_source_derived_rows(connection, source_id)
    for relative in old_thumbnails:
        _remove_derived_file(settings, relative)
    if not old_ids:
        return
    # Keep vector stores consistent with the committed SQL cleanup. NumPy is
    # the supported fallback, so an unavailable optional vector backend does
    # not make ordinary FTS indexing fail.
    try:
        stores = (
            FaissVectorStore(settings.data_dir / "text.index", settings.embedding_dimension),
            FaissVectorStore(settings.data_dir / "visual.index", settings.embedding_dimension),
        )
        for store in stores:
            store.remove_many(old_ids)
            store.persist()
    except (VectorStoreUnavailable, RuntimeError, ValueError):
        pass


def _remove_derived_file(settings: Settings, relative_path: str) -> None:
    derived_root = settings.derived_dir.resolve()
    candidate = (settings.derived_dir / relative_path).resolve()
    try:
        candidate.relative_to(derived_root)
    except ValueError:
        return
    if candidate.is_file() or candidate.is_symlink():
        candidate.unlink()


def _result_detail(result: dict[str, object]) -> str | None:
    if not result["warnings"] and not result["errors"]:
        return None
    return json.dumps({"warnings": result["warnings"], "errors": result["errors"]})


def _remove_source_derived_rows(connection: sqlite3.Connection, source_id: int) -> None:
    connection.execute("DELETE FROM fts_units WHERE source_id = ?", (source_id,))
    connection.execute("DELETE FROM document_units WHERE source_id = ?", (source_id,))
    clear_automatic_source_evidence(connection, source_id)


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

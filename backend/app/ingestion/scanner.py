from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from pathlib import Path
import mimetypes
import os
import sqlite3
import shutil

from app.retrieval.vector_store import FaissVectorStore, VectorStoreUnavailable
from app.knowledge.discovery import clear_automatic_source_evidence


SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf",
    ".pptx": "pptx",
    ".docx": "docx",
    ".txt": "text",
    ".md": "markdown",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".webp": "image",
}


@dataclass(frozen=True)
class DiscoveredFile:
    path: Path
    source_type: str
    mime_type: str | None
    content_hash: str
    created_at: str
    modified_at: str


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def discover_files(folder: Path) -> list[DiscoveredFile]:
    root = folder.resolve()
    discovered: list[DiscoveredFile] = []
    try:
        candidates = sorted(folder.rglob("*"), key=lambda item: str(item))
    except OSError:
        return discovered
    for candidate in candidates:
        try:
            path = candidate.resolve(strict=True)
            relative = path.relative_to(root)
        except (OSError, ValueError):
            # Broken links, inaccessible files, and links escaping the mounted
            # folder are not part of the local source inventory.
            continue
        if not path.is_file() or any(part.startswith(".") for part in relative.parts):
            continue
        source_type = SUPPORTED_EXTENSIONS.get(path.suffix.lower())
        if source_type is None:
            continue
        try:
            stat = path.stat()
            created_timestamp = getattr(stat, "st_birthtime", stat.st_ctime)
            content_hash = sha256_file(path)
        except OSError:
            # A single unreadable file must not abort the rest of a scan.
            continue
        discovered.append(
            DiscoveredFile(
                path=path,
                source_type=source_type,
                mime_type=mimetypes.guess_type(path.name)[0],
                content_hash=content_hash,
                created_at=datetime.fromtimestamp(created_timestamp, UTC).isoformat(),
                modified_at=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            )
        )
    return discovered


def sync_mount(
    connection: sqlite3.Connection, mount_id: int, folder: Path, settings=None
) -> dict[str, int]:
    """Register new/changed files and mark removed files as pending.

    Actual extraction is intentionally separate: this function only establishes the
    durable inventory and change-detection contract for the worker.
    """
    discovered = {str(item.path): item for item in discover_files(folder)}
    existing_rows = connection.execute(
        "SELECT source_id, original_path, content_hash, indexing_status, excluded "
        "FROM sources WHERE mount_id = ?",
        (mount_id,),
    ).fetchall()
    existing = {row["original_path"]: row for row in existing_rows}
    added = changed = unchanged = removed = 0

    for path, item in discovered.items():
        previous = existing.get(path)
        if previous is None:
            connection.execute(
                "INSERT INTO sources "
                "(mount_id, source_type, title, original_path, mime_type, content_hash, created_at, modified_at, indexing_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                (mount_id, item.source_type, item.path.name, path, item.mime_type, item.content_hash,
                 item.created_at, item.modified_at),
            )
            added += 1
        elif previous["content_hash"] != item.content_hash:
            connection.execute(
                "UPDATE sources SET source_type = ?, title = ?, mime_type = ?, content_hash = ?, "
                "modified_at = ?, indexing_status = 'pending', indexing_error = NULL, excluded = 0 "
                "WHERE source_id = ?",
                (item.source_type, item.path.name, item.mime_type, item.content_hash, item.modified_at, previous["source_id"]),
            )
            changed += 1
        else:
            # A failed or temporarily unavailable file is eligible for a
            # deliberate rescan once it is readable again. Partial sources
            # remain partial until an explicit reindex, so cloud-disabled
            # folders are not needlessly reprocessed on every scan.
            if previous["excluded"] or previous["indexing_status"] == "error":
                connection.execute(
                    "UPDATE sources SET indexing_status = 'pending', indexing_error = NULL, excluded = 0 "
                    "WHERE source_id = ?",
                    (previous["source_id"],),
                )
            unchanged += 1

    for path, row in existing.items():
        if path not in discovered and row["content_hash"] is not None:
            unit_rows = connection.execute(
                "SELECT unit_id FROM document_units WHERE source_id = ?", (row["source_id"],)
            ).fetchall()
            unit_ids = [int(unit["unit_id"]) for unit in unit_rows]
            connection.execute("DELETE FROM fts_units WHERE source_id = ?", (row["source_id"],))
            connection.execute("DELETE FROM document_units WHERE source_id = ?", (row["source_id"],))
            connection.execute(
                "UPDATE sources SET indexing_status = 'pending', indexing_error = 'file no longer exists', excluded = 1 "
                "WHERE source_id = ?",
                (row["source_id"],),
            )
            clear_automatic_source_evidence(connection, int(row["source_id"]))
            if settings is not None:
                _remove_source_vectors(settings, unit_ids)
                thumbnail_dir = settings.derived_dir / "thumbnails" / str(row["source_id"])
                if thumbnail_dir.exists():
                    shutil.rmtree(thumbnail_dir)
            removed += 1

    return {"added": added, "changed": changed, "unchanged": unchanged, "removed": removed}


def _remove_source_vectors(settings, unit_ids: list[int]) -> None:
    if not unit_ids:
        return
    try:
        stores = (
            FaissVectorStore(settings.data_dir / "text.index", settings.embedding_dimension),
            FaissVectorStore(settings.data_dir / "visual.index", settings.embedding_dimension),
        )
        for store in stores:
            store.remove_many(unit_ids)
            store.persist()
    except (VectorStoreUnavailable, RuntimeError, ValueError):
        pass


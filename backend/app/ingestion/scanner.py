from dataclasses import dataclass
from datetime import datetime
import hashlib
from pathlib import Path
import mimetypes
import os
import sqlite3
import shutil

from app.retrieval.vector_store import FaissVectorStore, VectorStoreUnavailable


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
    modified_at: str


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def discover_files(folder: Path) -> list[DiscoveredFile]:
    discovered: list[DiscoveredFile] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        source_type = SUPPORTED_EXTENSIONS.get(path.suffix.lower())
        if source_type is None:
            continue
        stat = path.stat()
        discovered.append(
            DiscoveredFile(
                path=path.resolve(),
                source_type=source_type,
                mime_type=mimetypes.guess_type(path.name)[0],
                content_hash=sha256_file(path),
                modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
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
        "SELECT source_id, original_path, content_hash FROM sources WHERE mount_id = ?",
        (mount_id,),
    ).fetchall()
    existing = {row["original_path"]: row for row in existing_rows}
    added = changed = unchanged = removed = 0

    for path, item in discovered.items():
        previous = existing.get(path)
        if previous is None:
            connection.execute(
                "INSERT INTO sources "
                "(mount_id, source_type, title, original_path, mime_type, content_hash, modified_at, indexing_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')",
                (mount_id, item.source_type, item.path.name, path, item.mime_type, item.content_hash, item.modified_at),
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
                "UPDATE sources SET indexing_status = 'pending', indexing_error = 'file no longer exists' "
                "WHERE source_id = ?",
                (row["source_id"],),
            )
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


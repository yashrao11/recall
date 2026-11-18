from datetime import UTC, datetime
from pathlib import Path
import shutil

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.db.database import get_connection
from app.ingestion.scanner import sync_mount
from app.knowledge.discovery import reconcile_automatic_knowledge
from app.retrieval.vector_store import FaissVectorStore, VectorStoreUnavailable

router = APIRouter(prefix="/sources", tags=["sources"])


class FolderMountRequest(BaseModel):
    path: str = Field(min_length=1)
    label: str | None = None


@router.post("/folders", status_code=status.HTTP_201_CREATED)
def mount_folder(payload: FolderMountRequest, request: Request) -> dict:
    folder = Path(payload.path).expanduser().resolve()
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=400, detail="path must be an existing directory")

    database_path = request.app.state.settings.database_path
    with get_connection(database_path) as connection:
        try:
            cursor = connection.execute(
                "INSERT INTO folder_mounts(path, label) VALUES (?, ?)",
                (str(folder), payload.label or folder.name),
            )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise HTTPException(status_code=409, detail="folder is already mounted") from exc
            raise
        scan_summary = sync_mount(connection, int(cursor.lastrowid), folder, request.app.state.settings)

    return {
        "mount_id": cursor.lastrowid,
        "path": str(folder),
        "label": payload.label or folder.name,
        "enabled": True,
        "created_at": datetime.now(UTC).isoformat(),
        "indexing_status": "pending" if scan_summary["added"] else "idle",
        "scan_summary": scan_summary,
    }


@router.get("")
def list_sources(request: Request) -> dict:
    with get_connection(request.app.state.settings.database_path) as connection:
        mounts = [dict(row) for row in connection.execute(
            "SELECT mount_id, path, label, enabled, created_at FROM folder_mounts ORDER BY created_at"
        )]
        sources = [dict(row) for row in connection.execute(
            "SELECT source_id, mount_id, source_type, title, original_path, indexing_status, indexing_error "
            "FROM sources ORDER BY imported_at DESC"
        )]
    return {"mounts": mounts, "sources": sources}


@router.get("/{source_id}/file")
def source_file(source_id: int, request: Request) -> FileResponse:
    with get_connection(request.app.state.settings.database_path) as connection:
        source = _source_with_mount(connection, source_id)
    path = _mounted_file_path(source)
    return FileResponse(path, media_type=source["mime_type"] or "application/octet-stream")


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: int, request: Request) -> None:
    settings = request.app.state.settings
    with get_connection(request.app.state.settings.database_path) as connection:
        source = _source_with_mount(connection, source_id)
        unit_ids = [
            int(row["unit_id"])
            for row in connection.execute("SELECT unit_id FROM document_units WHERE source_id = ?", (source_id,))
        ]
        connection.execute("DELETE FROM fts_units WHERE source_id = ?", (source_id,))
        connection.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))
        reconcile_automatic_knowledge(connection)
    try:
        text_store = FaissVectorStore(settings.data_dir / "text.index", settings.embedding_dimension)
        visual_store = FaissVectorStore(settings.data_dir / "visual.index", settings.embedding_dimension)
        text_store.remove_many(unit_ids)
        visual_store.remove_many(unit_ids)
        text_store.persist()
        visual_store.persist()
    except (VectorStoreUnavailable, RuntimeError, ValueError):
        pass
    thumbnail_dir = settings.derived_dir / "thumbnails" / str(source_id)
    if thumbnail_dir.exists():
        shutil.rmtree(thumbnail_dir)


def _source_with_mount(connection, source_id: int):
    source = connection.execute(
        "SELECT s.*, m.path AS mount_path, m.enabled AS mount_enabled "
        "FROM sources s JOIN folder_mounts m ON m.mount_id = s.mount_id WHERE s.source_id = ?",
        (source_id,),
    ).fetchone()
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    return source


def _mounted_file_path(source) -> Path:
    mount_path = Path(source["mount_path"]).resolve()
    source_path = Path(source["original_path"]).resolve()
    if not source["mount_enabled"] or not source_path.is_file():
        raise HTTPException(status_code=404, detail="source file is unavailable")
    try:
        source_path.relative_to(mount_path)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="source is outside its mounted folder") from exc
    return source_path


from datetime import UTC, datetime
from pathlib import Path
import shutil
import subprocess
import sys

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


class FolderRescanRequest(BaseModel):
    path: str = Field(min_length=1)


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
        "label": payload.label or folder.name,
        "enabled": True,
        "created_at": datetime.now(UTC).isoformat(),
        "indexing_status": "pending" if scan_summary["added"] else "idle",
        "scan_summary": scan_summary,
    }


@router.post("/folders/rescan")
def rescan_folder(payload: FolderRescanRequest, request: Request) -> dict:
    """Rescan an already registered folder without exposing its stored path."""
    folder = Path(payload.path).expanduser().resolve()
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="mounted folder is unavailable")

    with get_connection(request.app.state.settings.database_path) as connection:
        mount = connection.execute(
            "SELECT mount_id, path, label, enabled, created_at FROM folder_mounts WHERE path = ?",
            (str(folder),),
        ).fetchone()
        if mount is None:
            raise HTTPException(status_code=404, detail="folder mount not found")
        if not mount["enabled"]:
            raise HTTPException(status_code=409, detail="folder mount is disabled")
        scan_summary = sync_mount(connection, int(mount["mount_id"]), folder, request.app.state.settings)
        pending = connection.execute(
            "SELECT COUNT(*) FROM sources WHERE mount_id = ? AND indexing_status = 'pending'",
            (mount["mount_id"],),
        ).fetchone()[0]
    return {
        "mount_id": mount["mount_id"],
        "label": mount["label"],
        "enabled": bool(mount["enabled"]),
        "created_at": mount["created_at"],
        "indexing_status": "pending" if pending else "idle",
        "scan_summary": scan_summary,
    }


@router.post("/{mount_id}/rescan")
def rescan_mount(mount_id: int, request: Request) -> dict:
    with get_connection(request.app.state.settings.database_path) as connection:
        mount = connection.execute(
            "SELECT mount_id, path, label, enabled, created_at FROM folder_mounts WHERE mount_id = ?",
            (mount_id,),
        ).fetchone()
        if mount is None:
            raise HTTPException(status_code=404, detail="folder mount not found")
        if not mount["enabled"]:
            raise HTTPException(status_code=409, detail="folder mount is disabled")
        folder = Path(mount["path"]).resolve()
        if not folder.is_dir():
            raise HTTPException(status_code=404, detail="mounted folder is unavailable")
        scan_summary = sync_mount(connection, mount_id, folder, request.app.state.settings)
        pending = connection.execute(
            "SELECT COUNT(*) FROM sources WHERE mount_id = ? AND indexing_status = 'pending'",
            (mount_id,),
        ).fetchone()[0]
    return {
        "mount_id": mount_id,
        "label": mount["label"],
        "enabled": bool(mount["enabled"]),
        "created_at": mount["created_at"],
        "indexing_status": "pending" if pending else "idle",
        "scan_summary": scan_summary,
    }


@router.get("")
def list_sources(request: Request, mount_id: int | None = None) -> dict:
    with get_connection(request.app.state.settings.database_path) as connection:
        mounts = [dict(row) for row in connection.execute(
            "SELECT mount_id, label, enabled, created_at FROM folder_mounts ORDER BY created_at"
        )]
        source_query = (
            "SELECT source_id, mount_id, source_type, title, original_path, capture_url, indexing_status, indexing_error, excluded "
            "FROM sources "
        )
        source_params: tuple[object, ...] = ()
        if mount_id is not None:
            source_query += "WHERE mount_id = ? "
            source_params = (mount_id,)
        source_query += "ORDER BY imported_at DESC"
        source_rows = [dict(row) for row in connection.execute(source_query, source_params)]
    sources = []
    for source in source_rows:
        source["filename"] = Path(source.pop("original_path")).name
        source["available"] = not bool(source.get("excluded"))
        source["status"] = (
            "excluded" if source.get("excluded") else
            {"done": "ready", "error": "failed"}.get(source["indexing_status"], source["indexing_status"])
        )
        sources.append(source)
    return {"mounts": mounts, "sources": sources}


@router.get("/{source_id}/file")
def source_file(source_id: int, request: Request) -> FileResponse:
    with get_connection(request.app.state.settings.database_path) as connection:
        source = _source_with_mount(connection, source_id)
    path = _mounted_file_path(source)
    return FileResponse(path, media_type=source["mime_type"] or "application/octet-stream")


@router.post("/{source_id}/reveal")
def reveal_source(source_id: int, request: Request) -> dict:
    source = _source_for_file_response(request, source_id)
    if sys.platform != "darwin":
        raise HTTPException(status_code=501, detail="Finder reveal is only supported on macOS")
    try:
        subprocess.run(
            ["open", "-R", str(source)],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=501, detail="macOS open command is unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="Finder reveal timed out") from exc
    except subprocess.CalledProcessError as exc:
        raise HTTPException(status_code=502, detail="Finder could not reveal the source file") from exc
    return {"status": "revealed", "source_id": source_id}


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
        # Explicit source removal also removes automatic graph derivatives that
        # no longer have any evidence. Manual entities/relationships survive.
        connection.execute(
            "DELETE FROM relationships WHERE created_by IN ('system', 'llm') "
            "AND NOT EXISTS (SELECT 1 FROM relationship_evidence e WHERE e.relationship_id = relationships.relationship_id)"
        )
        connection.execute(
            "DELETE FROM objects WHERE created_by IN ('system', 'llm') "
            "AND NOT EXISTS (SELECT 1 FROM object_evidence e WHERE e.object_id = objects.object_id) "
            "AND NOT EXISTS (SELECT 1 FROM relationships r WHERE r.from_object_id = objects.object_id OR r.to_object_id = objects.object_id)"
        )
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


def _source_for_file_response(request: Request, source_id: int) -> Path:
    with get_connection(request.app.state.settings.database_path) as connection:
        source = _source_with_mount(connection, source_id)
    return _mounted_file_path(source)


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


from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.db.database import get_connection
from app.ingestion.scanner import sync_mount

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
        scan_summary = sync_mount(connection, int(cursor.lastrowid), folder)

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


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: int, request: Request) -> None:
    with get_connection(request.app.state.settings.database_path) as connection:
        cursor = connection.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="source not found")


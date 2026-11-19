import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.db.database import get_connection

router = APIRouter(prefix="/units", tags=["units"])


@router.get("/{unit_id}")
def get_unit(unit_id: int, request: Request) -> dict:
    with get_connection(request.app.state.settings.database_path) as connection:
        row = connection.execute(
            "SELECT u.*, s.source_id, s.title, s.original_path, s.mime_type "
            "FROM document_units u JOIN sources s ON s.source_id = u.source_id WHERE u.unit_id = ?",
            (unit_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="unit not found")
    result = dict(row)
    result["filename"] = Path(result.pop("original_path")).name
    result["tags"] = json.loads(result.pop("tags_json"))
    return result


@router.get("/{unit_id}/thumbnail")
def get_thumbnail(unit_id: int, request: Request) -> FileResponse:
    with get_connection(request.app.state.settings.database_path) as connection:
        row = connection.execute(
            "SELECT u.thumbnail_path, s.original_path, m.path AS mount_path, m.enabled AS mount_enabled "
            "FROM document_units u JOIN sources s ON s.source_id = u.source_id "
            "JOIN folder_mounts m ON m.mount_id = s.mount_id WHERE u.unit_id = ?",
            (unit_id,),
        ).fetchone()
    if row is None or not row["thumbnail_path"]:
        raise HTTPException(status_code=404, detail="thumbnail not found")
    source_path = Path(row["original_path"]).resolve()
    mount_path = Path(row["mount_path"]).resolve()
    try:
        source_path.relative_to(mount_path)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="source is outside its mounted folder") from exc
    if not row["mount_enabled"] or not source_path.is_file():
        raise HTTPException(status_code=404, detail="source file is unavailable")
    derived_root = request.app.state.settings.derived_dir.resolve()
    thumbnail = (request.app.state.settings.derived_dir / row["thumbnail_path"]).resolve()
    try:
        thumbnail.relative_to(derived_root)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="thumbnail is outside the derived data folder") from exc
    if not thumbnail.is_file():
        raise HTTPException(status_code=404, detail="thumbnail not found")
    return FileResponse(thumbnail, media_type="image/png")


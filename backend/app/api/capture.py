from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from app.capture.service import CaptureError, CapturePayload, capture_payload, retry_capture

router = APIRouter(prefix="/capture", tags=["capture"])


class CaptureJsonRequest(BaseModel):
    type: str = Field(min_length=1, max_length=20)
    title: str | None = Field(default=None, max_length=200)
    content: str | None = None
    url: str | None = Field(default=None, max_length=2048)


@router.post("/{source_id}/retry")
def retry(source_id: int, request: Request) -> dict:
    try:
        return retry_capture(request.app.state.settings, source_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", response_model=None)
async def capture(request: Request) -> JSONResponse | dict:
    try:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            capture_type = str(form.get("type") or "file")
            files = [item for item in form.getlist("files") if isinstance(item, UploadFile)]
            if not files and isinstance(form.get("file"), UploadFile):
                files = [form.get("file")]
            if capture_type != "file" or not files:
                raise CaptureError("multipart capture requires one or more files")
            items = []
            for upload in files:
                try:
                    data = await _read_upload(upload)
                    items.append(capture_payload(request.app.state.settings, CapturePayload(
                        capture_type="file", filename=upload.filename, mime_type=upload.content_type,
                        data=data,
                    )))
                except CaptureError as exc:
                    items.append({"status": "rejected", "filename": upload.filename, "error": str(exc)})
            overall = "captured" if any(item["status"] in {"indexed", "duplicate", "error"} for item in items) else "rejected"
            code = status.HTTP_201_CREATED if overall == "captured" else status.HTTP_400_BAD_REQUEST
            return JSONResponse({"status": overall, "items": items}, status_code=code)
        try:
            body = CaptureJsonRequest.model_validate(await request.json())
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="invalid capture request") from exc
        item = capture_payload(request.app.state.settings, CapturePayload(
            capture_type=body.type, title=body.title, content=body.content, url=body.url,
        ))
        return JSONResponse({"status": "captured", "items": [item]}, status_code=status.HTTP_201_CREATED)
    except CaptureError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _read_upload(upload: UploadFile) -> bytes:
    from app.capture.service import MAX_UPLOAD_BYTES

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise CaptureError("file exceeds the 25 MB limit")
        chunks.append(chunk)
    return b"".join(chunks)

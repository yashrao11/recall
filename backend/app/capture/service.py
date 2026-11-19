from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from html.parser import HTMLParser
import ipaddress
import mimetypes
import os
from pathlib import Path
import socket
import ssl
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from uuid import uuid4

from app.ai.providers import (
    LocalMetadataProvider,
    ProviderUnavailable,
    build_embedding_provider,
    build_metadata_provider,
)
from app.config import Settings
from app.db.database import get_connection, record_memory_event
from app.ingestion.indexer import index_source
from app.ingestion.scanner import SUPPORTED_EXTENSIONS


MAX_TEXT_BYTES = 5 * 1024 * 1024
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_WEB_BYTES = 2 * 1024 * 1024
WEB_TIMEOUT_SECONDS = 10
ALLOWED_WEB_CONTENT_TYPES = {"text/html", "text/plain", "application/xhtml+xml", "application/pdf"}


class CaptureError(ValueError):
    pass


@dataclass(frozen=True)
class CapturePayload:
    capture_type: str
    title: str | None = None
    content: str | None = None
    url: str | None = None
    filename: str | None = None
    mime_type: str | None = None
    data: bytes | None = None


def capture_payload(settings: Settings, payload: CapturePayload) -> dict:
    if payload.capture_type == "note":
        data, title, source_type, mime_type, capture_url = _note_data(payload)
    elif payload.capture_type == "webpage":
        data, title, source_type, mime_type, capture_url = _webpage_data(payload)
    elif payload.capture_type == "file":
        data, title, source_type, mime_type, capture_url = _file_data(payload)
    else:
        raise CaptureError("capture type must be note, webpage, or file")
    item = _persist_and_index(
        settings, data=data, title=title, source_type=source_type,
        mime_type=mime_type, capture_url=capture_url,
        event_type={"note": "note_created", "webpage": "webpage_saved", "file": "file_imported"}[payload.capture_type],
    )
    return item


def retry_capture(settings: Settings, source_id: int) -> dict:
    with get_connection(settings.database_path) as connection:
        source = connection.execute(
            "SELECT source_id, title FROM sources WHERE source_id = ?", (source_id,)
        ).fetchone()
    if source is None:
        raise LookupError("captured source not found")
    try:
        result = _index_captured_source(settings, source_id)
        return {"status": "indexed" if result["status"] in {"done", "partial"} else "error",
                "source_id": source_id, "title": source["title"],
                "indexing_status": result["status"], "indexing": result}
    except Exception as exc:
        with get_connection(settings.database_path) as connection:
            row = connection.execute(
                "SELECT indexing_status, indexing_error FROM sources WHERE source_id = ?", (source_id,)
            ).fetchone()
        return {"status": "error", "source_id": source_id, "title": source["title"],
                "indexing_status": row["indexing_status"] if row else "error",
                "error": row["indexing_error"] if row else type(exc).__name__}


def _persist_and_index(settings: Settings, *, data: bytes, title: str, source_type: str,
                       mime_type: str | None, capture_url: str | None, event_type: str) -> dict:
    if not data:
        raise CaptureError("captured content must not be empty")
    content_hash = hashlib.sha256(data).hexdigest()
    inbox = _inbox_path(settings)
    inbox.mkdir(parents=True, exist_ok=True)
    suffix = _suffix_for(source_type, title)
    stored_name = f"{uuid4().hex}{suffix}"
    destination = inbox / stored_name
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=inbox, prefix=".capture-", delete=False) as temp:
            temp.write(data)
            temp_path = Path(temp.name)
        os.replace(temp_path, destination)
        with get_connection(settings.database_path) as connection:
            duplicate = connection.execute(
                "SELECT source_id, title, indexing_status FROM sources WHERE content_hash = ? LIMIT 1",
                (content_hash,),
            ).fetchone()
            if duplicate is not None:
                destination.unlink(missing_ok=True)
                return {
                    "status": "duplicate", "source_id": duplicate["source_id"],
                    "title": duplicate["title"], "indexing_status": duplicate["indexing_status"],
                }
            mount_id = _ensure_inbox_mount(connection, inbox)
            now = datetime.now(UTC).isoformat()
            cursor = connection.execute(
                "INSERT INTO sources(mount_id, source_type, title, original_path, mime_type, content_hash, "
                "created_at, modified_at, imported_at, captured_at, capture_url, indexing_status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')",
                (mount_id, source_type, title, str(destination), mime_type, content_hash,
                 now, now, now, now, capture_url),
            )
            source_id = int(cursor.lastrowid)
            record_memory_event(
                connection, event_type, source_id=source_id, timestamp=now,
                date_basis="captured_at", metadata={
                    "source_title": title, "source_type": source_type,
                    "content_hash": content_hash, "capture_url": capture_url,
                },
            )
    except Exception:
        destination.unlink(missing_ok=True)
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise

    try:
        result = _index_captured_source(settings, source_id)
        return {
            "status": "indexed" if result["status"] in {"done", "partial"} else "error",
            "source_id": source_id,
            "title": title,
            "indexing_status": result["status"],
            "indexing": result,
        }
    except Exception as exc:  # index_source persists error status itself
        with get_connection(settings.database_path) as connection:
            row = connection.execute(
                "SELECT indexing_status, indexing_error FROM sources WHERE source_id = ?", (source_id,)
            ).fetchone()
        return {
            "status": "error", "source_id": source_id, "title": title,
            "indexing_status": row["indexing_status"] if row else "error",
            "error": row["indexing_error"] if row else type(exc).__name__,
        }


def _index_captured_source(settings: Settings, source_id: int) -> dict:
    embedding_provider = None
    metadata_provider = LocalMetadataProvider()
    if settings.allow_cloud_ai:
        try:
            embedding_provider = build_embedding_provider(settings)
        except (ProviderUnavailable, ValueError):
            embedding_provider = None
        try:
            metadata_provider = build_metadata_provider(settings)
        except ProviderUnavailable:
            metadata_provider = LocalMetadataProvider()
    return index_source(source_id, settings, embedding_provider, metadata_provider)


def _ensure_inbox_mount(connection, inbox: Path) -> int:
    row = connection.execute("SELECT mount_id FROM folder_mounts WHERE path = ?", (str(inbox),)).fetchone()
    if row is not None:
        return int(row["mount_id"])
    cursor = connection.execute(
        "INSERT INTO folder_mounts(path, label, enabled) VALUES (?, 'Recall Capture Inbox', 1)", (str(inbox),)
    )
    return int(cursor.lastrowid)


def _inbox_path(settings: Settings) -> Path:
    return (settings.data_dir / "capture-inbox").resolve()


def _note_data(payload: CapturePayload):
    content = (payload.content or "").strip()
    if not content:
        raise CaptureError("note content must not be empty")
    data = content.encode("utf-8")
    if len(data) > MAX_TEXT_BYTES:
        raise CaptureError("note exceeds the 5 MB limit")
    title = _safe_title(payload.title or _first_line(content) or "Untitled note")
    return data, title, "markdown", "text/markdown", None


def _webpage_data(payload: CapturePayload):
    if not payload.url:
        raise CaptureError("url is required for webpage capture")
    url = _validate_url(payload.url)
    body, final_url, content_type = _fetch_webpage(url)
    if content_type == "application/pdf":
        url_title = Path(unquote(urlparse(final_url).path)).stem
        title = _safe_title(payload.title or url_title or final_url)
        return body, title, "pdf", "application/pdf", final_url
    title, text = _extract_web_text(body, content_type)
    if not text.strip():
        raise CaptureError("webpage did not contain extractable text")
    combined = f"# {_safe_title(payload.title or title or final_url)}\n\nSource URL: {final_url}\n\n{text.strip()}\n"
    data = combined.encode("utf-8")
    if len(data) > MAX_TEXT_BYTES:
        raise CaptureError("webpage content exceeds the 5 MB limit")
    return data, _safe_title(payload.title or title or final_url), "webpage", "text/markdown", final_url


def _file_data(payload: CapturePayload):
    if not payload.data:
        raise CaptureError("file is required")
    if len(payload.data) > MAX_UPLOAD_BYTES:
        raise CaptureError("file exceeds the 25 MB limit")
    filename = Path(payload.filename or "").name
    suffix = Path(filename).suffix.lower()
    source_type = SUPPORTED_EXTENSIONS.get(suffix)
    if source_type is None:
        raise CaptureError("unsupported file type")
    expected = _expected_mime(source_type, suffix)
    if payload.mime_type and expected and payload.mime_type != "application/octet-stream" and not payload.mime_type.startswith(expected):
        raise CaptureError("file MIME type does not match its extension")
    return payload.data, _safe_title(Path(filename).stem or "Captured file"), source_type, payload.mime_type or mimetypes.guess_type(filename)[0], None


def _expected_mime(source_type: str, suffix: str) -> str | None:
    if source_type == "image":
        return "image/"
    return {"pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "text": "text/", "markdown": "text/"}.get(source_type)


def _suffix_for(source_type: str, title: str) -> str:
    if source_type == "pdf": return ".pdf"
    if source_type == "docx": return ".docx"
    if source_type == "pptx": return ".pptx"
    if source_type == "image": return ".png"
    return ".md"


def _safe_title(value: str) -> str:
    clean = " ".join(str(value).replace("\x00", "").split())
    return clean[:200] or "Untitled capture"


def _first_line(content: str) -> str:
    return content.splitlines()[0].lstrip("# ").strip()[:200]


def _validate_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise CaptureError("url must be an http or https address without credentials")
    _assert_public_host(parsed.hostname)
    return parsed._replace(fragment="").geturl()


def _assert_public_host(hostname: str) -> None:
    lowered = hostname.lower().rstrip(".")
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(".local"):
        raise CaptureError("private or local network addresses are not allowed")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(lowered, None)}
    except socket.gaierror as exc:
        raise CaptureError("could not resolve webpage host") from exc
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if parsed.is_private or parsed.is_loopback or parsed.is_link_local or parsed.is_multicast or parsed.is_reserved or parsed.is_unspecified:
            raise CaptureError("private or local network addresses are not allowed")


class _SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe_url = _validate_url(urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, safe_url)


def _fetch_webpage(url: str) -> tuple[bytes, str, str]:
    try:
        import certifi
        ssl_context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ssl_context = ssl.create_default_context()
    opener = build_opener(_SafeRedirectHandler, HTTPSHandler(context=ssl_context))
    request = Request(url, headers={"User-Agent": "Recall/1.0"}, method="GET")
    try:
        with opener.open(request, timeout=WEB_TIMEOUT_SECONDS) as response:
            content_type = response.headers.get_content_type()
            if content_type not in ALLOWED_WEB_CONTENT_TYPES:
                raise CaptureError("webpage content type is not supported")
            data = response.read(MAX_WEB_BYTES + 1)
            if len(data) > MAX_WEB_BYTES:
                raise CaptureError("webpage exceeds the 2 MB limit")
            return data, response.geturl(), content_type
    except (HTTPError, URLError, TimeoutError, socket.timeout) as exc:
        raise CaptureError("webpage could not be retrieved") from exc


class _ReadableHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._in_title = False
        self._ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag == "title": self._in_title = True
        if tag in {"script", "style", "noscript", "svg"}: self._ignored += 1

    def handle_endtag(self, tag):
        if tag == "title": self._in_title = False
        if tag in {"script", "style", "noscript", "svg"} and self._ignored: self._ignored -= 1

    def handle_data(self, data):
        if self._ignored: return
        clean = " ".join(data.split())
        if not clean: return
        if self._in_title: self.title_parts.append(clean)
        self.text_parts.append(clean)


def _extract_web_text(data: bytes, content_type: str) -> tuple[str, str]:
    text = data.decode("utf-8", errors="replace")
    if content_type == "text/plain":
        return "", text
    parser = _ReadableHTML()
    parser.feed(text)
    return " ".join(parser.title_parts), " ".join(parser.text_parts)

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app import capture as capture_package
from app.capture import service as capture_service
from app.config import Settings
from app.db.database import connect
from app.main import create_app


def client_for(tmp_path: Path) -> TestClient:
    return TestClient(create_app(Settings(data_dir=tmp_path / "data")))


def test_quick_note_becomes_searchable_source_and_calendar_event(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        response = client.post("/capture", json={
            "type": "note", "title": "Aurora note",
            "content": "Project Aurora uses a blue indicator and a 12 volt supply.",
        })
        assert response.status_code == 201
        item = response.json()["items"][0]
        assert item["status"] == "indexed"
        source_id = item["source_id"]
        search = client.get("/search", params={"q": "blue indicator", "mode": "keyword"})
        assert search.status_code == 200
        assert any(result["source_id"] == source_id for result in search.json()["results"])
        with connect(tmp_path / "data" / "recall.sqlite3") as connection:
            source = connection.execute("SELECT source_type, captured_at, capture_url FROM sources WHERE source_id = ?", (source_id,)).fetchone()
            events = connection.execute("SELECT event_type FROM memory_events WHERE source_id = ?", (source_id,)).fetchall()
        assert source["source_type"] == "markdown"
        assert source["captured_at"]
        assert source["capture_url"] is None
        assert [event["event_type"] for event in events] == ["note_created"]


def test_unit_metadata_exposes_filename_without_absolute_source_path(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        captured = client.post("/capture", json={
            "type": "note", "title": "Private path check", "content": "Safe indexed content.",
        }).json()["items"][0]
        with connect(tmp_path / "data" / "recall.sqlite3") as connection:
            unit_id = connection.execute(
                "SELECT unit_id FROM document_units WHERE source_id = ?", (captured["source_id"],)
            ).fetchone()[0]
        unit = client.get(f"/units/{unit_id}")

    assert unit.status_code == 200
    payload = unit.json()
    assert payload["filename"]
    assert "original_path" not in payload


def test_duplicate_capture_is_idempotent(tmp_path: Path) -> None:
    payload = {"type": "note", "content": "An idempotent captured note."}
    with client_for(tmp_path) as client:
        first = client.post("/capture", json=payload).json()["items"][0]
        second = client.post("/capture", json=payload).json()["items"][0]
    assert first["status"] == "indexed"
    assert second["status"] == "duplicate"
    assert second["source_id"] == first["source_id"]


def test_multiple_file_capture_reports_per_file_results(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        response = client.post(
            "/capture",
            data={"type": "file"},
            files=[
                ("files", ("notes.txt", b"A supported text capture.", "text/plain")),
                ("files", ("virus.exe", b"not supported", "application/octet-stream")),
            ],
        )
    assert response.status_code == 201
    items = response.json()["items"]
    assert [item["status"] for item in items] == ["indexed", "rejected"]
    assert items[1]["filename"] == "virus.exe"


def test_upload_rejects_mismatched_mime_and_empty_file(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        mismatch = client.post(
            "/capture", data={"type": "file"},
            files=[("files", ("notes.txt", b"text", "application/pdf"))],
        )
        empty = client.post(
            "/capture", data={"type": "file"},
            files=[("files", ("notes.txt", b"", "text/plain"))],
        )
    assert mismatch.status_code == 400
    assert mismatch.json()["items"][0]["status"] == "rejected"
    assert empty.status_code == 400
    assert empty.json()["items"][0]["status"] == "rejected"


def test_webpage_capture_preserves_original_url_without_network_call(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        capture_service, "_fetch_webpage",
        lambda url: (b"<html><title>Recall page</title><body>Local page content about retrieval.</body></html>", url, "text/html"),
    )
    with client_for(tmp_path) as client:
        response = client.post("/capture", json={"type": "webpage", "url": "https://example.com/recall"})
    assert response.status_code == 201
    source_id = response.json()["items"][0]["source_id"]
    with connect(tmp_path / "data" / "recall.sqlite3") as connection:
        source = connection.execute("SELECT source_type, capture_url FROM sources WHERE source_id = ?", (source_id,)).fetchone()
        unit = connection.execute("SELECT extracted_text FROM document_units WHERE source_id = ?", (source_id,)).fetchone()
    assert source["source_type"] == "webpage"
    assert source["capture_url"] == "https://example.com/recall"
    assert "Local page content" in unit["extracted_text"]


def test_webpage_capture_rejects_private_destination(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        response = client.post("/capture", json={"type": "webpage", "url": "http://127.0.0.1:8000/private"})
    assert response.status_code == 400
    assert "private" in response.json()["detail"]


def test_malformed_capture_request_is_rejected_without_a_source(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        response = client.post("/capture", content="{not-json", headers={"content-type": "application/json"})
    assert response.status_code == 422


def test_invalid_pdf_is_persisted_as_retryable_error(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        response = client.post(
            "/capture", data={"type": "file"},
            files=[("files", ("broken.pdf", b"not a pdf", "application/pdf"))],
        )
    assert response.status_code == 201
    item = response.json()["items"][0]
    assert item["status"] == "error"
    with connect(tmp_path / "data" / "recall.sqlite3") as connection:
        row = connection.execute("SELECT indexing_status, indexing_error FROM sources WHERE source_id = ?", (item["source_id"],)).fetchone()
    assert row["indexing_status"] == "error"
    assert row["indexing_error"]


def test_retry_reindexes_a_captured_source(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        captured = client.post("/capture", json={"type": "note", "content": "Retryable note content."}).json()["items"][0]
        source_id = captured["source_id"]
        with connect(tmp_path / "data" / "recall.sqlite3") as connection:
            connection.execute("UPDATE sources SET indexing_status = 'error', indexing_error = 'synthetic failure' WHERE source_id = ?", (source_id,))
            connection.commit()
        retried = client.post(f"/capture/{source_id}/retry")
    assert retried.status_code == 200
    assert retried.json()["status"] == "indexed"
    with connect(tmp_path / "data" / "recall.sqlite3") as connection:
        assert connection.execute("SELECT indexing_status FROM sources WHERE source_id = ?", (source_id,)).fetchone()[0] == "partial"

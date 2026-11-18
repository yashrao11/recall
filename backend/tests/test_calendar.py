from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.db.database import get_connection, initialize_database, record_memory_event
from app.ingestion.indexer import index_source
from app.ingestion.scanner import sync_mount
from app.main import create_app


class FakeEmbeddingProvider:
    name = "fake"
    dimension = 3

    def embed_text(self, text, content_hash, connection):
        return [1.0, 0.0, 0.0]

    def embed_image(self, image_bytes, mime_type, content_hash, connection):
        return [0.0, 1.0, 0.0]


def create_source(tmp_path: Path):
    data_dir = tmp_path / "data"
    source_path = tmp_path / "memory.md"
    source_path.write_text("calendar memory", encoding="utf-8")
    initialize_database(data_dir / "recall.sqlite3")
    with get_connection(data_dir / "recall.sqlite3") as connection:
        mount_id = connection.execute(
            "INSERT INTO folder_mounts(path, label) VALUES (?, ?)", (str(tmp_path), "calendar")
        ).lastrowid
        source_id = connection.execute(
            "INSERT INTO sources(mount_id, source_type, title, original_path, content_hash) "
            "VALUES (?, 'markdown', ?, ?, 'calendar-hash')",
            (mount_id, source_path.name, str(source_path)),
        ).lastrowid
    return Settings(data_dir=data_dir, allow_cloud_ai=False), source_path, int(source_id), int(mount_id)


def add_controlled_events(settings: Settings) -> None:
    with get_connection(settings.database_path) as connection:
        record_memory_event(
            connection,
            "note_created",
            timestamp="2026-11-01T23:30:00Z",
            date_basis="created_at",
            metadata={"source_title": "Late note"},
        )
        record_memory_event(
            connection,
            "file_imported",
            timestamp="2026-11-02T00:30:00Z",
            date_basis="imported_at",
            metadata={"source_title": "Imported PDF", "source_type": "pdf"},
        )
        record_memory_event(
            connection,
            "webpage_saved",
            timestamp="2024-11-18T10:00:00Z",
            date_basis="captured_at",
            metadata={"source_title": "Historical page"},
        )
        record_memory_event(
            connection,
            "webpage_saved",
            timestamp="2026-11-18T10:00:00Z",
            date_basis="captured_at",
            metadata={"source_title": "Current page"},
        )
        record_memory_event(
            connection,
            "study_session_completed",
            timestamp="2026-11-17T15:00:00Z",
            date_basis="study_at",
            metadata={"questions": 5},
        )


def test_successful_import_creates_event_and_unchanged_scan_does_not_duplicate(tmp_path: Path) -> None:
    settings, source_path, source_id, mount_id = create_source(tmp_path)
    with get_connection(settings.database_path) as connection:
        sync_mount(connection, mount_id, tmp_path)
    index_source(source_id, settings, FakeEmbeddingProvider())
    with get_connection(settings.database_path) as connection:
        assert sync_mount(connection, mount_id, tmp_path) == {
            "added": 0, "changed": 0, "unchanged": 1, "removed": 0
        }
    index_source(source_id, settings, FakeEmbeddingProvider())
    with get_connection(settings.database_path) as connection:
        events = connection.execute(
            "SELECT event_type, date_basis, metadata_json FROM memory_events WHERE source_id = ?",
            (source_id,),
        ).fetchall()
    assert len(events) == 1
    assert events[0]["event_type"] == "file_imported"
    assert events[0]["date_basis"] == "imported_at"
    assert source_path.exists()


def test_successful_modified_reindex_creates_file_modified_event(tmp_path: Path) -> None:
    settings, source_path, source_id, mount_id = create_source(tmp_path)
    with get_connection(settings.database_path) as connection:
        sync_mount(connection, mount_id, tmp_path)
    index_source(source_id, settings, FakeEmbeddingProvider())
    source_path.write_text("calendar memory changed", encoding="utf-8")
    with get_connection(settings.database_path) as connection:
        sync_mount(connection, mount_id, tmp_path)
        connection.execute(
            "UPDATE sources SET modified_at = ? WHERE source_id = ?",
            ("2026-11-18T12:00:00+00:00", source_id),
        )
    index_source(source_id, settings, FakeEmbeddingProvider())
    with get_connection(settings.database_path) as connection:
        events = connection.execute(
            "SELECT event_type, timestamp, date_basis FROM memory_events "
            "WHERE source_id = ? ORDER BY event_id", (source_id,)
        ).fetchall()
    assert [event["event_type"] for event in events] == ["file_imported", "file_modified"]
    assert events[1]["timestamp"] == "2026-11-18T12:00:00+00:00"
    assert events[1]["date_basis"] == "modified_at"


def test_calendar_uses_utc_storage_and_user_local_dates(tmp_path: Path) -> None:
    settings, _, _, _ = create_source(tmp_path)
    add_controlled_events(settings)
    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/calendar", params={"view": "month", "date": "2026-11", "timezone": "Asia/Kolkata"}
        )
    payload = response.json()
    assert response.status_code == 200
    assert payload["date"] == "2026-11"
    assert {day["date"] for day in payload["days"]} == {"2026-11-02", "2026-11-17", "2026-11-18"}
    assert payload["statistics"]["memory_count"] == 4


def test_month_week_day_grouping_and_pagination(tmp_path: Path) -> None:
    settings, _, _, _ = create_source(tmp_path)
    add_controlled_events(settings)
    with get_connection(settings.database_path) as connection:
        record_memory_event(connection, "note_created", timestamp="2026-11-18T11:00:00Z")
    with TestClient(create_app(settings)) as client:
        month = client.get("/calendar", params={"view": "month", "date": "2026-11"}).json()
        week = client.get("/calendar", params={"view": "week", "date": "2026-11-18"}).json()
        day = client.get("/calendar", params={"view": "day", "date": "2026-11-18", "limit": 1}).json()
    assert month["total"] == 5
    assert week["total"] == 3
    assert day["total"] == 2
    assert len(day["events"]) == 1
    assert day["has_more"] is True


def test_on_this_day_excludes_current_date_and_recap_uses_actual_statistics(tmp_path: Path) -> None:
    settings, _, _, _ = create_source(tmp_path)
    add_controlled_events(settings)
    with TestClient(create_app(settings)) as client:
        historical = client.get(
            "/calendar/on-this-day", params={"date": "2026-11-18"}
        ).json()
        recap = client.get(
            "/calendar/recap", params={"period": "week", "date": "2026-11-18"}
        ).json()
    assert historical["total"] == 1
    assert historical["events"][0]["title"] == "Historical page"
    assert recap["total_events"] == 2
    assert recap["statistics"]["study_sessions"] == 1
    assert recap["statistics"]["saved_webpages"] == 1


def test_deleted_source_event_is_cascaded_and_empty_calendar_is_truthful(tmp_path: Path) -> None:
    settings, _, source_id, _ = create_source(tmp_path)
    with get_connection(settings.database_path) as connection:
        record_memory_event(
            connection,
            "file_imported",
            source_id=source_id,
            timestamp="2026-11-18T10:00:00Z",
            metadata={"source_title": "Deleted source"},
        )
    with TestClient(create_app(settings)) as client:
        assert client.delete(f"/sources/{source_id}").status_code == 204
        payload = client.get("/calendar", params={"view": "day", "date": "2026-11-18"}).json()
    assert payload["total"] == 0
    assert payload["events"] == []
    assert payload["statistics"]["memory_count"] == 0

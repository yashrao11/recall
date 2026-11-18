from pathlib import Path

from app.db.database import get_connection, initialize_database
from app.ingestion.scanner import discover_files, sync_mount


def test_discover_files_hashes_supported_files_only(tmp_path: Path) -> None:
    document = tmp_path / "lesson.md"
    document.write_text("attention", encoding="utf-8")
    (tmp_path / "ignored.zip").write_bytes(b"not indexed")

    files = discover_files(tmp_path)

    assert len(files) == 1
    assert files[0].source_type == "markdown"
    assert len(files[0].content_hash) == 64


def test_sync_mount_is_incremental(tmp_path: Path) -> None:
    database_path = tmp_path / "recall.sqlite3"
    initialize_database(database_path)
    document = tmp_path / "lesson.txt"
    document.write_text("first", encoding="utf-8")

    with get_connection(database_path) as connection:
        mount_id = connection.execute(
            "INSERT INTO folder_mounts(path, label) VALUES (?, ?)", (str(tmp_path), "test")
        ).lastrowid
        assert sync_mount(connection, int(mount_id), tmp_path) == {
            "added": 1, "changed": 0, "unchanged": 0, "removed": 0
        }
        assert sync_mount(connection, int(mount_id), tmp_path) == {
            "added": 0, "changed": 0, "unchanged": 1, "removed": 0
        }
        document.write_text("second", encoding="utf-8")
        assert sync_mount(connection, int(mount_id), tmp_path)["changed"] == 1


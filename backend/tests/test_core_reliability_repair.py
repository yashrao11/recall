from pathlib import Path

from fastapi.testclient import TestClient

from app.ai.providers import LocalMetadataProvider
from app.config import Settings
from app.db.database import get_connection, initialize_database
from app.ingestion import extractor
from app.ingestion.indexer import index_source
from app.ingestion.scanner import sha256_file, sync_mount
from app.main import create_app
from app.rag import pipeline


class FakeEmbeddingProvider:
    name = "fake"
    dimension = 3
    supports_visual = True

    def embed_text(self, text, content_hash, connection):
        return [1.0, 0.0, 0.0]

    def embed_image(self, image_bytes, mime_type, content_hash, connection):
        return [0.0, 1.0, 0.0]


class FakeGenerationProvider:
    def generate(self, prompt, content_hash, connection, *, system_instruction=None):
        return '{"answer":"The document uses cross validation [1].","citations":[1]}'


class FakeMetadataProvider:
    name = "fake-metadata"

    def describe(self, text, image_bytes, mime_type, content_hash, connection):
        return "OCR evidence", ["cross-validation"]


def _settings_and_source(tmp_path: Path, filename: str, content: bytes, source_type: str = "text"):
    data_dir = tmp_path / "data"
    database_path = data_dir / "recall.sqlite3"
    initialize_database(database_path)
    source_path = tmp_path / filename
    source_path.write_bytes(content)
    with get_connection(database_path) as connection:
        mount_id = connection.execute(
            "INSERT INTO folder_mounts(path, label) VALUES (?, 'repair')", (str(tmp_path),)
        ).lastrowid
        source_id = connection.execute(
            "INSERT INTO sources(mount_id, source_type, title, original_path, content_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (mount_id, source_type, filename, str(source_path), sha256_file(source_path)),
        ).lastrowid
    return Settings(data_dir=data_dir, allow_cloud_ai=False), source_path, int(source_id), int(mount_id)


def test_image_ocr_text_is_indexed_for_keyword_and_rag(tmp_path: Path, monkeypatch) -> None:
    settings, _, source_id, _ = _settings_and_source(
        tmp_path, "cv.png", b"synthetic image bytes", "image"
    )
    monkeypatch.setattr(extractor, "_ocr_image", lambda image: "Cross validation checks model generalization.")
    result = index_source(source_id, settings, FakeEmbeddingProvider(), FakeMetadataProvider())

    assert result["status"] == "done"
    with get_connection(settings.database_path) as connection:
        row = connection.execute(
            "SELECT extracted_text FROM document_units WHERE source_id = ?", (source_id,)
        ).fetchone()
        assert row["extracted_text"] == "Cross validation checks model generalization."
        search = connection.execute(
            "SELECT COUNT(*) FROM fts_units WHERE fts_units MATCH 'cross AND validation'"
        ).fetchone()[0]
    assert search == 1

    response = pipeline.ask_question(
        settings,
        "What checks model generalization?",
        generation_provider=FakeGenerationProvider(),
    )
    assert response["status"] == "answered"
    assert response["citations"][0]["source_id"] == source_id


def test_missing_scanned_file_is_excluded_from_search_but_retained_for_recovery(tmp_path: Path) -> None:
    settings, source_path, source_id, mount_id = _settings_and_source(
        tmp_path, "lesson.txt", b"retrievable lesson", "text"
    )
    index_source(source_id, settings, None, LocalMetadataProvider())
    source_path.unlink()
    with get_connection(settings.database_path) as connection:
        summary = sync_mount(connection, mount_id, tmp_path, settings)
        row = connection.execute(
            "SELECT indexing_status, excluded, indexing_error FROM sources WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        assert summary["removed"] == 1
        assert row["indexing_status"] == "pending"
        assert row["excluded"] == 1
        assert row["indexing_error"] == "file no longer exists"
        assert connection.execute(
            "SELECT COUNT(*) FROM fts_units WHERE source_id = ?", (source_id,)
        ).fetchone()[0] == 0


def test_missing_file_reappears_and_is_eligible_for_rescan(tmp_path: Path) -> None:
    settings, source_path, source_id, mount_id = _settings_and_source(
        tmp_path, "restore.txt", b"restore me", "text"
    )
    index_source(source_id, settings, None, LocalMetadataProvider())
    source_path.unlink()
    with get_connection(settings.database_path) as connection:
        sync_mount(connection, mount_id, tmp_path, settings)
    source_path.write_bytes(b"restore me")
    with get_connection(settings.database_path) as connection:
        summary = sync_mount(connection, mount_id, tmp_path, settings)
        row = connection.execute(
            "SELECT indexing_status, excluded FROM sources WHERE source_id = ?", (source_id,)
        ).fetchone()
    assert summary["unchanged"] == 1
    assert row["indexing_status"] == "pending"
    assert row["excluded"] == 0


def test_folder_mount_runs_only_its_sources_and_returns_real_statuses(tmp_path: Path) -> None:
    folder = tmp_path / "Nested Folder"
    folder.mkdir()
    (folder / "unicode résumé.md").write_text("folder retrieval content", encoding="utf-8")
    (folder / ".hidden.md").write_text("must not be indexed", encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", allow_cloud_ai=False)
    with TestClient(create_app(settings)) as client:
        mounted = client.post("/sources/folders", json={"path": str(folder)})
        assert mounted.status_code == 201
        mount_id = mounted.json()["mount_id"]
        indexing = client.post("/index/run", params={"mount_id": mount_id})
        assert indexing.status_code == 200
        inventory = client.get("/sources", params={"mount_id": mount_id}).json()

    assert len(inventory["sources"]) == 1
    assert inventory["sources"][0]["filename"] == "unicode résumé.md"
    assert inventory["sources"][0]["indexing_status"] == "partial"


def test_existing_folder_can_be_rescanned_without_duplicate_mount(tmp_path: Path) -> None:
    folder = tmp_path / "rescanned"
    folder.mkdir()
    (folder / "first.txt").write_text("first", encoding="utf-8")
    settings = Settings(data_dir=tmp_path / "data", allow_cloud_ai=False)
    with TestClient(create_app(settings)) as client:
        mounted = client.post("/sources/folders", json={"path": str(folder)})
        assert mounted.status_code == 201
        (folder / "second.txt").write_text("second", encoding="utf-8")

        rescanned = client.post("/sources/folders/rescan", json={"path": str(folder)})

    assert rescanned.status_code == 200
    assert rescanned.json()["scan_summary"]["added"] == 1
    assert rescanned.json()["indexing_status"] == "pending"


def test_finder_reveal_uses_validated_mounted_path(tmp_path: Path, monkeypatch) -> None:
    settings, source_path, source_id, _ = _settings_and_source(
        tmp_path, "notes with spaces.txt", b"finder", "text"
    )
    calls = []
    monkeypatch.setattr("app.api.sources.sys.platform", "darwin")
    monkeypatch.setattr(
        "app.api.sources.subprocess.run",
        lambda args, **kwargs: calls.append((args, kwargs)),
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(f"/sources/{source_id}/reveal")

    assert response.status_code == 200
    assert response.json() == {"status": "revealed", "source_id": source_id}
    assert calls[0][0] == ["open", "-R", str(source_path.resolve())]
    assert calls[0][1]["check"] is True
    assert calls[0][1]["timeout"] == 10


from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.config import Settings
from app.db.database import get_connection, initialize_database
from app.ingestion import indexer
from app.ingestion.extractor import ExtractedUnit
from app.ingestion.indexer import index_source
from app.ingestion.scanner import sync_mount
from app.main import create_app
from app.retrieval.vector_store import FaissVectorStore


class FakeEmbeddingProvider:
    name = "fake"
    dimension = 3

    def embed_text(self, text, content_hash, connection):
        return [1.0, 0.0, 0.0] if "old" in text else [0.0, 1.0, 0.0]

    def embed_image(self, image_bytes, mime_type, content_hash, connection):
        return [0.0, 0.0, 1.0]


class FakeMetadataProvider:
    name = "fake-metadata"

    def describe(self, text, image_bytes, mime_type, content_hash, connection):
        return "indexed comment", ["lifecycle"]


def create_source(tmp_path: Path, filename: str = "lesson.md", source_type: str = "markdown"):
    data_dir = tmp_path / "data"
    source_path = tmp_path / filename
    source_path.write_text("old content", encoding="utf-8")
    database_path = data_dir / "recall.sqlite3"
    initialize_database(database_path)
    with get_connection(database_path) as connection:
        mount_id = connection.execute(
            "INSERT INTO folder_mounts(path, label) VALUES (?, ?)", (str(tmp_path), "lifecycle")
        ).lastrowid
        source_id = connection.execute(
            "INSERT INTO sources(mount_id, source_type, title, original_path, content_hash) "
            "VALUES (?, ?, ?, ?, 'old-hash')",
            (mount_id, source_type, filename, str(source_path)),
        ).lastrowid
    return Settings(data_dir=data_dir, embedding_provider="bge"), source_path, int(source_id), int(mount_id)


def test_numpy_vector_store_persists_reloads_replaces_and_deletes(tmp_path: Path) -> None:
    store_path = tmp_path / "text.index"
    first = FaissVectorStore(store_path, 2)
    if first._faiss is not None:
        return  # FAISS is optional; this test targets the NumPy fallback.
    with pytest.raises(ValueError, match="dimension mismatch"):
        first.add(99, [1.0])
    first.add(41, [1.0, 0.0])
    first.persist()

    restarted = FaissVectorStore(store_path, 2)
    assert restarted.search([1.0, 0.0]) == [(41, 1.0)]
    restarted.add(41, [0.0, 1.0])
    restarted.persist()

    replaced = FaissVectorStore(store_path, 2)
    assert replaced.search([1.0, 0.0]) == [(41, 0.0)]
    assert replaced.search([0.0, 1.0]) == [(41, 1.0)]
    replaced.remove(41)
    replaced.persist()
    assert FaissVectorStore(store_path, 2).search([0.0, 1.0]) == []


def test_reindex_modified_file_removes_old_units_fts_vectors_and_keeps_latest(tmp_path: Path) -> None:
    settings, source_path, source_id, mount_id = create_source(tmp_path)
    index_source(source_id, settings, FakeEmbeddingProvider(), FakeMetadataProvider())
    with get_connection(settings.database_path) as connection:
        old_unit_id = connection.execute(
            "SELECT unit_id FROM document_units WHERE source_id = ?", (source_id,)
        ).fetchone()[0]

    source_path.write_text("new content", encoding="utf-8")
    with get_connection(settings.database_path) as connection:
        summary = sync_mount(connection, mount_id, tmp_path)
    assert summary["changed"] == 1
    result = index_source(source_id, settings, FakeEmbeddingProvider(), FakeMetadataProvider())
    assert result["status"] == "done"

    with get_connection(settings.database_path) as connection:
        units = connection.execute(
            "SELECT unit_id, extracted_text FROM document_units WHERE source_id = ?", (source_id,)
        ).fetchall()
        assert len(units) == 1
        assert units[0]["unit_id"] != old_unit_id
        assert units[0]["extracted_text"] == "new content"
        assert connection.execute(
            "SELECT COUNT(*) FROM fts_units WHERE source_id = ? AND extracted_text = 'old content'",
            (source_id,),
        ).fetchone()[0] == 0

    store = FaissVectorStore(settings.data_dir / "text.index", 3)
    assert all(unit_id != old_unit_id for unit_id, _ in store.search([1.0, 0.0, 0.0]))
    assert store.search([0.0, 1.0, 0.0])[0][0] == units[0]["unit_id"]


def test_reindex_removes_old_thumbnail_files(tmp_path: Path, monkeypatch) -> None:
    settings, source_path, source_id, _, = create_source(tmp_path, "slide.png", "image")

    def extract(path, source_type):
        payload = path.read_bytes()
        return [ExtractedUnit("image", None, payload.decode(), payload, "image/png")]

    monkeypatch.setattr(indexer, "extract_file", extract)
    index_source(source_id, settings, FakeEmbeddingProvider(), FakeMetadataProvider())
    thumbnail_dir = settings.derived_dir / "thumbnails" / str(source_id)
    old_thumbnails = list(thumbnail_dir.iterdir())
    assert old_thumbnails

    source_path.write_text("new slide", encoding="utf-8")
    with get_connection(settings.database_path) as connection:
        mount_id = connection.execute("SELECT mount_id FROM sources WHERE source_id = ?", (source_id,)).fetchone()[0]
        sync_mount(connection, mount_id, tmp_path)
    index_source(source_id, settings, FakeEmbeddingProvider(), FakeMetadataProvider())
    assert all(not path.exists() for path in old_thumbnails)


def test_missing_scanned_file_is_removed_from_searchable_derived_data(tmp_path: Path) -> None:
    settings, source_path, source_id, mount_id = create_source(tmp_path)
    index_source(source_id, settings, FakeEmbeddingProvider(), FakeMetadataProvider())
    source_path.unlink()
    with get_connection(settings.database_path) as connection:
        summary = sync_mount(connection, mount_id, tmp_path, settings)
        assert summary["removed"] == 1
        assert connection.execute("SELECT COUNT(*) FROM document_units").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM fts_units").fetchone()[0] == 0
        assert connection.execute(
            "SELECT indexing_status FROM sources WHERE source_id = ?", (source_id,)
        ).fetchone()[0] == "pending"


def test_source_delete_removes_cascades_vectors_thumbnails_and_records(tmp_path: Path, monkeypatch) -> None:
    settings, source_path, source_id, _ = create_source(tmp_path, "slide.png", "image")
    monkeypatch.setattr(
        indexer,
        "extract_file",
        lambda path, source_type: [ExtractedUnit("image", None, "slide", b"image", "image/png")],
    )
    index_source(source_id, settings, FakeEmbeddingProvider(), FakeMetadataProvider())
    with get_connection(settings.database_path) as connection:
        connection.execute(
            "INSERT INTO api_cache(cache_key, content_hash, provider, operation, response_json) "
            "VALUES ('delete-check', 'old-hash', 'fake', 'test', '{}')"
        )
        connection.execute(
            "INSERT INTO memory_events(event_type, source_id, date_basis) VALUES ('capture', ?, 'created_at')",
            (source_id,),
        )
        unit_id = connection.execute(
            "SELECT unit_id FROM document_units WHERE source_id = ?", (source_id,)
        ).fetchone()[0]
    thumbnail_dir = settings.derived_dir / "thumbnails" / str(source_id)
    assert list(thumbnail_dir.iterdir())

    with TestClient(create_app(settings)) as client:
        assert client.delete(f"/sources/{source_id}").status_code == 204

    with get_connection(settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sources WHERE source_id = ?", (source_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM document_units WHERE unit_id = ?", (unit_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM fts_units WHERE source_id = ?", (source_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM memory_events WHERE source_id = ?", (source_id,)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0] == 0
    assert not thumbnail_dir.exists()
    assert FaissVectorStore(settings.data_dir / "text.index", 3).search([1.0, 0.0, 0.0]) == []
    assert FaissVectorStore(settings.data_dir / "visual.index", 3).search([0.0, 0.0, 1.0]) == []

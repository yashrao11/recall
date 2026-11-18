from pathlib import Path

import pytest

from app.ai.providers import LocalMetadataProvider, ProviderUnavailable
from app.config import Settings
from app.db.database import get_connection, initialize_database
from app.ingestion import indexer
from app.ingestion.extractor import ExtractedUnit
from app.ingestion.indexer import index_source


class FakeEmbeddingProvider:
    name = "fake"
    dimension = 3

    def __init__(self, text_error: Exception | None = None, image_error: Exception | None = None):
        self.text_error = text_error
        self.image_error = image_error

    def embed_text(self, text, content_hash, connection):
        if self.text_error:
            raise self.text_error
        return [1.0, 0.0, 0.0]

    def embed_image(self, image_bytes, mime_type, content_hash, connection):
        if self.image_error:
            raise self.image_error
        return [0.0, 1.0, 0.0]


class FakeMetadataProvider:
    name = "fake-metadata"

    def describe(self, text, image_bytes, mime_type, content_hash, connection):
        return "A generated comment", ["generated", "test"]


class FailingMetadataProvider:
    name = "gemini"

    def describe(self, text, image_bytes, mime_type, content_hash, connection):
        raise ProviderUnavailable("metadata provider unavailable")


def make_source(tmp_path: Path, source_type: str = "markdown", content: str = "Recall indexing test"):
    data_dir = tmp_path / "data"
    source_path = tmp_path / ("image.png" if source_type == "image" else "notes.md")
    if source_type == "image":
        source_path.write_bytes(b"not-a-real-image-but-an-extracted-payload")
    else:
        source_path.write_text(content, encoding="utf-8")
    initialize_database(data_dir / "recall.sqlite3")
    with get_connection(data_dir / "recall.sqlite3") as connection:
        mount_id = connection.execute(
            "INSERT INTO folder_mounts(path, label) VALUES (?, ?)", (str(tmp_path), "test")
        ).lastrowid
        source_id = connection.execute(
            "INSERT INTO sources(mount_id, source_type, title, original_path, content_hash) "
            "VALUES (?, ?, ?, ?, 'hash')",
            (mount_id, source_type, source_path.name, str(source_path)),
        ).lastrowid
    return Settings(data_dir=data_dir, allow_cloud_ai=False), int(source_id)


def source_status(settings: Settings, source_id: int):
    with get_connection(settings.database_path) as connection:
        return connection.execute(
            "SELECT indexing_status, indexing_error FROM sources WHERE source_id = ?", (source_id,)
        ).fetchone()


def test_complete_indexing_is_done(tmp_path: Path) -> None:
    settings, source_id = make_source(tmp_path)
    result = index_source(source_id, settings, FakeEmbeddingProvider(), FakeMetadataProvider())
    assert result["status"] == "done"
    assert result["units_created"] == 1
    assert result["text_embeddings_created"] == 1
    assert result["metadata_fallbacks"] == 0
    assert source_status(settings, source_id)["indexing_status"] == "done"


def test_cloud_disabled_indexing_is_partial_and_preserves_fts(tmp_path: Path) -> None:
    settings, source_id = make_source(tmp_path)
    result = index_source(source_id, settings, None, LocalMetadataProvider())
    assert result["status"] == "partial"
    assert result["text_embeddings_created"] == 0
    assert result["metadata_fallbacks"] == 1
    assert any("cloud AI is disabled" in warning for warning in result["warnings"])
    with get_connection(settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM document_units").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM fts_units").fetchone()[0] == 1
    assert source_status(settings, source_id)["indexing_status"] == "partial"


def test_metadata_fallback_is_reported(tmp_path: Path) -> None:
    settings, source_id = make_source(tmp_path)
    result = index_source(source_id, settings, FakeEmbeddingProvider(), FailingMetadataProvider())
    assert result["status"] == "partial"
    assert result["metadata_fallbacks"] == 1
    assert any("metadata provider unavailable" in warning for warning in result["warnings"])


def test_text_embedding_failure_is_partial_with_error(tmp_path: Path) -> None:
    settings, source_id = make_source(tmp_path)
    result = index_source(
        source_id, settings, FakeEmbeddingProvider(ProviderUnavailable("text failed")), FakeMetadataProvider()
    )
    assert result["status"] == "partial"
    assert result["text_embeddings_created"] == 0
    assert any("Text embedding failed" in error for error in result["errors"])
    assert source_status(settings, source_id)["indexing_status"] == "partial"


def test_visual_embedding_failure_is_partial_with_error(tmp_path: Path, monkeypatch) -> None:
    settings, source_id = make_source(tmp_path, source_type="image")
    monkeypatch.setattr(
        indexer,
        "extract_file",
        lambda path, source_type: [ExtractedUnit("image", None, "visual text", b"image", "image/png")],
    )
    result = index_source(
        source_id, settings, FakeEmbeddingProvider(image_error=ProviderUnavailable("image failed")), FakeMetadataProvider()
    )
    assert result["status"] == "partial"
    assert result["text_embeddings_created"] == 1
    assert result["visual_embeddings_created"] == 0
    assert any("Visual embedding failed" in error for error in result["errors"])


def test_core_extraction_failure_persists_error_status(tmp_path: Path, monkeypatch) -> None:
    settings, source_id = make_source(tmp_path)

    def fail_extract(path, source_type):
        raise RuntimeError("extractor failed")

    monkeypatch.setattr(indexer, "extract_file", fail_extract)
    with pytest.raises(RuntimeError, match="extractor failed"):
        index_source(source_id, settings, FakeEmbeddingProvider(), FakeMetadataProvider())
    row = source_status(settings, source_id)
    assert row["indexing_status"] == "error"
    assert "extractor failed" in row["indexing_error"]

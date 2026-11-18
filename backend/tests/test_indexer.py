from pathlib import Path

from app.config import Settings
from app.db.database import get_connection, initialize_database
from app.ingestion.indexer import index_source


class FakeEmbeddingProvider:
    name = "fake"
    dimension = 3

    def embed_text(self, text, content_hash, connection):
        return [1.0, 0.0, 0.0]

    def embed_image(self, image_bytes, mime_type, content_hash, connection):
        return [0.0, 1.0, 0.0]


def test_indexer_writes_units_metadata_and_fts(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    source_path = tmp_path / "notes.md"
    source_path.write_text("Transformers use attention for context.", encoding="utf-8")
    database_path = data_dir / "recall.sqlite3"
    initialize_database(database_path)

    with get_connection(database_path) as connection:
        mount_id = connection.execute(
            "INSERT INTO folder_mounts(path, label) VALUES (?, ?)", (str(tmp_path), "sample")
        ).lastrowid
        source_id = connection.execute(
            "INSERT INTO sources(mount_id, source_type, title, original_path, content_hash) "
            "VALUES (?, 'markdown', ?, ?, 'hash')",
            (mount_id, source_path.name, str(source_path)),
        ).lastrowid

    result = index_source(
        int(source_id), Settings(data_dir=data_dir), FakeEmbeddingProvider()
    )
    assert result["units"] == 1

    with get_connection(database_path) as connection:
        unit = connection.execute("SELECT * FROM document_units").fetchone()
        fts = connection.execute(
            "SELECT extracted_text, short_comment, tags FROM fts_units WHERE unit_id = ?",
            (unit["unit_id"],),
        ).fetchone()
        assert unit["text_embedding_ref"] == f"fake:{unit['unit_id']}"
        assert fts["extracted_text"]
        assert fts["short_comment"]
        assert fts["tags"]


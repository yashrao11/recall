from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.db.database import get_connection, initialize_database
from app.ingestion.indexer import index_source
from app.main import create_app


def build_indexed_app(tmp_path: Path):
    data_dir = tmp_path / "data"
    source_path = tmp_path / "attention.md"
    source_path.write_text("Self-attention connects tokens across a sequence.", encoding="utf-8")
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
    settings = Settings(data_dir=data_dir, embedding_provider="bge")
    index_source(int(source_id), settings)
    return create_app(settings), int(source_id), source_path


def test_keyword_search_sanitises_fts_operators(tmp_path: Path) -> None:
    app, _, _ = build_indexed_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/search", params={"q": 'attention OR * --', "mode": "keyword"})
        assert response.status_code == 200
        assert response.json()["results"][0]["title"] == "attention.md"


def test_keyword_search_finds_filename_metadata(tmp_path: Path) -> None:
    app, _, _ = build_indexed_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/search", params={"q": "my md", "mode": "keyword"})
    assert response.status_code == 200
    assert response.json()["results"]
    assert response.json()["results"][0]["match_type"] == ["filename"]


def test_source_file_and_delete_remove_indexed_content(tmp_path: Path) -> None:
    app, source_id, source_path = build_indexed_app(tmp_path)
    with TestClient(app) as client:
        file_response = client.get(f"/sources/{source_id}/file")
        assert file_response.status_code == 200
        assert file_response.content == source_path.read_bytes()
        assert client.delete(f"/sources/{source_id}").status_code == 204
        assert client.get("/search", params={"q": "attention", "mode": "keyword"}).json()["total"] == 0


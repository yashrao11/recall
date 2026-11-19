from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(data_dir=tmp_path / "data")
    return TestClient(create_app(settings))


def test_health_and_database_initialize(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert (tmp_path / "data" / "recall.sqlite3").exists()


def test_mount_folder_and_report_status(tmp_path: Path) -> None:
    folder = tmp_path / "documents"
    folder.mkdir()

    with make_client(tmp_path) as client:
        response = client.post("/sources/folders", json={"path": str(folder)})
        assert response.status_code == 201
        assert "path" not in response.json()

        listing = client.get("/sources").json()
        assert len(listing["mounts"]) == 1

        index_status = client.get("/index/status").json()
        assert index_status["mounts"] == 1
        assert index_status["total"] == 0


def test_duplicate_and_invalid_mounts_are_rejected(tmp_path: Path) -> None:
    folder = tmp_path / "documents"
    folder.mkdir()

    with make_client(tmp_path) as client:
        assert client.post("/sources/folders", json={"path": str(folder)}).status_code == 201
        assert client.post("/sources/folders", json={"path": str(folder)}).status_code == 409
        assert client.post("/sources/folders", json={"path": str(tmp_path / "missing")}).status_code == 400


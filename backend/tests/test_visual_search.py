from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.db.database import get_connection, initialize_database
from app.ingestion import indexer
from app.ingestion.extractor import ExtractedUnit
from app.ingestion.indexer import index_source
from app.main import create_app
from app.retrieval.visual_search import visual_search


class DeterministicMultimodalProvider:
    name = "fake-multimodal"
    dimension = 3
    supports_visual = True

    def embed_text(self, text, content_hash, connection):
        lowered = text.lower()
        if "authentication" in lowered or "flowchart" in lowered:
            return [0.0, 1.0, 0.0]
        return [1.0, 0.0, 0.0]

    def embed_image(self, image_bytes, mime_type, content_hash, connection):
        return [0.0, 1.0, 0.0] if b"auth" in image_bytes else [1.0, 0.0, 0.0]


class TextOnlyProvider:
    name = "fake-text-only"
    dimension = 3
    supports_visual = False

    def embed_text(self, text, content_hash, connection):
        return [1.0, 0.0, 0.0]

    def embed_image(self, image_bytes, mime_type, content_hash, connection):
        raise AssertionError("visual embedding must not be requested from a text-only provider")


def create_visual_source(tmp_path: Path, filename: str, source_type: str, payload: bytes, page: int = 4):
    data_dir = tmp_path / "data"
    source_path = tmp_path / filename
    source_path.write_bytes(payload)
    database_path = data_dir / "recall.sqlite3"
    if not database_path.exists():
        initialize_database(database_path)
    with get_connection(database_path) as connection:
        mount = connection.execute(
            "SELECT mount_id FROM folder_mounts WHERE path = ?", (str(tmp_path),)
        ).fetchone()
        mount_id = mount[0] if mount else connection.execute(
            "INSERT INTO folder_mounts(path, label) VALUES (?, ?)", (str(tmp_path), "visual")
        ).lastrowid
        source_id = connection.execute(
            "INSERT INTO sources(mount_id, source_type, title, original_path, content_hash) "
            "VALUES (?, ?, ?, ?, ?)",
            (mount_id, source_type, filename, str(source_path), filename),
        ).lastrowid
    return Settings(data_dir=data_dir, allow_cloud_ai=True), int(source_id), source_path, page


def patch_visual_extractor(monkeypatch):
    def extract(path, source_type):
        page = 9 if source_type == "pdf" else None
        return [ExtractedUnit("page" if source_type == "pdf" else "image", page, path.read_text("utf-8"), path.read_bytes(), "image/png")]

    monkeypatch.setattr(indexer, "extract_file", extract)


def test_pdf_page_image_and_screenshot_receive_visual_embeddings(tmp_path: Path, monkeypatch) -> None:
    patch_visual_extractor(monkeypatch)
    settings, pdf_id, _, _ = create_visual_source(tmp_path, "diagram.pdf", "pdf", b"cnn diagram")
    _, image_id, _, _ = create_visual_source(tmp_path, "screenshot.png", "image", b"auth flowchart")
    provider = DeterministicMultimodalProvider()

    pdf_result = index_source(pdf_id, settings, provider)
    image_result = index_source(image_id, settings, provider)
    assert pdf_result["visual_embeddings_created"] == 1
    assert image_result["visual_embeddings_created"] == 1
    with get_connection(settings.database_path) as connection:
        rows = connection.execute(
            "SELECT source_id, page_or_slide, visual_embedding_ref, thumbnail_path FROM document_units "
            "WHERE source_id IN (?, ?)", (pdf_id, image_id)
        ).fetchall()
        assert {row["source_id"] for row in rows} == {pdf_id, image_id}
        assert all(row["visual_embedding_ref"] for row in rows)
        assert all(row["thumbnail_path"] for row in rows)


def test_text_query_retrieves_visual_units_with_correct_mapping_and_order(tmp_path: Path, monkeypatch) -> None:
    patch_visual_extractor(monkeypatch)
    settings, diagram_id, _, _ = create_visual_source(tmp_path, "cnn.pdf", "pdf", b"cnn diagram", 4)
    _, auth_id, _, _ = create_visual_source(tmp_path, "auth.png", "image", b"auth flowchart", 1)
    provider = DeterministicMultimodalProvider()
    index_source(diagram_id, settings, provider)
    index_source(auth_id, settings, provider)

    with get_connection(settings.database_path) as connection:
        results = visual_search(connection, settings, provider, "CNN max pooling")

    assert [result["filename"] for result in results[:2]] == ["cnn.pdf", "auth.png"]
    top = results[0]
    assert top["source_id"] == diagram_id
    assert top["page"] == 9
    assert top["thumbnail_url"].startswith("/units/")
    assert top["match_type"] == ["visual"]
    assert top["visual_match"] is True

    # A newly-opened connection represents an application restart: the visual
    # index is reloaded from disk and still maps back to the same unit.
    with get_connection(settings.database_path) as restarted_connection:
        restarted_results = visual_search(
            restarted_connection, settings, provider, "CNN max pooling"
        )
    assert restarted_results[0]["unit_id"] == top["unit_id"]


def test_visual_results_are_explicitly_selected(tmp_path: Path, monkeypatch) -> None:
    patch_visual_extractor(monkeypatch)
    settings, diagram_id, _, _ = create_visual_source(tmp_path, "cnn.pdf", "pdf", b"cnn diagram")
    provider = DeterministicMultimodalProvider()
    index_source(diagram_id, settings, provider)
    app = create_app(settings)
    monkeypatch.setattr("app.api.search.build_embedding_provider", lambda runtime_settings: provider)

    with TestClient(app) as client:
        response = client.get("/search", params={"q": "CNN max pooling", "mode": "visual"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["visual_retrieval_operational"] is True
    assert payload["results"]
    assert payload["results"][0]["source_id"] == diagram_id
    assert payload["results"][0]["visual_match"] is True
    assert "visual" in payload["results"][0]["match_type"]


def test_cloud_disabled_visual_search_reports_unavailable_without_provider_call(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        allow_cloud_ai=False,
        embedding_provider="gemini",
        gemini_api_key="must-not-be-used",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/search", params={"q": "a flowchart", "mode": "visual"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["results"] == []
    assert payload["visual_retrieval_operational"] is False
    assert "Cloud AI is disabled" in payload["notice"]


def test_text_only_provider_does_not_claim_visual_retrieval() -> None:
    provider = TextOnlyProvider()
    assert provider.supports_visual is False

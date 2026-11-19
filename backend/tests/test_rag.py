from pathlib import Path
import json

from fastapi.testclient import TestClient

from app.ai.generation import GeminiGenerationProvider
from app.config import Settings
from app.db.database import get_connection, initialize_database
from app.main import create_app
from app.rag import pipeline
from app.rag.conversations import list_messages


class FakeGenerationProvider:
    name = "fake-generation"
    model = "fake-model"

    def __init__(self, response=None, error=None):
        self.response = response or '{"answer":"The answer is supported [1].","citations":[1]}'
        self.error = error
        self.prompts = []

    def generate(self, prompt, content_hash, connection):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.response


def seed_database(tmp_path: Path, entries: list[tuple[str, str, int | None]]) -> tuple[Settings, list[int]]:
    data_dir = tmp_path / "data"
    database_path = data_dir / "recall.sqlite3"
    initialize_database(database_path)
    source_ids = []
    with get_connection(database_path) as connection:
        mount_id = connection.execute(
            "INSERT INTO folder_mounts(path, label) VALUES (?, ?)", (str(tmp_path), "rag")
        ).lastrowid
        for filename, text, page in entries:
            source_id = connection.execute(
                "INSERT INTO sources(mount_id, source_type, title, original_path, content_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (mount_id, "pdf", filename, str(tmp_path / filename), filename),
            ).lastrowid
            unit_id = connection.execute(
                "INSERT INTO document_units(source_id, unit_type, page_or_slide, extracted_text, short_comment) "
                "VALUES (?, ?, ?, ?, ?)",
                (source_id, "page", page, text, text[:80]),
            ).lastrowid
            connection.execute(
                "INSERT INTO fts_units(unit_id, source_id, extracted_text, short_comment, tags) VALUES (?, ?, ?, ?, ?)",
                (unit_id, source_id, text, text[:80], "rag"),
            )
            source_ids.append(int(source_id))
    return Settings(data_dir=data_dir, allow_cloud_ai=False), source_ids


def fake_result(settings: Settings, source_id: int, unit_id: int, score: float = 0.8) -> dict:
    return {
        "unit_id": unit_id,
        "source_id": source_id,
        "title": "Architecture notes.pdf",
        "source_type": "pdf",
        "filename": "architecture-notes.pdf",
        "page": 7,
        "snippet": "Architecture evidence",
        "short_comment": "Architecture evidence",
        "tags": ["rag"],
        "match_type": ["keyword"],
        "visual_match": False,
        "score": score,
    }


def test_question_returns_verified_source_and_page_citation(tmp_path: Path, monkeypatch) -> None:
    settings, source_ids = seed_database(tmp_path, [("architecture-notes.pdf", "Residual connections improve gradient flow.", 7)])
    with get_connection(settings.database_path) as connection:
        unit_id = connection.execute("SELECT unit_id FROM document_units").fetchone()[0]
    generation = FakeGenerationProvider()
    monkeypatch.setattr(
        pipeline,
        "retrieve_hybrid",
        lambda *args, **kwargs: {"results": [fake_result(settings, source_ids[0], unit_id)], "total": 1},
    )

    result = pipeline.ask_question(settings, "What improves gradient flow?", generation_provider=generation)

    assert result["status"] == "answered"
    assert result["citations"][0]["source_id"] == source_ids[0]
    assert result["citations"][0]["unit_id"] == unit_id
    assert result["citations"][0]["page"] == 7
    assert result["citations"][0]["source_url"] == f"/sources/{source_ids[0]}/file"
    assert "Residual connections" in generation.prompts[0]


def test_multi_document_context_and_invalid_model_citations_are_filtered(tmp_path: Path, monkeypatch) -> None:
    settings, source_ids = seed_database(
        tmp_path,
        [("one.pdf", "One document says cats are mammals.", 2), ("two.pdf", "Two document says whales are mammals.", 9)],
    )
    with get_connection(settings.database_path) as connection:
        units = [row[0] for row in connection.execute("SELECT unit_id FROM document_units ORDER BY unit_id")]
    generation = FakeGenerationProvider('{"answer":"Both documents support this [1] [2] [99].","citations":[1,2,99]}')
    monkeypatch.setattr(
        pipeline,
        "retrieve_hybrid",
        lambda *args, **kwargs: {"results": [
            fake_result(settings, source_ids[0], units[0]), fake_result(settings, source_ids[1], units[1])
        ], "total": 2},
    )

    result = pipeline.ask_question(settings, "Compare the documents.", generation_provider=generation)

    assert [citation["source_id"] for citation in result["citations"]] == source_ids
    assert [citation["reference"] for citation in result["citations"]] == [1, 2]
    assert "[99]" not in result["answer"]


def test_multiple_units_from_one_source_create_one_verified_citation(tmp_path: Path, monkeypatch) -> None:
    settings, source_ids = seed_database(
        tmp_path,
        [("one.pdf", "First passage about deep learning.", 2), ("two.pdf", "Other document.", 9)],
    )
    with get_connection(settings.database_path) as connection:
        second_unit = connection.execute(
            "INSERT INTO document_units(source_id, unit_type, page_or_slide, extracted_text, short_comment) "
            "VALUES (?, 'page', 3, ?, ?)",
            (source_ids[0], "Second passage about deep learning.", "Second passage"),
        ).lastrowid
        units = [row[0] for row in connection.execute("SELECT unit_id FROM document_units ORDER BY unit_id")]
    generation = FakeGenerationProvider()
    monkeypatch.setattr(
        pipeline,
        "retrieve_hybrid",
        lambda *args, **kwargs: {"results": [
            fake_result(settings, source_ids[0], units[0]),
            fake_result(settings, source_ids[0], second_unit, 0.7),
        ], "total": 2},
    )

    result = pipeline.ask_question(settings, "Explain deep learning.", generation_provider=generation)

    assert result["status"] == "answered"
    assert len(result["citations"]) == 1
    assert result["citations"][0]["source_id"] == source_ids[0]
    assert len(result["citations"][0]["passages"]) == 2


def test_follow_up_uses_local_conversation_history_and_survives_reconnect(tmp_path: Path, monkeypatch) -> None:
    settings, source_ids = seed_database(tmp_path, [("notes.pdf", "Pooling reduces spatial dimensions.", 3)])
    with get_connection(settings.database_path) as connection:
        unit_id = connection.execute("SELECT unit_id FROM document_units").fetchone()[0]
    generation = FakeGenerationProvider()
    monkeypatch.setattr(
        pipeline,
        "retrieve_hybrid",
        lambda *args, **kwargs: {"results": [fake_result(settings, source_ids[0], unit_id)], "total": 1},
    )

    first = pipeline.ask_question(settings, "What does pooling do?", generation_provider=generation)
    second = pipeline.ask_question(settings, "And why is that useful?", first["conversation_id"], generation)

    assert second["conversation_id"] == first["conversation_id"]
    assert "What does pooling do?" in generation.prompts[1]
    with get_connection(settings.database_path) as connection:
        messages = list_messages(connection, first["conversation_id"], 20, 0)
    assert [message["role"] for message in messages] == ["user", "assistant", "user", "assistant"]


def test_empty_retrieval_returns_controlled_insufficient_evidence(tmp_path: Path, monkeypatch) -> None:
    settings, _ = seed_database(tmp_path, [])
    generation = FakeGenerationProvider()
    monkeypatch.setattr(pipeline, "retrieve_hybrid", lambda *args, **kwargs: {"results": [], "total": 0})

    result = pipeline.ask_question(settings, "What is not in my library?", generation_provider=generation)

    assert result["status"] == "no_sources_indexed"
    assert result["answer"] == pipeline.NO_SOURCES_INDEXED
    assert generation.prompts == []


def test_context_limit_is_explicit(tmp_path: Path, monkeypatch) -> None:
    settings, source_ids = seed_database(tmp_path, [("long.pdf", "evidence " * 500, 1)])
    with get_connection(settings.database_path) as connection:
        unit_id = connection.execute("SELECT unit_id FROM document_units").fetchone()[0]
    generation = FakeGenerationProvider()
    monkeypatch.setattr(
        pipeline,
        "retrieve_hybrid",
        lambda *args, **kwargs: {"results": [fake_result(settings, source_ids[0], unit_id)], "total": 1},
    )

    result = pipeline.ask_question(
        settings, "Summarize this.", generation_provider=generation, max_context_chars=80
    )

    assert result["retrieval"]["context_characters"] <= 80
    assert result["retrieval"]["evidence_count"] == 1


def test_provider_error_is_clear_and_persisted(tmp_path: Path, monkeypatch) -> None:
    settings, source_ids = seed_database(tmp_path, [("error.pdf", "known evidence.", 1)])
    with get_connection(settings.database_path) as connection:
        unit_id = connection.execute("SELECT unit_id FROM document_units").fetchone()[0]
    generation = FakeGenerationProvider(error=TimeoutError("not exposed"))
    monkeypatch.setattr(
        pipeline,
        "retrieve_hybrid",
        lambda *args, **kwargs: {"results": [fake_result(settings, source_ids[0], unit_id)], "total": 1},
    )

    result = pipeline.ask_question(settings, "Answer this.", generation_provider=generation)

    assert result["status"] == "provider_error"
    assert result["error"] == "Generation provider failed: TimeoutError"
    with get_connection(settings.database_path) as connection:
        assert len(list_messages(connection, result["conversation_id"], 20, 0)) == 2


def test_cloud_disabled_api_stores_local_conversation_without_generation(tmp_path: Path) -> None:
    settings, _ = seed_database(tmp_path, [])
    with TestClient(create_app(settings)) as client:
        response = client.post("/ask", json={"question": "Tell me about private files."})
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "no_sources_indexed"
        conversation_id = payload["conversation_id"]
        assert client.get(f"/conversations/{conversation_id}/messages").status_code == 200
        assert client.delete(f"/conversations/{conversation_id}").status_code == 204
        assert client.get(f"/conversations/{conversation_id}").status_code == 404


def test_cloud_disabled_with_evidence_returns_explicit_message(tmp_path: Path, monkeypatch) -> None:
    settings, source_ids = seed_database(tmp_path, [("private.pdf", "private evidence", 1)])
    with get_connection(settings.database_path) as connection:
        unit_id = connection.execute("SELECT unit_id FROM document_units").fetchone()[0]
    monkeypatch.setattr(
        pipeline,
        "retrieve_hybrid",
        lambda *args, **kwargs: {"results": [fake_result(settings, source_ids[0], unit_id)], "total": 1},
    )

    result = pipeline.ask_question(settings, "What is in this file?")

    assert result["status"] == "cloud_disabled"
    assert "Cloud AI is disabled" in result["answer"]
    assert result["citations"] == []


def test_generation_provider_uses_interactions_store_false_without_network(tmp_path: Path, monkeypatch) -> None:
    import google.genai as genai

    calls = []

    class Interactions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return type("Interaction", (), {"output_text": "answer"})()

    monkeypatch.setattr(genai, "Client", lambda api_key: type("Client", (), {"interactions": Interactions()})())
    provider = GeminiGenerationProvider(api_key="secret", allow_cloud_ai=True)
    database_path = tmp_path / "recall.sqlite3"
    initialize_database(database_path)
    with get_connection(database_path) as connection:
        assert provider.generate(
            "grounded prompt", "hash", connection, system_instruction="grounding rules"
        ) == "answer"
    assert calls[0]["model"] == "gemini-3.1-flash-lite"
    assert calls[0]["store"] is False
    assert calls[0]["input"] == "grounded prompt"
    assert calls[0]["system_instruction"] == "grounding rules"

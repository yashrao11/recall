"""Tests for Study Mode: SM-2 scheduling, service layer, and API routes."""
from __future__ import annotations

from pathlib import Path
import json

import pytest
from fastapi.testclient import TestClient

from app.ai.study_generator import parse_study_generation, sm2_next_review
from app.config import Settings
from app.db.database import initialize_database, connect
from app.main import create_app
from app.study.service import generate_study_set, list_due_cards, respond_to_card, complete_session


class FakeStudyProvider:
    name = "fake-study"
    model = "synthetic"

    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0
        self.prompts = []

    def generate(self, prompt, content_hash, connection, *, system_instruction=None):
        self.calls += 1
        self.prompts.append(prompt)
        assert "Retrieved" not in prompt  # Study prompt uses its own evidence heading.
        assert system_instruction and "source_refs" in system_instruction
        return json.dumps(self.payload)


# ── Fixtures ────────────────────────────────────────────────────────────────

def _synthetic_db(path: Path) -> None:
    """Seed an isolated test database with one source, two document units."""
    initialize_database(path)
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO folder_mounts(path, label) VALUES (?, ?)",
            (str(path.parent), "Test mount"),
        )
        mount_id = conn.execute("SELECT mount_id FROM folder_mounts").fetchone()[0]
        conn.execute(
            "INSERT INTO sources(mount_id, source_type, title, original_path, indexing_status) "
            "VALUES (?, 'pdf', 'Test Document', ?, 'done')",
            (mount_id, str(path.parent / "test.pdf")),
        )
        source_id = conn.execute("SELECT source_id FROM sources").fetchone()[0]
        conn.execute(
            "INSERT INTO document_units(source_id, unit_type, page_or_slide, extracted_text) "
            "VALUES (?, 'page', 1, ?)",
            (source_id, "Neural networks are computational models inspired by the human brain. They consist of layers of interconnected nodes that learn representations from data."),
        )
        conn.execute(
            "INSERT INTO document_units(source_id, unit_type, page_or_slide, extracted_text) "
            "VALUES (?, 'page', 2, ?)",
            (source_id, "Backpropagation is an algorithm used to train neural networks by computing gradients of the loss function with respect to each weight."),
        )
        conn.commit()


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(data_dir=tmp_path / "data")
    return TestClient(create_app(settings))


# ── SM-2 algorithm unit tests ────────────────────────────────────────────────

def test_sm2_quality_below_3_resets_interval() -> None:
    interval, ease = sm2_next_review(2.5, 10, 2)
    assert interval == 1
    assert ease < 2.5  # ease penalised


def test_sm2_quality_3_on_first_card_gives_6_days() -> None:
    interval, ease = sm2_next_review(2.5, 1, 3)
    assert interval == 6


def test_sm2_quality_5_increases_ease_and_extends_interval() -> None:
    interval, ease = sm2_next_review(2.5, 6, 5)
    assert interval == 15  # 6 * 2.5 = 15
    assert ease > 2.5


def test_sm2_ease_floor_at_1_3() -> None:
    _, ease = sm2_next_review(1.3, 5, 0)
    assert ease == pytest.approx(1.3)


def test_sm2_quality_out_of_range_raises() -> None:
    with pytest.raises(ValueError):
        sm2_next_review(2.5, 1, 6)
    with pytest.raises(ValueError):
        sm2_next_review(2.5, 1, -1)


# ── parse_study_generation unit tests ───────────────────────────────────────

def test_parse_study_generation_valid_json() -> None:
    raw = json.dumps({"flashcards": [], "mcqs": [], "short_answers": []})
    result = parse_study_generation(raw)
    assert isinstance(result, dict)
    assert "flashcards" in result


def test_parse_study_generation_strips_code_fence() -> None:
    raw = "```json\n{\"flashcards\": [], \"mcqs\": [], \"short_answers\": []}\n```"
    result = parse_study_generation(raw)
    assert isinstance(result, dict)


def test_parse_study_generation_rejects_non_object() -> None:
    with pytest.raises((ValueError, Exception)):
        parse_study_generation("[1, 2, 3]")


# ── Service layer tests (no AI — cloud disabled) ────────────────────────────

def test_generate_study_set_cloud_disabled(tmp_path: Path) -> None:
    _synthetic_db(tmp_path / "data" / "recall.sqlite3")
    settings = Settings(data_dir=tmp_path / "data", allow_cloud_ai=False)
    # get the source_id
    with connect(settings.database_path) as conn:
        source_id = conn.execute("SELECT source_id FROM sources").fetchone()[0]
    result = generate_study_set(settings, source_id=source_id)
    assert result["status"] == "cloud_disabled"
    assert "cards" in result
    assert "evidence" in result


def test_generate_study_set_source_not_found(tmp_path: Path) -> None:
    _synthetic_db(tmp_path / "data" / "recall.sqlite3")
    settings = Settings(data_dir=tmp_path / "data")
    with pytest.raises(LookupError, match="source not found"):
        generate_study_set(settings, source_id=9999)


def test_generate_study_set_requires_exactly_one_target(tmp_path: Path) -> None:
    _synthetic_db(tmp_path / "data" / "recall.sqlite3")
    settings = Settings(data_dir=tmp_path / "data")
    with pytest.raises(ValueError):
        generate_study_set(settings, source_id=1, object_id=1)
    with pytest.raises(ValueError):
        generate_study_set(settings)


def test_generate_study_set_validates_citations_and_persists_mcq_explanation(tmp_path: Path) -> None:
    database = tmp_path / "data" / "recall.sqlite3"
    _synthetic_db(database)
    settings = Settings(data_dir=tmp_path / "data", allow_cloud_ai=True)
    with connect(database) as conn:
        source_id = conn.execute("SELECT source_id FROM sources").fetchone()[0]
    provider = FakeStudyProvider({
        "flashcards": [{"front": "What trains the model?", "back": "Backpropagation", "source_refs": [1]}],
        "mcqs": [{"question": "How many options?", "options": ["1", "2", "3", "4"], "answer": "4", "explanation": "The question defines four options.", "source_refs": [2]}],
        "short_answers": [{"question": "Unsupported", "answer": "No", "source_refs": [99]}],
    })
    result = generate_study_set(settings, source_id=source_id, generation_provider=provider)
    assert result["status"] == "ready"
    assert len(result["cards"]) == 2
    assert "Neural networks are computational models" in provider.prompts[0]
    assert "Backpropagation is an algorithm" in provider.prompts[0]
    mcq = next(card for card in result["cards"] if card["card_type"] == "mcq")
    assert mcq["options"] == ["1", "2", "3", "4"]
    assert mcq["explanation"] == "The question defines four options."
    assert all(card["evidence"] for card in result["cards"])
    assert provider.calls == 1


def test_generate_study_set_supports_entity_evidence(tmp_path: Path) -> None:
    database = tmp_path / "data" / "recall.sqlite3"
    _synthetic_db(database)
    settings = Settings(data_dir=tmp_path / "data", allow_cloud_ai=True)
    with connect(database) as conn:
        source_id = conn.execute("SELECT source_id FROM sources").fetchone()[0]
        unit_id = conn.execute("SELECT unit_id FROM document_units ORDER BY unit_id LIMIT 1").fetchone()[0]
        conn.execute("INSERT INTO objects(object_type, title, normalized_title, created_by, discovery_method) VALUES ('topic', 'Neural networks', 'neural networks', 'system', 'lexical')")
        object_id = conn.execute("SELECT object_id FROM objects").fetchone()[0]
        conn.execute("INSERT INTO object_evidence(object_id, source_id, unit_id, discovery_method) VALUES (?, ?, ?, 'lexical')", (object_id, source_id, unit_id))
        conn.commit()
    provider = FakeStudyProvider({"flashcards": [{"front": "Q", "back": "A", "source_refs": [1]}], "mcqs": [], "short_answers": []})
    result = generate_study_set(settings, object_id=object_id, generation_provider=provider)
    assert result["status"] == "ready"
    assert result["title"] == "Neural networks"


def test_list_due_cards_empty_on_fresh_db(tmp_path: Path) -> None:
    _synthetic_db(tmp_path / "data" / "recall.sqlite3")
    settings = Settings(data_dir=tmp_path / "data")
    result = list_due_cards(settings)
    assert result["cards"] == []
    assert result["total"] == 0


def test_list_due_cards_limit_clamped(tmp_path: Path) -> None:
    _synthetic_db(tmp_path / "data" / "recall.sqlite3")
    settings = Settings(data_dir=tmp_path / "data")
    result = list_due_cards(settings, limit=200)
    assert result["limit"] == 100
    result2 = list_due_cards(settings, limit=0)
    assert result2["limit"] == 1


def test_respond_to_card_not_found(tmp_path: Path) -> None:
    _synthetic_db(tmp_path / "data" / "recall.sqlite3")
    settings = Settings(data_dir=tmp_path / "data")
    with pytest.raises(LookupError, match="study card not found"):
        respond_to_card(settings, card_id=9999, quality=3)


def test_complete_session_persists_event(tmp_path: Path) -> None:
    _synthetic_db(tmp_path / "data" / "recall.sqlite3")
    settings = Settings(data_dir=tmp_path / "data")
    result = complete_session(
        settings,
        source_id=None,
        object_id=None,
        started_at=None,
        completed_at=None,
        questions_attempted=5,
        answers_correct=3,
    )
    assert "session_id" in result
    assert "event_id" in result
    assert result["event_id"] > 0
    with connect(settings.database_path) as conn:
        session = conn.execute(
            "SELECT questions_attempted, answers_correct FROM study_sessions WHERE session_id = ?",
            (result["session_id"],),
        ).fetchone()
        assert session["questions_attempted"] == 5
        assert session["answers_correct"] == 3
        event = conn.execute(
            "SELECT event_type FROM memory_events WHERE event_id = ?", (result["event_id"],)
        ).fetchone()
        assert event["event_type"] == "study_session_completed"


def test_complete_session_rejects_invalid_counts(tmp_path: Path) -> None:
    _synthetic_db(tmp_path / "data" / "recall.sqlite3")
    settings = Settings(data_dir=tmp_path / "data")
    with pytest.raises(ValueError):
        complete_session(settings, source_id=None, object_id=None, started_at=None, completed_at=None, questions_attempted=2, answers_correct=5)


def test_complete_session_source_scope(tmp_path: Path) -> None:
    _synthetic_db(tmp_path / "data" / "recall.sqlite3")
    settings = Settings(data_dir=tmp_path / "data")
    with connect(settings.database_path) as conn:
        source_id = conn.execute("SELECT source_id FROM sources").fetchone()[0]
    result = complete_session(
        settings,
        source_id=source_id,
        object_id=None,
        started_at="2026-11-18T10:00:00Z",
        completed_at="2026-11-18T10:30:00Z",
        questions_attempted=3,
        answers_correct=2,
    )
    assert result["session_id"] > 0
    with connect(settings.database_path) as conn:
        row = conn.execute(
            "SELECT source_id FROM study_sessions WHERE session_id = ?", (result["session_id"],)
        ).fetchone()
        assert row["source_id"] == source_id


# ── API route tests ──────────────────────────────────────────────────────────

def test_study_generate_404_for_missing_source(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post("/study/generate", json={"source_id": 9999})
        assert response.status_code == 404


def test_study_generate_400_for_ambiguous_target(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post("/study/generate", json={"source_id": 1, "object_id": 1})
        assert response.status_code == 400


def test_study_due_returns_empty_list(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/study/due")
        assert response.status_code == 200
        data = response.json()
        assert "cards" in data
        assert isinstance(data["cards"], list)


def test_study_due_limit_validation(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/study/due?limit=0")
        assert response.status_code == 422  # FastAPI Query validation
        response2 = client.get("/study/due?limit=101")
        assert response2.status_code == 422


def test_study_respond_404_for_missing_card(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post("/study/respond", json={"card_id": 9999, "quality": 3})
        assert response.status_code == 404


def test_study_respond_422_for_invalid_quality(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post("/study/respond", json={"card_id": 1, "quality": 6})
        assert response.status_code == 422


def test_study_sessions_persists_and_returns_event(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post("/study/sessions", json={
            "questions_attempted": 4,
            "answers_correct": 4,
        })
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert "event_id" in data


def test_study_sessions_rejects_bad_counts(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.post("/study/sessions", json={
            "questions_attempted": 2,
            "answers_correct": 5,
        })
        assert response.status_code == 400


def test_study_generate_returns_cloud_disabled_for_local_only_source(tmp_path: Path) -> None:
    """With cloud AI disabled (default), generate returns a clear status rather than failing."""
    settings = Settings(data_dir=tmp_path / "data", allow_cloud_ai=False)
    # Initialize and seed
    _synthetic_db(settings.database_path)
    with connect(settings.database_path) as conn:
        source_id = conn.execute("SELECT source_id FROM sources").fetchone()[0]
    # Use an app with cloud AI disabled
    client = TestClient(create_app(settings))
    with client:
        response = client.post("/study/generate", json={"source_id": source_id})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in {"cloud_disabled", "provider_unavailable", "insufficient_evidence"}
        assert isinstance(data["cards"], list)


def test_respond_to_card_updates_sm2_schedule(tmp_path: Path) -> None:
    """Seeding a flashcard directly then verifying SM-2 update via API."""
    settings = Settings(data_dir=tmp_path / "data")
    _synthetic_db(settings.database_path)
    # Insert a flashcard directly
    with connect(settings.database_path) as conn:
        source_id = conn.execute("SELECT source_id FROM sources").fetchone()[0]
        conn.execute(
            "INSERT INTO flashcards(source_id, card_type, front, back) VALUES (?, 'flashcard', 'Q?', 'A.')",
            (source_id,),
        )
        card_id = conn.execute("SELECT card_id FROM flashcards").fetchone()[0]

    client = TestClient(create_app(settings))
    with client:
        response = client.post("/study/respond", json={"card_id": card_id, "quality": 5})
        assert response.status_code == 200
        data = response.json()
        assert data["card_id"] == card_id
        assert data["quality"] == 5
        assert data["interval_days"] > 1
        assert data["ease_factor"] > 2.5

    # Verify persisted
    with connect(settings.database_path) as conn:
        row = conn.execute("SELECT ease_factor, interval_days FROM flashcards WHERE card_id = ?", (card_id,)).fetchone()
        assert row["interval_days"] > 1

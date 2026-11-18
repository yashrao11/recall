"""Controlled live Gemini generation smoke test for Ask My Memory.

Run from the repository root with an explicitly scoped environment:

    ALLOW_CLOUD_AI=true PYTHONPATH=backend \
        .venv/bin/python backend/scripts/live_rag_smoke.py

The script creates only synthetic evidence in a temporary database. It never
scans mounted folders or reads personal documents.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

from app.ai.generation import build_generation_provider
from app.config import Settings, load_project_environment
from app.db.database import get_connection, initialize_database
from app.rag import pipeline
from app.rag.conversations import history_for_prompt


QUESTION = "What voltage does the Project Aurora prototype use?"
FOLLOW_UP = "What colour is its indicator?"
UNANSWERABLE = "What is the mass of the Project Aurora prototype?"
SOURCE_TITLE = "Synthetic Physics Notes"
SOURCE_TEXT = (
    "Project Aurora is a fictional experiment. Its prototype uses a 12-volt "
    "power supply and a blue indicator."
)


def _truth(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _synthetic_retriever(unit_id: int, source_id: int):
    def retrieve(_connection, _settings, query, _mode, limit=50):
        if any(token in query.lower() for token in ("voltage", "volt", "colour", "color", "indicator")):
            return {
                "results": [{
                    "unit_id": unit_id,
                    "source_id": source_id,
                    "title": SOURCE_TITLE,
                    "source_type": "note",
                    "filename": "synthetic-physics-notes.txt",
                    "page": 1,
                    "snippet": SOURCE_TEXT,
                    "short_comment": SOURCE_TEXT,
                    "match_type": ["synthetic"],
                    "visual_match": False,
                    "score": 1.0,
                }][:limit],
                "total": 1,
            }
        return {"results": [], "total": 0}

    return retrieve


def _cache_count(database_path: Path) -> int:
    with get_connection(database_path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM api_cache").fetchone()[0])


def main() -> int:
    # Load the same project environment as the application; shell variables win.
    load_project_environment()
    if not _truth(os.getenv("ALLOW_CLOUD_AI")):
        print("SKIPPED: ALLOW_CLOUD_AI=true is required for the live smoke test")
        return 2
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        print("SKIPPED: GEMINI_API_KEY is required (value is never printed)")
        return 2

    original_retriever = pipeline.retrieve_hybrid
    try:
        with tempfile.TemporaryDirectory(prefix="recall-live-rag-") as temporary_directory:
            temporary_path = Path(temporary_directory)
            database_path = temporary_path / "data" / "recall.sqlite3"
            synthetic_file = temporary_path / "synthetic-physics-notes.txt"
            synthetic_file.write_text(SOURCE_TEXT, encoding="utf-8")
            initialize_database(database_path)
            with get_connection(database_path) as connection:
                mount_id = connection.execute(
                    "INSERT INTO folder_mounts(path, label) VALUES (?, ?)",
                    (temporary_directory, "synthetic"),
                ).lastrowid
                source_id = connection.execute(
                    "INSERT INTO sources(mount_id, source_type, title, original_path, content_hash) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (mount_id, "note", SOURCE_TITLE, str(synthetic_file), "synthetic-aurora"),
                ).lastrowid
                unit_id = connection.execute(
                    "INSERT INTO document_units(source_id, unit_type, page_or_slide, extracted_text, short_comment) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (source_id, "note", 1, SOURCE_TEXT, SOURCE_TEXT),
                ).lastrowid

            settings = Settings(
                data_dir=temporary_path / "data",
                allow_cloud_ai=True,
                gemini_api_key=api_key,
                # This isolated validation targets Gemini even if the normal local
                # development provider remains configured as Ollama in .env.
                llm_provider="gemini",
                gemini_generation_model=os.getenv("GEMINI_GENERATION_MODEL", "gemini-3.1-flash-lite"),
            )
            provider = build_generation_provider(settings)
            pipeline.retrieve_hybrid = _synthetic_retriever(int(unit_id), int(source_id))

            first = pipeline.ask_question(settings, QUESTION, generation_provider=provider)
            first_citation = first["citations"][0] if first["citations"] else {}
            first_ok = (
                first["status"] == "answered"
                and "12" in first["answer"]
                and "volt" in first["answer"].lower()
            )
            citation_ok = (
                first_citation.get("source_id") == source_id
                and first_citation.get("unit_id") == unit_id
                and first_citation.get("title") == SOURCE_TITLE
                and first_citation.get("page") == 1
            )

            second = pipeline.ask_question(
                settings, FOLLOW_UP, first["conversation_id"], generation_provider=provider
            )
            follow_up_ok = (
                second["status"] == "answered"
                and "blue" in second["answer"].lower()
                and bool(second["citations"])
            )
            with get_connection(database_path) as connection:
                local_history = history_for_prompt(connection, first["conversation_id"])
            local_context_ok = any(QUESTION in item["content"] for item in local_history)

            cache_before = _cache_count(database_path)
            no_evidence = pipeline.ask_question(
                settings, UNANSWERABLE, first["conversation_id"], generation_provider=provider
            )
            cache_after = _cache_count(database_path)
            insufficient_ok = (
                no_evidence["status"] == "insufficient_evidence"
                and no_evidence["answer"] == pipeline.INSUFFICIENT_EVIDENCE
                and cache_after == cache_before
            )

            invalid_removed = "[999]" not in pipeline._remove_invalid_inline_citations(
                "Supported [1] and unsupported [999]", 1
            )
            request_metadata = getattr(provider, "last_request_metadata", {})
            print(f"generation_model: {provider.model}")
            print("api_success: true")
            print(f"store_false_confirmed: {request_metadata.get('store') is False}")
            print(f"system_instruction_confirmed: {request_metadata.get('has_system_instruction') is True}")
            print(f"answer_correctness: {first_ok}")
            print(f"citation_correctness: {citation_ok}")
            print(f"follow_up_correctness: {follow_up_ok}")
            print(f"local_conversation_context: {local_context_ok}")
            print(f"unanswerable_controlled: {insufficient_ok}")
            print(f"invalid_citation_rejected: {invalid_removed}")
            print("api_errors_or_rate_limits: none")
            return 0 if all((first_ok, citation_ok, follow_up_ok, local_context_ok, insufficient_ok, invalid_removed)) else 1
    except Exception as exc:  # Do not expose SDK payloads or keys in smoke output.
        print(f"api_success: false")
        print(f"api_error: {type(exc).__name__}")
        return 1
    finally:
        pipeline.retrieve_hybrid = original_retriever


if __name__ == "__main__":
    raise SystemExit(main())

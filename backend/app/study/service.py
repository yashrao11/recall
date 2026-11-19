from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import inspect
import json
from pathlib import Path
import re
import sqlite3

from app.ai.generation import GenerationProvider, build_generation_provider
from app.ai.providers import ProviderUnavailable
from app.ai.study_generator import (
    STUDY_SYSTEM_INSTRUCTION,
    parse_study_generation,
    sm2_next_review,
    study_prompt,
)
from app.config import Settings
from app.db.database import get_connection, record_memory_event
from app.rag.pipeline import _excerpt


class StudyError(RuntimeError):
    pass


def generate_study_set(
    settings: Settings,
    *,
    source_id: int | None = None,
    object_id: int | None = None,
    generation_provider: GenerationProvider | None = None,
    max_context_chars: int = 18000,
) -> dict:
    if (source_id is None) == (object_id is None):
        raise ValueError("provide exactly one of source_id or object_id")
    with get_connection(settings.database_path) as connection:
        context_rows, title = _study_rows(connection, source_id, object_id)
        if not context_rows:
            return {"status": "insufficient_evidence", "cards": [], "evidence": []}
        context, evidence = _build_evidence(context_rows, max_context_chars)
        local_message = None
        cloud_provider_injection = (
            generation_provider is not None
            and str(getattr(generation_provider, "name", "")).lower() == "gemini"
        )
        if not settings.allow_cloud_ai and (generation_provider is None or cloud_provider_injection):
            # Keep the execution boundary safe even if an internal caller passes
            # a Gemini-shaped provider directly while cloud AI is disabled.
            generation_provider = None
            cards = _local_extractive_cards(evidence)
            local_message = (
                "Cloud AI is disabled. Recall created an extractive study set locally "
                "from the indexed evidence."
            )
        elif generation_provider is None:
            try:
                generation_provider = build_generation_provider(settings)
            except ProviderUnavailable as exc:
                return {"status": "provider_unavailable", "message": str(exc), "cards": [], "evidence": evidence}
        if generation_provider is not None:
            prompt = study_prompt(context, title)
            content_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            try:
                raw = _invoke_generation(generation_provider, prompt, content_hash, connection)
                payload = parse_study_generation(raw)
                cards = _validated_cards(payload, evidence)
            except Exception as exc:  # provider and malformed-output boundary
                return {
                    "status": "provider_error",
                    "message": f"Study generation failed: {type(exc).__name__}",
                    "cards": [],
                    "evidence": evidence,
                }
        if not cards:
            return {
                "status": "insufficient_evidence",
                "message": "The indexed text is not substantial enough to create reliable study cards.",
                "cards": [],
                "evidence": evidence,
            }
        saved = []
        for item in cards:
            refs = item.pop("source_refs")
            ref = evidence[refs[0] - 1]
            cursor = connection.execute(
                "INSERT INTO flashcards(source_id, unit_id, card_type, front, back, options_json, next_review) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ref["source_id"], ref["unit_id"], item["card_type"], item["front"], item["back"],
                 json.dumps(_stored_options(item)) if item.get("options") is not None else None,
                 datetime.now(UTC).isoformat()),
            )
            saved.append(_card_payload(connection, int(cursor.lastrowid), item, refs, evidence))
        result = {"status": "ready", "cards": saved, "evidence": evidence, "title": title}
        if local_message:
            result["message"] = local_message
        return result


def list_due_cards(settings: Settings, limit: int = 20) -> dict:
    with get_connection(settings.database_path) as connection:
        rows = connection.execute(
            "SELECT card_id FROM flashcards WHERE next_review IS NULL OR datetime(next_review) <= datetime(?) "
            "ORDER BY COALESCE(next_review, '') LIMIT ?", (datetime.now(UTC).isoformat(), min(max(limit, 1), 100))
        ).fetchall()
        cards = [_card_payload_from_row(connection, row["card_id"]) for row in rows]
    return {"cards": cards, "total": len(cards), "limit": min(max(limit, 1), 100)}


def respond_to_card(settings: Settings, card_id: int, quality: int, session_id: int | None = None) -> dict:
    with get_connection(settings.database_path) as connection:
        row = connection.execute("SELECT * FROM flashcards WHERE card_id = ?", (card_id,)).fetchone()
        if row is None:
            raise LookupError("study card not found")
        interval, ease = sm2_next_review(float(row["ease_factor"]), int(row["interval_days"]), quality)
        next_review = (datetime.now(UTC) + timedelta(days=interval)).isoformat()
        connection.execute(
            "UPDATE flashcards SET next_review = ?, ease_factor = ?, interval_days = ? WHERE card_id = ?",
            (next_review, ease, interval, card_id),
        )
        if session_id is not None:
            connection.execute(
                "UPDATE study_sessions SET questions_attempted = questions_attempted + 1, "
                "answers_correct = answers_correct + ? WHERE session_id = ?",
                (1 if quality >= 3 else 0, session_id),
            )
        return {"card_id": card_id, "quality": quality, "interval_days": interval, "ease_factor": ease, "next_review": next_review}


def complete_session(
    settings: Settings,
    *,
    source_id: int | None,
    object_id: int | None,
    started_at: str | None,
    completed_at: str | None,
    questions_attempted: int,
    answers_correct: int,
) -> dict:
    if questions_attempted < 0 or answers_correct < 0 or answers_correct > questions_attempted:
        raise ValueError("invalid study session counts")
    with get_connection(settings.database_path) as connection:
        _validate_scope(connection, source_id, object_id)
        started = _utc(started_at)
        completed = _utc(completed_at)
        cursor = connection.execute(
            "INSERT INTO study_sessions(source_id, object_id, started_at, completed_at, questions_attempted, answers_correct) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (source_id, object_id, started, completed, questions_attempted, answers_correct),
        )
        session_id = int(cursor.lastrowid)
        event_id = record_memory_event(
            connection, "study_session_completed", source_id=source_id, timestamp=completed,
            date_basis="studied_at", metadata={"session_id": session_id, "object_id": object_id,
            "questions_attempted": questions_attempted, "answers_correct": answers_correct},
        )
    return {"session_id": session_id, "event_id": event_id, "completed_at": completed}


def _study_rows(connection: sqlite3.Connection, source_id: int | None, object_id: int | None):
    if source_id is not None:
        source = connection.execute("SELECT source_id, title, original_path FROM sources WHERE source_id = ?", (source_id,)).fetchone()
        if source is None:
            raise LookupError("source not found")
        rows = connection.execute(_UNIT_QUERY + " WHERE u.source_id = ? ORDER BY u.unit_id", (source_id,)).fetchall()
        return rows, source["title"] or Path(source["original_path"]).name
    entity = connection.execute("SELECT object_id, title FROM objects WHERE object_id = ?", (object_id,)).fetchone()
    if entity is None:
        raise LookupError("entity not found")
    rows = connection.execute(
        _UNIT_QUERY + " JOIN object_evidence oe ON oe.unit_id = u.unit_id AND oe.source_id = u.source_id "
        "WHERE oe.object_id = ? ORDER BY u.unit_id", (object_id,)
    ).fetchall()
    return rows, entity["title"]


_UNIT_QUERY = (
    "SELECT u.unit_id, u.source_id, u.page_or_slide, u.extracted_text, u.short_comment, "
    "s.title AS source_title, s.original_path, s.source_type FROM document_units u "
    "JOIN sources s ON s.source_id = u.source_id"
)


def _build_evidence(rows, max_chars):
    evidence, blocks, used = [], [], 0
    for row in rows:
        excerpt = _excerpt(row["extracted_text"] or row["short_comment"] or "")
        if not excerpt:
            continue
        item = {"reference": len(evidence) + 1, "source_id": row["source_id"], "unit_id": row["unit_id"],
                "title": row["source_title"] or Path(row["original_path"]).name, "filename": Path(row["original_path"]).name,
                "page": row["page_or_slide"], "excerpt": excerpt, "source_type": row["source_type"]}
        block = f"[{item['reference']}] {item['title']} ({item['filename']})" + (f", page {item['page']}" if item["page"] is not None else "") + f"\nExcerpt: {excerpt}\n"
        if blocks and used + len(block) > max_chars:
            break
        if not blocks and len(block) > max_chars:
            prefix = block.split("Excerpt: ", 1)[0] + "Excerpt: "
            item["excerpt"] = excerpt[:max(0, max_chars - len(prefix) - 1)]
            block = prefix + item["excerpt"] + "\n"
        evidence.append(item)
        blocks.append(block)
        used += len(block)
    return "\n".join(blocks), evidence


def _validated_cards(payload, evidence):
    valid_refs = set(range(1, len(evidence) + 1))
    cards = []
    for raw in payload.get("flashcards", []) if isinstance(payload.get("flashcards", []), list) else []:
        cards.append(_card(raw, "flashcard", valid_refs))
    for raw in payload.get("mcqs", []) if isinstance(payload.get("mcqs", []), list) else []:
        cards.append(_card(raw, "mcq", valid_refs))
    for raw in payload.get("short_answers", []) if isinstance(payload.get("short_answers", []), list) else []:
        cards.append(_card(raw, "short_answer", valid_refs))
    return [card for card in cards if card is not None]


_LOCAL_STOPWORDS = {
    "about", "after", "again", "also", "because", "before", "being", "between",
    "could", "every", "from", "have", "into", "itself", "more", "other", "over",
    "should", "such", "than", "that", "their", "there", "these", "they", "this",
    "those", "through", "under", "using", "which", "while", "with", "would",
}


def _local_extractive_cards(evidence: list[dict], limit: int = 10) -> list[dict]:
    """Create conservative cards without a model, using only indexed evidence.

    The answer is always a verbatim token from the supplied excerpt. Short or
    unstructured excerpts are rejected instead of being padded with guesses.
    """
    cards: list[dict] = []
    seen_units: set[int] = set()
    for item in evidence:
        unit_id = int(item["unit_id"])
        if unit_id in seen_units:
            continue
        seen_units.add(unit_id)
        excerpt = " ".join(str(item.get("excerpt") or "").split())
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", excerpt) if part.strip()]
        if not sentences and excerpt:
            sentences = [excerpt]
        for sentence in sentences:
            words = re.findall(r"[A-Za-z][A-Za-z0-9-]{4,}", sentence)
            if len(sentence.split()) < 8:
                continue
            candidates = [
                word for word in words
                if word.casefold() not in _LOCAL_STOPWORDS and not word.isdigit()
            ]
            if not candidates:
                continue
            answer = max(candidates, key=lambda value: (len(value), value.casefold()))
            cloze = re.sub(rf"\b{re.escape(answer)}\b", "_____", sentence, count=1)
            if cloze == sentence:
                continue
            reference = int(item["reference"])
            cards.append({
                "card_type": "flashcard",
                "front": f"Complete the statement from {item['title']}: {cloze}",
                "back": answer,
                "options": None,
                "explanation": "The answer is taken directly from the indexed excerpt.",
                "source_refs": [reference],
            })
            if len(cards) >= limit:
                return cards
            break
    return cards


def _card(raw, kind, valid_refs):
    if not isinstance(raw, dict):
        return None
    refs = raw.get("source_refs")
    if not isinstance(refs, list) or not refs or any(not isinstance(ref, int) or ref not in valid_refs for ref in refs):
        return None
    front = str(raw.get("front") or raw.get("question") or "").strip()
    back = str(raw.get("back") or raw.get("answer") or "").strip()
    if not front or not back:
        return None
    options = raw.get("options")
    if kind == "mcq":
        if not isinstance(options, list) or len(options) != 4 or any(not str(option).strip() for option in options):
            return None
        if back not in [str(option) for option in options]:
            return None
    explanation = str(raw.get("explanation") or "").strip() or None
    return {"card_type": kind, "front": front, "back": back, "options": options,
            "explanation": explanation, "source_refs": list(dict.fromkeys(refs))}


def _stored_options(item):
    if item.get("card_type") == "mcq":
        return {"options": item.get("options") or [], "explanation": item.get("explanation")}
    return item.get("options")


def _card_payload(connection, card_id, item, refs, evidence):
    return {"card_id": card_id, **item, "evidence": [evidence[ref - 1] for ref in refs]}


def _card_payload_from_row(connection, card_id):
    row = connection.execute(
        "SELECT f.*, s.title, s.original_path, s.source_type, u.page_or_slide, u.extracted_text "
        "FROM flashcards f LEFT JOIN sources s ON s.source_id = f.source_id LEFT JOIN document_units u ON u.unit_id = f.unit_id "
        "WHERE f.card_id = ?", (card_id,)
    ).fetchone()
    if row is None:
        return None
    evidence = [{"source_id": row["source_id"], "unit_id": row["unit_id"], "title": row["title"],
                 "filename": Path(row["original_path"]).name if row["original_path"] else None,
                 "page": row["page_or_slide"], "excerpt": _excerpt(row["extracted_text"] or ""), "source_type": row["source_type"]}]
    stored = json.loads(row["options_json"]) if row["options_json"] else None
    if isinstance(stored, dict):
        options, explanation = stored.get("options"), stored.get("explanation")
    else:
        options, explanation = stored, None
    return {"card_id": row["card_id"], "card_type": row["card_type"], "front": row["front"], "back": row["back"],
            "options": options, "explanation": explanation, "next_review": row["next_review"], "evidence": evidence}


def _validate_scope(connection, source_id, object_id):
    if source_id is not None and connection.execute("SELECT 1 FROM sources WHERE source_id = ?", (source_id,)).fetchone() is None:
        raise LookupError("source not found")
    if object_id is not None and connection.execute("SELECT 1 FROM objects WHERE object_id = ?", (object_id,)).fetchone() is None:
        raise LookupError("entity not found")


def _utc(value):
    if value is None:
        return datetime.now(UTC).isoformat()
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


def _invoke_generation(provider, prompt, content_hash, connection):
    if "system_instruction" in inspect.signature(provider.generate).parameters:
        return provider.generate(prompt, content_hash, connection, system_instruction=STUDY_SYSTEM_INSTRUCTION)
    return provider.generate(prompt, content_hash, connection)

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import re
import sqlite3

from app.ai.generation import GenerationProvider, build_generation_provider
from app.ai.providers import CloudAIDisabled, ProviderUnavailable
from app.config import Settings
from app.db.database import get_connection
from app.rag.conversations import (
    append_message,
    conversation_exists,
    create_conversation,
    history_for_prompt,
)
from app.retrieval.hybrid import retrieve_hybrid


INSUFFICIENT_EVIDENCE = "I couldn't find enough indexed evidence to answer that question."
NO_SOURCES_INDEXED = "No sources have been indexed yet. Capture or mount a file, then run indexing."
MAX_CONTEXT_CHARS = 24000


def ask_question(
    settings: Settings,
    question: str,
    conversation_id: int | None = None,
    generation_provider: GenerationProvider | None = None,
    max_context_chars: int = MAX_CONTEXT_CHARS,
) -> dict:
    question = question.strip()
    if not question:
        raise ValueError("question must not be empty")
    with get_connection(settings.database_path) as connection:
        if conversation_id is None:
            conversation_id = create_conversation(connection, question[:80])
        elif not conversation_exists(connection, conversation_id):
            raise LookupError("conversation not found")

        history = history_for_prompt(connection, conversation_id)
        user_message_id = append_message(connection, conversation_id, "user", question)
        retrieval = retrieve_hybrid(connection, settings, question, "hybrid_rerank", limit=50)
        evidence = _verified_evidence(connection, retrieval["results"])

        if not evidence:
            indexed_count = connection.execute(
                "SELECT COUNT(*) FROM sources WHERE indexing_status IN ('done', 'partial') AND excluded = 0"
            ).fetchone()[0]
            no_sources = indexed_count == 0
            no_evidence_message = NO_SOURCES_INDEXED if no_sources else INSUFFICIENT_EVIDENCE
            assistant_message_id = append_message(
                connection, conversation_id, "assistant", no_evidence_message
            )
            return _response(
                conversation_id,
                user_message_id,
                assistant_message_id,
                no_evidence_message,
                [],
                retrieval,
                "no_sources_indexed" if no_sources else "insufficient_evidence",
                0,
            )

        context, citations = _build_context(evidence, max_context_chars)
        prompt = _grounded_prompt(question, history, context)
        system_instruction = _grounded_system_instruction(len(citations))
        if generation_provider is None:
            try:
                generation_provider = build_generation_provider(settings)
            except (CloudAIDisabled, ProviderUnavailable) as exc:
                message = str(exc)
                assistant_message_id = append_message(connection, conversation_id, "assistant", message)
                return _response(
                    conversation_id, user_message_id, assistant_message_id, message, [], retrieval,
                    "cloud_disabled" if isinstance(exc, CloudAIDisabled) else "provider_unavailable", 0,
                    error=message,
                )
        content_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        try:
            raw = _invoke_generation(
                generation_provider,
                prompt,
                content_hash,
                connection,
                system_instruction,
            )
        except Exception as exc:  # noqa: BLE001 - provider boundary keeps secrets/content out of errors
            message = f"Generation provider failed: {type(exc).__name__}"
            assistant_message_id = append_message(connection, conversation_id, "assistant", message)
            return _response(
                conversation_id, user_message_id, assistant_message_id, message, [], retrieval,
                "provider_error", 0, error=message,
            )

        answer, reference_numbers = _parse_generation(raw)
        valid_numbers = [number for number in reference_numbers if 1 <= number <= len(citations)]
        valid_numbers = list(dict.fromkeys(valid_numbers))
        selected_citations = [citations[number - 1] for number in valid_numbers]
        answer = _remove_invalid_inline_citations(answer, len(citations))
        if not selected_citations:
            answer = INSUFFICIENT_EVIDENCE
        assistant_message_id = append_message(
            connection, conversation_id, "assistant", answer, selected_citations
        )
        return _response(
            conversation_id,
            user_message_id,
            assistant_message_id,
            answer,
            selected_citations,
            retrieval,
            "answered" if selected_citations else "insufficient_evidence",
            len(context),
        )


def _verified_evidence(connection: sqlite3.Connection, results: list[dict]) -> list[dict]:
    verified = []
    seen_units: set[int] = set()
    for result in results:
        unit_id = int(result.get("unit_id", 0))
        if unit_id in seen_units:
            continue
        row = connection.execute(
            "SELECT u.unit_id, u.source_id, u.page_or_slide, u.extracted_text, u.short_comment, "
            "s.title, s.original_path, s.source_type, s.capture_url "
            "FROM document_units u JOIN sources s ON s.source_id = u.source_id WHERE u.unit_id = ?",
            (unit_id,),
        ).fetchone()
        if row is None or int(row["source_id"]) != int(result.get("source_id", -1)):
            continue
        seen_units.add(unit_id)
        item = dict(result)
        item["extracted_text"] = row["extracted_text"]
        item["short_comment"] = row["short_comment"]
        item["title"] = row["title"]
        item["filename"] = Path(row["original_path"]).name
        item["source_type"] = row["source_type"]
        item["capture_url"] = row["capture_url"]
        item["page"] = row["page_or_slide"]
        verified.append(item)
    return verified


def _build_context(evidence: list[dict], max_chars: int) -> tuple[str, list[dict]]:
    chunks: list[str] = []
    citations: list[dict] = []
    used = 0
    for result in evidence:
        excerpt = _excerpt(result.get("extracted_text") or result.get("short_comment") or "")
        citation = {
            "reference": len(citations) + 1,
            "source_id": int(result["source_id"]),
            "unit_id": int(result["unit_id"]),
            "title": result.get("title") or result.get("filename") or "Untitled source",
            "filename": result.get("filename"),
            "page": result.get("page"),
            "excerpt": excerpt,
            "source_url": result.get("capture_url") or f"/sources/{int(result['source_id'])}/file",
        }
        block = (
            f"[{citation['reference']}] {citation['title']} ({citation['filename']})"
            f"{f', page {citation['page']}' if citation['page'] is not None else ''}\n"
            f"Excerpt: {excerpt}\n"
        )
        if chunks and used + len(block) > max_chars:
            break
        if not chunks and len(block) > max_chars:
            block = block[:max_chars]
        chunks.append(block)
        citations.append(citation)
        used += len(block)
    return "\n".join(chunks), citations


def _grounded_prompt(question: str, history: list[dict], context: str) -> str:
    history_text = "\n".join(f"{item['role'].title()}: {item['content']}" for item in history[-12:])
    return f"""Conversation context:
{history_text or '(none)'}

Retrieved evidence:
{context}

Question:
{question}
"""


def _grounded_system_instruction(citation_count: int) -> str:
    return f"""You are Recall's grounded memory assistant.

Answer only from the supplied retrieved evidence and local conversation context. Cite every factual claim using one or more supplied reference numbers like [1]. Never invent file contents, page numbers, titles, or citations. Distinguish direct evidence from reasonable inference. If the evidence is insufficient, say so clearly. Never claim that a source supports information absent from its excerpt.

Return JSON only with exactly these keys: answer (string), citations (array of integer reference numbers). Citation numbers must be between 1 and {citation_count}. Use an empty citations array only when no factual answer is supported.
"""


def _invoke_generation(
    provider: GenerationProvider,
    prompt: str,
    content_hash: str,
    connection: sqlite3.Connection,
    system_instruction: str,
) -> str:
    """Call newer providers with a system instruction while preserving old test doubles."""
    parameters = inspect.signature(provider.generate).parameters
    if "system_instruction" in parameters:
        return provider.generate(
            prompt,
            content_hash,
            connection,
            system_instruction=system_instruction,
        )
    return provider.generate(prompt, content_hash, connection)


def _parse_generation(raw: str) -> tuple[str, list[int]]:
    text = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return text, [int(value) for value in re.findall(r"\[(\d+)\]", text)]
    if not isinstance(payload, dict):
        return text, []
    answer = str(payload.get("answer", "")).strip()
    references = payload.get("citations", [])
    numbers = [int(value) for value in references if isinstance(value, int) and not isinstance(value, bool)]
    return answer or INSUFFICIENT_EVIDENCE, numbers


def _remove_invalid_inline_citations(answer: str, citation_count: int) -> str:
    return re.sub(
        r"\[(\d+)\]",
        lambda match: match.group(0)
        if 1 <= int(match.group(1)) <= citation_count
        else "",
        answer,
    ).strip()


def _excerpt(value: str, limit: int = 800) -> str:
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _response(
    conversation_id: int,
    user_message_id: int,
    assistant_message_id: int,
    answer: str,
    citations: list[dict],
    retrieval: dict,
    status: str,
    context_characters: int,
    error: str | None = None,
) -> dict:
    response = {
        "conversation_id": conversation_id,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
        "answer": answer,
        "citations": citations,
        "status": status,
        "retrieval": {
            "evidence_count": len(citations),
            "total_candidates": retrieval["total"],
            "context_characters": context_characters,
            "confidence": "evidence" if citations else "none",
        },
    }
    if error:
        response["error"] = error
    return response

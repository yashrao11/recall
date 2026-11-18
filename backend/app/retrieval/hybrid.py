from __future__ import annotations

import sqlite3

from app.ai.providers import ProviderUnavailable, build_embedding_provider
from app.config import Settings
from app.retrieval.fts_search import keyword_search
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.reranker import rerank
from app.retrieval.semantic_search import vector_search
from app.retrieval.visual_search import visual_search


def retrieve_hybrid(
    connection: sqlite3.Connection,
    settings: Settings,
    query: str,
    mode: str = "hybrid_rerank",
    limit: int = 20,
    source_type: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    provider_builder=build_embedding_provider,
) -> dict[str, object]:
    """Run the existing keyword/semantic/visual/RRF pipeline for API and RAG callers."""
    keyword_results = keyword_search(connection, query, 50, source_type, from_date, to_date)
    provider = None
    notice = None
    try:
        provider = provider_builder(settings)
    except (ProviderUnavailable, ValueError) as exc:
        notice = str(exc)

    semantic_results: list[dict] = []
    visual_results: list[dict] = []
    if provider is not None and mode in {"semantic", "hybrid", "hybrid_rerank"}:
        semantic_results = vector_search(connection, settings, provider, query, "semantic")
    if provider is not None and mode in {"visual", "hybrid", "hybrid_rerank"}:
        if getattr(provider, "supports_visual", False):
            visual_results = visual_search(connection, settings, provider, query)
        elif mode == "visual":
            notice = f"Visual retrieval is unavailable: embedding provider '{provider.name}' is text-only"

    if mode == "keyword":
        results = keyword_results
    elif mode == "semantic":
        results = semantic_results
    elif mode == "visual":
        results = visual_results
    else:
        lists = [keyword_results]
        if semantic_results:
            lists.append(semantic_results)
        if visual_results:
            lists.append(visual_results)
        results = reciprocal_rank_fusion(lists)
        if mode == "hybrid_rerank":
            results = rerank(query, results, limit)

    return {
        "results": results[:limit],
        "total": len(results),
        "mode": mode,
        "embedding_operational": provider is not None,
        "visual_retrieval_operational": bool(
            provider is not None and getattr(provider, "supports_visual", False)
        ),
        "notice": notice,
    }

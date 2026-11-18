from __future__ import annotations

from fastapi import APIRouter, Request

from app.ai.providers import ProviderUnavailable, build_embedding_provider
from app.db.database import get_connection
from app.retrieval.fts_search import keyword_search
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.reranker import rerank
from app.retrieval.semantic_search import vector_search

router = APIRouter(prefix="/search", tags=["search"])


@router.get("")
def search(
    request: Request,
    q: str,
    mode: str = "hybrid_rerank",
    type: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 20,
) -> dict:
    if mode not in {"keyword", "semantic", "visual", "hybrid", "hybrid_rerank"}:
        return {"results": [], "total": 0, "error": "unsupported search mode"}
    settings = request.app.state.settings
    with get_connection(settings.database_path) as connection:
        keyword_results = keyword_search(connection, q, 50, type, from_date, to_date)
        provider = None
        try:
            provider = build_embedding_provider(settings)
        except (ProviderUnavailable, ValueError):
            pass
        vector_results = []
        if provider is not None and mode in {"semantic", "visual", "hybrid", "hybrid_rerank"}:
            vector_results = vector_search(connection, settings, provider, q, mode if mode != "semantic" else "semantic")

        if mode == "keyword":
            results = keyword_results
        elif mode in {"semantic", "visual"}:
            results = vector_results
        else:
            lists = [keyword_results]
            if vector_results:
                lists.append(vector_results)
            results = reciprocal_rank_fusion(lists)
            if mode == "hybrid_rerank":
                results = rerank(q, results, limit)
        return {"results": results[:limit], "total": len(results), "mode": mode}


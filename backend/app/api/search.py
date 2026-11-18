from __future__ import annotations

from fastapi import APIRouter, Request

from app.ai.providers import ProviderUnavailable, build_embedding_provider
from app.db.database import get_connection
from app.retrieval.fts_search import keyword_search
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.reranker import rerank
from app.retrieval.semantic_search import vector_search
from app.retrieval.visual_search import visual_search

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
        notice = None
        try:
            provider = build_embedding_provider(settings)
        except (ProviderUnavailable, ValueError) as exc:
            notice = str(exc)
        semantic_results: list[dict] = []
        visual_results: list[dict] = []
        if provider is not None and mode in {"semantic", "hybrid", "hybrid_rerank"}:
            semantic_results = vector_search(connection, settings, provider, q, "semantic")
        if provider is not None and mode in {"visual", "hybrid", "hybrid_rerank"}:
            if getattr(provider, "supports_visual", False):
                visual_results = visual_search(connection, settings, provider, q)
            elif mode == "visual":
                notice = (
                    f"Visual retrieval is unavailable: embedding provider '{provider.name}' is text-only"
                )

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
                results = rerank(q, results, limit)
        response = {
            "results": results[:limit],
            "total": len(results),
            "mode": mode,
            "embedding_operational": provider is not None,
            "visual_retrieval_operational": bool(
                provider is not None and getattr(provider, "supports_visual", False)
            ),
        }
        if notice:
            response["notice"] = notice
        return response


from __future__ import annotations

from fastapi import APIRouter, Request

from app.ai.providers import build_embedding_provider
from app.db.database import get_connection
from app.retrieval.hybrid import retrieve_hybrid

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
        response = retrieve_hybrid(
            connection, settings, q, mode, limit, type, from_date, to_date,
            provider_builder=build_embedding_provider,
            group_sources=True,
        )
        if not response["notice"]:
            response.pop("notice")
        return response


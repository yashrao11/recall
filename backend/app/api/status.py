from fastapi import APIRouter, Request

from app.db.database import get_connection
from app.ai.providers import (
    CloudAIDisabled,
    LocalMetadataProvider,
    ProviderUnavailable,
    build_embedding_provider,
    build_metadata_provider,
    provider_status,
)
from app.ingestion.indexer import queue_reindex, run_pending

router = APIRouter(prefix="/index", tags=["indexing"])


@router.get("/status")
def indexing_status(request: Request) -> dict:


    with get_connection(request.app.state.settings.database_path) as connection:
        counts = {
            row["indexing_status"]: row["count"]
            for row in connection.execute(
                "SELECT indexing_status, COUNT(*) AS count FROM sources GROUP BY indexing_status"
            )
        }
        excluded = connection.execute(
            "SELECT COUNT(*) FROM sources WHERE excluded = 1"
        ).fetchone()[0]
        mounts = connection.execute("SELECT COUNT(*) FROM folder_mounts WHERE enabled = 1").fetchone()[0]
    return {
        "mounts": mounts,
        "total": sum(counts.values()),
        "pending": counts.get("pending", 0),
        "processing": counts.get("processing", 0),
        "done": counts.get("done", 0),
        "ready": counts.get("done", 0),
        "partial": counts.get("partial", 0),
        "errors": counts.get("error", 0),
        "failed": counts.get("error", 0),
        "excluded": excluded,
        "paused": request.app.state.indexing_paused,
    }


@router.post("/pause")
def pause_indexing(request: Request) -> dict:
    request.app.state.indexing_paused = True
    return {"paused": True}


@router.post("/resume")
def resume_indexing(request: Request) -> dict:
    request.app.state.indexing_paused = False
    return {"paused": False}


@router.post("/run")
def run_indexing(request: Request, mount_id: int | None = None, reindex_existing: bool = False) -> dict:
    if request.app.state.indexing_paused:
        return {"queued": 0, "completed": 0, "partial": 0, "errors": 0, "paused": True}
    settings = request.app.state.settings
    requeued = queue_reindex(settings, mount_id) if reindex_existing else 0
    embedding_provider = None
    warnings: list[str] = []
    try:
        embedding_provider = build_embedding_provider(settings)
    except (CloudAIDisabled, ProviderUnavailable, ValueError) as exc:
        # Text/metadata ingestion remains usable offline; semantic vectors are optional,
        # but the caller must be told exactly why they are unavailable.
        warnings.append(str(exc))
    try:
        metadata_provider = build_metadata_provider(settings)
    except ProviderUnavailable as exc:
        warnings.append(str(exc))
        metadata_provider = LocalMetadataProvider()
    result = run_pending(settings, embedding_provider, metadata_provider, mount_id=mount_id)
    warnings.extend(str(item) for item in result.get("warnings", []))
    result.update(
        {
            "requeued": requeued,
            "cloud_ai_permitted": settings.allow_cloud_ai,
            "embedding_operational": embedding_provider is not None,
            "warnings": warnings,
        }
    )
    return result


@router.get("/config")
def configuration_status(request: Request) -> dict[str, object]:
    return provider_status(request.app.state.settings)


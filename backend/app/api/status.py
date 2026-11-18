from fastapi import APIRouter, Request

from app.db.database import get_connection
from app.ai.providers import ProviderUnavailable, build_embedding_provider, build_metadata_provider
from app.ingestion.indexer import run_pending

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
        mounts = connection.execute("SELECT COUNT(*) FROM folder_mounts WHERE enabled = 1").fetchone()[0]
    return {
        "mounts": mounts,
        "total": sum(counts.values()),
        "pending": counts.get("pending", 0),
        "processing": counts.get("processing", 0),
        "done": counts.get("done", 0),
        "errors": counts.get("error", 0),
        "skipped": counts.get("skipped", 0),
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
def run_indexing(request: Request) -> dict:
    if request.app.state.indexing_paused:
        return {"queued": 0, "completed": 0, "errors": 0, "paused": True}
    settings = request.app.state.settings
    embedding_provider = None
    try:
        embedding_provider = build_embedding_provider(settings)
    except (ProviderUnavailable, ValueError):
        # Text/metadata ingestion remains usable offline; semantic vectors are optional.
        embedding_provider = None
    return run_pending(settings, embedding_provider, build_metadata_provider(settings))


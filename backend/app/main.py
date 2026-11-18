from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.sources import router as sources_router
from app.api.status import router as status_router
from app.api.units import router as units_router
from app.api.search import router as search_router
from app.api.calendar import router as calendar_router
from app.api.ask import router as ask_router
from app.config import Settings, get_settings
from app.db.database import initialize_database


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime_settings.data_dir.mkdir(parents=True, exist_ok=True)
        runtime_settings.derived_dir.mkdir(parents=True, exist_ok=True)
        initialize_database(runtime_settings.database_path)
        yield

    app = FastAPI(title=runtime_settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.settings = runtime_settings
    app.state.indexing_paused = False
    app.add_middleware(
        CORSMiddleware,
        allow_origins=runtime_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(sources_router)
    app.include_router(status_router)
    app.include_router(units_router)
    app.include_router(search_router)
    app.include_router(calendar_router)
    app.include_router(ask_router)

    @app.get("/health", tags=["system"])
    def health() -> dict:
        return {"status": "ok", "service": "recall-api", "version": "0.1.0"}

    return app


app = create_app()


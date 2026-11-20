from dataclasses import dataclass, field
from functools import lru_cache
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_project_environment(env_path: Path | None = None) -> Path:
    """Load the project environment without overriding process-level variables."""
    path = env_path or PROJECT_ROOT / ".env"
    load_dotenv(dotenv_path=path, override=False)
    return path


def resolve_data_dir(value: str | Path) -> Path:
    """Resolve DATA_DIR consistently regardless of the process working directory."""
    configured = Path(value).expanduser()
    if configured.is_absolute():
        return configured
    project_path = (PROJECT_ROOT / configured).resolve()
    legacy_path = (PROJECT_ROOT / "backend" / configured).resolve()
    project_database = project_path / "recall.sqlite3"
    legacy_database = legacy_path / "recall.sqlite3"
    if legacy_database.exists() and not project_database.exists():
        return legacy_path
    if legacy_database.exists() and project_database.exists() and legacy_database.stat().st_mtime > project_database.stat().st_mtime:
        return legacy_path
    return project_path


@dataclass(frozen=True)
class Settings:
    """Runtime configuration loaded from environment variables or `.env`."""

    app_name: str = "Recall"
    environment: str = "development"
    data_dir: Path = field(default_factory=lambda: Path.home() / ".local" / "share" / "recall")
    allow_cloud_ai: bool = False
    llm_provider: str = "gemini"
    visual_model: str = "siglip"
    embedding_provider: str = "gemini"
    embedding_dimension: int = 768
    gemini_api_key: str | None = None
    gemini_embedding_model: str = "gemini-embedding-2"
    gemini_metadata_model: str = "gemini-2.5-flash"
    gemini_generation_model: str = "gemini-3.1-flash-lite"
    gemini_generation_fallback_models: tuple[str, ...] = (
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
    )
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])

    @property
    def database_path(self) -> Path:
        return self.data_dir / "recall.sqlite3"

    @property
    def derived_dir(self) -> Path:
        return self.data_dir / "derived"


@lru_cache
def get_settings() -> Settings:
    load_project_environment()
    raw_origins = os.getenv("CORS_ORIGINS")
    origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()] if raw_origins else []
    fallback_models = tuple(
        model.strip()
        for model in os.getenv(
            "GEMINI_GENERATION_FALLBACK_MODELS",
            "gemini-3.5-flash-lite,gemini-3.8-flash,gemini-2.5-flash-lite,gemini-2.5-flash",
        ).split(",")
        if model.strip()
    )
    for port in (5173, 5174, 5175):
        for host in ("localhost", "127.0.0.1"):
            origin = f"http://{host}:{port}"
            if origin not in origins:
                origins.append(origin)
    return Settings(
        app_name=os.getenv("APP_NAME", "Recall"),
        environment=os.getenv("ENVIRONMENT", "development"),
        data_dir=resolve_data_dir(os.getenv("DATA_DIR", str(Path.home() / ".local" / "share" / "recall"))),
        allow_cloud_ai=os.getenv("ALLOW_CLOUD_AI", "false").lower() in {"1", "true", "yes"},
        llm_provider=os.getenv("LLM_PROVIDER", "gemini"),
        visual_model=os.getenv("VISUAL_MODEL", "siglip"),
        embedding_provider=os.getenv("EMBEDDING_PROVIDER", "gemini"),
        embedding_dimension=int(os.getenv("EMBEDDING_DIMENSION", "768")),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        gemini_embedding_model=os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-2"),
        gemini_metadata_model=os.getenv("GEMINI_METADATA_MODEL", "gemini-2.5-flash"),
        gemini_generation_model=os.getenv("GEMINI_GENERATION_MODEL", "gemini-3.1-flash-lite"),
        gemini_generation_fallback_models=fallback_models,
        cors_origins=origins,
    )

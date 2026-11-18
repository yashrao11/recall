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
    cors_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

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
    origins = (
        [origin.strip() for origin in raw_origins.split(",") if origin.strip()]
        if raw_origins
        else ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    return Settings(
        app_name=os.getenv("APP_NAME", "Recall"),
        environment=os.getenv("ENVIRONMENT", "development"),
        data_dir=Path(os.getenv("DATA_DIR", str(Path.home() / ".local" / "share" / "recall"))),
        allow_cloud_ai=os.getenv("ALLOW_CLOUD_AI", "false").lower() in {"1", "true", "yes"},
        llm_provider=os.getenv("LLM_PROVIDER", "gemini"),
        visual_model=os.getenv("VISUAL_MODEL", "siglip"),
        embedding_provider=os.getenv("EMBEDDING_PROVIDER", "gemini"),
        embedding_dimension=int(os.getenv("EMBEDDING_DIMENSION", "768")),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        gemini_embedding_model=os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-2"),
        gemini_metadata_model=os.getenv("GEMINI_METADATA_MODEL", "gemini-2.5-flash"),
        gemini_generation_model=os.getenv("GEMINI_GENERATION_MODEL", "gemini-3.1-flash-lite"),
        cors_origins=origins,
    )


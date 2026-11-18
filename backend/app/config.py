from dataclasses import dataclass, field
from functools import lru_cache
import os
from pathlib import Path

@dataclass(frozen=True)
class Settings:
    """Runtime configuration loaded from environment variables or `.env`."""

    app_name: str = "Recall"
    environment: str = "development"
    data_dir: Path = field(default_factory=lambda: Path.home() / ".local" / "share" / "recall")
    allow_cloud_ai: bool = False
    llm_provider: str = "ollama"
    visual_model: str = "siglip"
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
        llm_provider=os.getenv("LLM_PROVIDER", "ollama"),
        visual_model=os.getenv("VISUAL_MODEL", "siglip"),
        cors_origins=origins,
    )


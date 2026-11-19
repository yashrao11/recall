from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import sqlite3

from app.ai.providers import CloudAIDisabled, ProviderConfigurationError, ProviderUnavailable
from app.config import Settings
from app.db.database import cache_key, get_cached_response, put_cached_response


class GenerationProviderError(RuntimeError):
    """Raised when the configured generation service cannot answer."""


class GenerationProvider:
    name: str
    model: str

    def generate(
        self,
        prompt: str,
        content_hash: str,
        connection: sqlite3.Connection,
        *,
        system_instruction: str | None = None,
    ) -> str:
        raise NotImplementedError


@dataclass
class GeminiGenerationProvider(GenerationProvider):
    api_key: str
    allow_cloud_ai: bool = False
    model: str = "gemini-3.1-flash-lite"
    name: str = "gemini"

    def __post_init__(self) -> None:
        self._ensure_cloud_allowed()
        if not self.api_key.strip():
            raise ProviderConfigurationError(
                "GEMINI_API_KEY is required when cloud AI is enabled for generation"
            )
        try:
            from google import genai
        except ImportError as exc:
            raise ProviderUnavailable("Install the optional 'ai' extra for Gemini generation") from exc
        self._client = genai.Client(api_key=self.api_key)
        if not hasattr(self._client, "interactions"):
            raise ProviderUnavailable("Installed google-genai SDK does not support the Interactions API")

    def _ensure_cloud_allowed(self) -> None:
        if not self.allow_cloud_ai:
            raise CloudAIDisabled(
                "Cloud AI is disabled (ALLOW_CLOUD_AI=false); Gemini generation is unavailable"
            )

    def generate(
        self,
        prompt: str,
        content_hash: str,
        connection: sqlite3.Connection,
        *,
        system_instruction: str | None = None,
    ) -> str:
        self._ensure_cloud_allowed()
        key = cache_key(self.name, "generate", content_hash)
        cached = get_cached_response(connection, key)
        if cached is not None:
            return str(cached)
        request = {
            "model": self.model,
            "store": False,
            "input": prompt,
        }
        if system_instruction:
            request["system_instruction"] = system_instruction
        # Safe diagnostics for smoke tests and operational status; never retain prompt or secrets.
        self.last_request_metadata = {
            "model": self.model,
            "store": False,
            "has_system_instruction": bool(system_instruction),
        }
        try:
            interaction = self._client.interactions.create(**request)
            output = _extract_output_text(interaction)
        except Exception as exc:  # noqa: BLE001 - provider boundary must normalize SDK errors
            code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            detail = "rate limit" if code == 429 else f"HTTP {code}" if code else type(exc).__name__
            raise GenerationProviderError(f"Gemini generation failed ({detail})") from exc
        if not output.strip():
            raise GenerationProviderError("Gemini generation returned an empty response")
        put_cached_response(connection, key, content_hash, self.name, "generate", output)
        return output


def build_generation_provider(settings: Settings) -> GenerationProvider:
    if not settings.allow_cloud_ai:
        raise CloudAIDisabled(
            "Cloud AI is disabled (ALLOW_CLOUD_AI=false); Ask My Memory generation is unavailable"
        )
    if settings.llm_provider.lower() not in {"gemini", "google"}:
        raise ProviderUnavailable(
            f"Unsupported generation provider '{settings.llm_provider}'; configure LLM_PROVIDER=gemini"
        )
    if not settings.gemini_api_key:
        raise ProviderConfigurationError(
            "GEMINI_API_KEY is required when ALLOW_CLOUD_AI=true for generation"
        )
    if importlib.util.find_spec("google.genai") is None:
        raise ProviderUnavailable("Install the optional 'ai' extra for Gemini generation")
    return GeminiGenerationProvider(
        api_key=settings.gemini_api_key,
        allow_cloud_ai=True,
        model=settings.gemini_generation_model,
    )


def _extract_output_text(interaction: object) -> str:
    output_text = getattr(interaction, "output_text", None)
    if isinstance(output_text, str) and output_text:
        return output_text
    steps = getattr(interaction, "steps", None) or []
    chunks: list[str] = []
    for step in steps:
        if getattr(step, "type", None) not in {None, "model_output"}:
            continue
        for content in getattr(step, "content", None) or []:
            text = getattr(content, "text", None)
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks)

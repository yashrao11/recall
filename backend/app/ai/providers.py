from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
import re
from typing import Protocol

import sqlite3

from app.config import Settings
from app.db.database import cache_key, get_cached_response, put_cached_response


class ProviderUnavailable(RuntimeError):
    """Raised when an optional local or cloud model is not configured."""


class CloudAIDisabled(ProviderUnavailable):
    """Raised whenever a cloud provider is requested while cloud AI is disabled."""


class ProviderConfigurationError(ProviderUnavailable):
    """Raised when a permitted provider is missing required configuration."""


class EmbeddingProvider(Protocol):
    name: str
    dimension: int
    supports_visual: bool

    def embed_text(self, text: str, content_hash: str, connection: sqlite3.Connection) -> list[float]: ...

    def embed_image(
        self, image_bytes: bytes, mime_type: str, content_hash: str, connection: sqlite3.Connection
    ) -> list[float]: ...


class UnitMetadataProvider(Protocol):
    name: str

    def describe(
        self, text: str, image_bytes: bytes | None, mime_type: str | None, content_hash: str,
        connection: sqlite3.Connection,
    ) -> tuple[str, list[str]]: ...


def _provider_failure(operation: str, exc: Exception) -> ProviderUnavailable:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code == 429:
        detail = "rate limit"
    elif code:
        detail = f"HTTP {code}"
    else:
        detail = type(exc).__name__
    return ProviderUnavailable(f"Gemini {operation} failed ({detail})")


@dataclass
class GeminiProvider:
    api_key: str
    allow_cloud_ai: bool = False
    model: str = "gemini-embedding-2"
    metadata_model: str = "gemini-2.5-flash"
    dimension: int = 768
    name: str = "gemini"
    supports_visual: bool = True

    def __post_init__(self) -> None:
        self._ensure_cloud_allowed()
        if not self.api_key.strip():
            raise ProviderConfigurationError("GEMINI_API_KEY is required when cloud AI is enabled")
        try:
            from google import genai
        except ImportError as exc:
            raise ProviderUnavailable("Install the optional 'ai' extra for Gemini support") from exc
        self._genai = genai
        self._client = genai.Client(api_key=self.api_key)

    def _ensure_cloud_allowed(self) -> None:
        if not self.allow_cloud_ai:
            raise CloudAIDisabled(
                "Cloud AI is disabled (ALLOW_CLOUD_AI=false); Gemini will not be initialised or called"
            )

    def embed_text(self, text: str, content_hash: str, connection: sqlite3.Connection) -> list[float]:
        self._ensure_cloud_allowed()
        # Embedding 2 uses document formatting for corpus items in asymmetric
        # retrieval. Titles are added by the indexer at the source level where
        # available; this provider-level fallback remains valid for untitled units.
        return self._embed(
            f"title: none | text: {text}", content_hash, connection, operation="embed_text"
        )

    def embed_query(self, query: str, content_hash: str, connection: sqlite3.Connection) -> list[float]:
        """Embed a retrieval query using Embedding 2's asymmetric query format."""
        self._ensure_cloud_allowed()
        return self._embed(
            f"task: search result | query: {query}", content_hash, connection, operation="embed_query"
        )

    def embed_image(
        self, image_bytes: bytes, mime_type: str, content_hash: str, connection: sqlite3.Connection
    ) -> list[float]:
        self._ensure_cloud_allowed()
        key = cache_key(self.name, "embed_image", content_hash)
        cached = get_cached_response(connection, key)
        if cached is not None:
            return [float(value) for value in cached]
        from google.genai import types

        try:
            result = self._client.models.embed_content(
                model=self.model,
                contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime_type)],
                config=types.EmbedContentConfig(output_dimensionality=self.dimension),
            )
        except Exception as exc:  # noqa: BLE001 - normalize SDK/provider failures
            raise _provider_failure("image embedding", exc) from exc
        values = _validated_embedding(_extract_embedding(result), self.dimension)
        put_cached_response(connection, key, content_hash, self.name, "embed_image", values)
        return values

    def extract_image_text(
        self,
        image_bytes: bytes,
        mime_type: str,
        content_hash: str,
        connection: sqlite3.Connection,
    ) -> str:
        """Extract readable text from an image or scanned page using Gemini vision."""
        self._ensure_cloud_allowed()
        key = cache_key(self.name, "extract_image_text", content_hash)
        cached = get_cached_response(connection, key)
        if cached is not None:
            return str(cached)
        from google.genai import types

        try:
            response = self._client.models.generate_content(
                model=self.metadata_model,
                contents=[
                    "Transcribe only the readable text in this image. Return plain text. "
                    "If there is no readable text, return an empty string. Do not describe the image "
                    "or invent missing content.",
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                ],
            )
        except Exception as exc:  # noqa: BLE001 - normalize SDK/provider failures
            raise _provider_failure("visual text extraction", exc) from exc
        text = str(getattr(response, "text", "") or "").strip()
        put_cached_response(connection, key, content_hash, self.name, "extract_image_text", text)
        return text

    def describe(
        self, text: str, image_bytes: bytes | None, mime_type: str | None, content_hash: str,
        connection: sqlite3.Connection,
    ) -> tuple[str, list[str]]:
        self._ensure_cloud_allowed()
        key = cache_key(self.name, "describe_unit", content_hash)
        cached = get_cached_response(connection, key)
        if cached is not None:
            return str(cached["comment"]), [str(tag) for tag in cached["tags"]]

        prompt = (
            "Return JSON only with keys comment and tags. Write a concise factual comment of at most "
            "25 words describing this document unit, and 3-8 lowercase topical tags. Do not invent facts.\n\n"
            f"Text:\n{text[:12000]}"
        )
        contents: object = prompt
        if image_bytes is not None and mime_type is not None:
            from google.genai import types
            contents = [prompt, types.Part.from_bytes(data=image_bytes, mime_type=mime_type)]
        try:
            response = self._client.models.generate_content(
                model=self.metadata_model,
                contents=contents,
            )
        except Exception as exc:  # noqa: BLE001 - normalize SDK/provider failures
            raise _provider_failure("metadata generation", exc) from exc
        raw_payload = re.sub(r"^```(?:json)?\s*|\s*```$", "", response.text.strip(), flags=re.IGNORECASE)
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            raise ProviderUnavailable("Gemini metadata response was not valid JSON") from exc
        comment = str(payload.get("comment", "")).strip()
        tags = _normalise_tags(payload.get("tags", []))
        put_cached_response(
            connection, key, content_hash, self.name, "describe_unit", {"comment": comment, "tags": tags}
        )
        return comment, tags

    def _embed(
        self,
        text: str,
        content_hash: str,
        connection: sqlite3.Connection,
        operation: str,
    ) -> list[float]:
        key = cache_key(self.name, operation, content_hash)
        cached = get_cached_response(connection, key)
        if cached is not None:
            return _validated_embedding([float(value) for value in cached], self.dimension)
        from google.genai import types

        try:
            result = self._client.models.embed_content(
                model=self.model,
                contents=text,
                config=types.EmbedContentConfig(output_dimensionality=self.dimension),
            )
        except Exception as exc:  # noqa: BLE001 - normalize SDK/provider failures
            raise _provider_failure(operation, exc) from exc
        values = _validated_embedding(_extract_embedding(result), self.dimension)
        put_cached_response(connection, key, content_hash, self.name, operation, values)
        return values


@dataclass
class BGEProvider:
    model_name: str = "BAAI/bge-base-en-v1.5"
    dimension: int = 768
    name: str = "bge"
    supports_visual: bool = False

    def __post_init__(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ProviderUnavailable("Install the optional 'retrieval' extra for BGE support") from exc
        self._model = SentenceTransformer(self.model_name)

    def embed_text(self, text: str, content_hash: str, connection: sqlite3.Connection) -> list[float]:
        key = cache_key(self.name, "embed_text", content_hash)
        cached = get_cached_response(connection, key)
        if cached is not None:
            return [float(value) for value in cached]
        vector = self._model.encode([text], normalize_embeddings=True)[0].tolist()
        put_cached_response(connection, key, content_hash, self.name, "embed_text", vector)
        return [float(value) for value in vector]

    def embed_image(
        self, image_bytes: bytes, mime_type: str, content_hash: str, connection: sqlite3.Connection
    ) -> list[float]:
        raise ProviderUnavailable("BGE is the local text baseline; use Gemini for image embeddings")


@dataclass
class LocalMetadataProvider:
    name: str = "local-heuristic"

    def describe(
        self, text: str, image_bytes: bytes | None, mime_type: str | None, content_hash: str,
        connection: sqlite3.Connection,
    ) -> tuple[str, list[str]]:
        words = re.findall(r"[a-zA-Z][a-zA-Z0-9-]{2,}", text.lower())
        stopwords = {"this", "that", "with", "from", "which", "into", "about", "there", "their"}
        tags = []
        for word in words:
            if word not in stopwords and word not in tags:
                tags.append(word)
            if len(tags) == 8:
                break
        comment = " ".join(text.split())[:160]
        if not comment:
            comment = "Visual document unit"
        return comment, tags


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider.lower() == "gemini":
        if not settings.allow_cloud_ai:
            raise CloudAIDisabled(
                "Cloud AI is disabled (ALLOW_CLOUD_AI=false); embedding provider is unavailable"
            )
        if not settings.gemini_api_key:
            raise ProviderConfigurationError(
                "GEMINI_API_KEY is required when ALLOW_CLOUD_AI=true and EMBEDDING_PROVIDER=gemini"
            )
        return GeminiProvider(
            api_key=settings.gemini_api_key,
            allow_cloud_ai=True,
            model=settings.gemini_embedding_model,
            metadata_model=settings.gemini_metadata_model,
            dimension=settings.embedding_dimension,
        )
    if settings.embedding_provider.lower() == "bge":
        return BGEProvider(dimension=settings.embedding_dimension)
    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")


def build_metadata_provider(settings: Settings) -> UnitMetadataProvider:
    if not settings.allow_cloud_ai:
        return LocalMetadataProvider()
    if not settings.gemini_api_key:
        raise ProviderConfigurationError(
            "GEMINI_API_KEY is required when ALLOW_CLOUD_AI=true for Gemini metadata generation"
        )
    if settings.allow_cloud_ai and settings.gemini_api_key:
        return GeminiProvider(
            api_key=settings.gemini_api_key,
            allow_cloud_ai=True,
            model=settings.gemini_embedding_model,
            metadata_model=settings.gemini_metadata_model,
            dimension=settings.embedding_dimension,
        )
    return LocalMetadataProvider()


def provider_status(settings: Settings) -> dict[str, object]:
    """Return configuration facts only; never instantiate a provider or expose secrets."""
    configured_embedding = settings.embedding_provider.lower()
    has_gemini_package = importlib.util.find_spec("google.genai") is not None
    has_bge_package = importlib.util.find_spec("sentence_transformers") is not None

    if configured_embedding == "gemini":
        if not settings.allow_cloud_ai:
            active_embedding = "disabled (cloud AI disabled)"
            embedding_operational = False
        elif not settings.gemini_api_key:
            active_embedding = "gemini (missing credentials)"
            embedding_operational = False
        elif not has_gemini_package:
            active_embedding = "gemini (google-genai unavailable)"
            embedding_operational = False
        else:
            active_embedding = "gemini"
            embedding_operational = True
    elif configured_embedding == "bge":
        active_embedding = "bge" if has_bge_package else "bge (sentence-transformers unavailable)"
        embedding_operational = has_bge_package
    else:
        active_embedding = f"unsupported ({settings.embedding_provider})"
        embedding_operational = False

    if settings.allow_cloud_ai and settings.gemini_api_key and has_gemini_package:
        active_metadata = "gemini"
    else:
        active_metadata = "local-heuristic"

    generation_configured = settings.llm_provider.lower() in {"gemini", "google"}
    generation_operational = bool(
        settings.allow_cloud_ai and settings.gemini_api_key and has_gemini_package and generation_configured
    )
    active_generation = (
        "gemini"
        if generation_operational
        else "disabled (cloud AI disabled)"
        if not settings.allow_cloud_ai
        else f"{settings.llm_provider} (unavailable)"
    )

    return {
        "cloud_ai_permitted": settings.allow_cloud_ai,
        "active_embedding_provider": active_embedding,
        "active_metadata_provider": active_metadata,
        "active_generation_provider": active_generation,
        "generation_model": settings.gemini_generation_model,
        "local_fallback_availability": {
            "keyword_search": True,
            "metadata_heuristic": True,
            "bge": has_bge_package,
        },
        "embedding_operational": embedding_operational,
        "generation_operational": generation_operational,
        "visual_retrieval_operational": (
            configured_embedding == "gemini"
            and settings.allow_cloud_ai
            and bool(settings.gemini_api_key)
            and has_gemini_package
        ),
        "message": (
            "Cloud AI is disabled; only local keyword search and local metadata fallback are active"
            if not settings.allow_cloud_ai
            else "Cloud AI is permitted; provider status reflects installed dependencies and credentials"
        ),
    }


def _extract_embedding(result: object) -> list[float]:
    embeddings = getattr(result, "embeddings", None)
    if not embeddings:
        raise RuntimeError("Gemini returned no embedding")
    values = getattr(embeddings[0], "values", None)
    if values is None:
        raise RuntimeError("Gemini returned an embedding without values")
    return [float(value) for value in values]


def _validated_embedding(values: list[float], dimension: int) -> list[float]:
    if len(values) != dimension:
        raise RuntimeError(f"Gemini returned dimension {len(values)}; expected {dimension}")
    if not all(math.isfinite(value) for value in values):
        raise RuntimeError("Gemini returned a non-finite embedding value")
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 0:
        raise RuntimeError("Gemini returned a zero-norm embedding")
    # Embedding 2 normalizes reduced-dimensional outputs server-side. Applying
    # the same normalization locally makes the invariant explicit and protects
    # callers if the API returns a slightly different representation.
    return [value / norm for value in values]


def _normalise_tags(tags: object) -> list[str]:
    if not isinstance(tags, list):
        return []
    return [tag.strip().lower() for tag in tags if isinstance(tag, str) and tag.strip()][:8]


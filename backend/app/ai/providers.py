from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Protocol

import sqlite3

from app.config import Settings
from app.db.database import cache_key, get_cached_response, put_cached_response


class ProviderUnavailable(RuntimeError):
    """Raised when an optional local or cloud model is not configured."""


class EmbeddingProvider(Protocol):
    name: str
    dimension: int

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


@dataclass
class GeminiProvider:
    api_key: str
    model: str = "gemini-embedding-2"
    metadata_model: str = "gemini-2.5-flash"
    dimension: int = 768
    name: str = "gemini"

    def __post_init__(self) -> None:
        try:
            from google import genai
        except ImportError as exc:
            raise ProviderUnavailable("Install the optional 'ai' extra for Gemini support") from exc
        self._genai = genai
        self._client = genai.Client(api_key=self.api_key)

    def embed_text(self, text: str, content_hash: str, connection: sqlite3.Connection) -> list[float]:
        return self._embed(text, content_hash, connection)

    def embed_image(
        self, image_bytes: bytes, mime_type: str, content_hash: str, connection: sqlite3.Connection
    ) -> list[float]:
        key = cache_key(self.name, "embed_image", content_hash)
        cached = get_cached_response(connection, key)
        if cached is not None:
            return [float(value) for value in cached]
        from google.genai import types

        result = self._client.models.embed_content(
            model=self.model,
            contents=types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            config=types.EmbedContentConfig(output_dimensionality=self.dimension),
        )
        values = _extract_embedding(result)
        put_cached_response(connection, key, content_hash, self.name, "embed_image", values)
        return values

    def describe(
        self, text: str, image_bytes: bytes | None, mime_type: str | None, content_hash: str,
        connection: sqlite3.Connection,
    ) -> tuple[str, list[str]]:
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
        response = self._client.models.generate_content(
            model=self.metadata_model,
            contents=contents,
            config={"response_mime_type": "application/json"},
        )
        payload = json.loads(response.text)
        comment = str(payload.get("comment", "")).strip()
        tags = _normalise_tags(payload.get("tags", []))
        put_cached_response(
            connection, key, content_hash, self.name, "describe_unit", {"comment": comment, "tags": tags}
        )
        return comment, tags

    def _embed(self, text: str, content_hash: str, connection: sqlite3.Connection) -> list[float]:
        key = cache_key(self.name, "embed_text", content_hash)
        cached = get_cached_response(connection, key)
        if cached is not None:
            return [float(value) for value in cached]
        from google.genai import types

        result = self._client.models.embed_content(
            model=self.model,
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=self.dimension),
        )
        values = _extract_embedding(result)
        put_cached_response(connection, key, content_hash, self.name, "embed_text", values)
        return values


@dataclass
class BGEProvider:
    model_name: str = "BAAI/bge-base-en-v1.5"
    dimension: int = 768
    name: str = "bge"

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
        if not settings.gemini_api_key:
            raise ProviderUnavailable("GEMINI_API_KEY is required when EMBEDDING_PROVIDER=gemini")
        return GeminiProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_embedding_model,
            metadata_model=settings.gemini_metadata_model,
            dimension=settings.embedding_dimension,
        )
    if settings.embedding_provider.lower() == "bge":
        return BGEProvider(dimension=settings.embedding_dimension)
    raise ValueError(f"Unsupported embedding provider: {settings.embedding_provider}")


def build_metadata_provider(settings: Settings) -> UnitMetadataProvider:
    if settings.gemini_api_key:
        return GeminiProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_embedding_model,
            metadata_model=settings.gemini_metadata_model,
            dimension=settings.embedding_dimension,
        )
    return LocalMetadataProvider()


def _extract_embedding(result: object) -> list[float]:
    embeddings = getattr(result, "embeddings", None)
    if not embeddings:
        raise RuntimeError("Gemini returned no embedding")
    values = getattr(embeddings[0], "values", None)
    if values is None:
        raise RuntimeError("Gemini returned an embedding without values")
    return [float(value) for value in values]


def _normalise_tags(tags: object) -> list[str]:
    if not isinstance(tags, list):
        return []
    return [tag.strip().lower() for tag in tags if isinstance(tag, str) and tag.strip()][:8]


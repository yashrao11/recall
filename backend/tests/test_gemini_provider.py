from pathlib import Path
from types import SimpleNamespace

import pytest

from app.ai import providers
from app.config import Settings
from app.db.database import get_connection, initialize_database


class FakeModels:
    def __init__(self):
        self.calls = []

    def embed_content(self, *, model, contents, config):
        self.calls.append((model, contents, config))
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[1.0] + [0.0] * 767)])


def test_gemini_embedding_2_uses_retrieval_formats_and_cache(tmp_path: Path, monkeypatch) -> None:
    import google.genai as genai

    models = FakeModels()
    monkeypatch.setattr(genai, "Client", lambda api_key: SimpleNamespace(models=models))
    provider = providers.GeminiProvider(
        api_key="test-secret", allow_cloud_ai=True, dimension=768
    )
    database_path = tmp_path / "recall.sqlite3"
    initialize_database(database_path)

    with get_connection(database_path) as connection:
        document = provider.embed_text("document content", "document-hash", connection)
        query = provider.embed_query("natural language query", "query-hash", connection)
        provider.embed_query("natural language query", "query-hash", connection)

    assert len(document) == 768
    assert len(query) == 768
    assert models.calls[0][0] == "gemini-embedding-2"
    assert models.calls[0][1] == "title: none | text: document content"
    assert models.calls[1][1] == "task: search result | query: natural language query"
    assert len(models.calls) == 2
    assert models.calls[0][2].output_dimensionality == 768


def test_gemini_embedding_2_rejects_wrong_dimension(tmp_path: Path, monkeypatch) -> None:
    import google.genai as genai

    class WrongModels:
        def embed_content(self, **kwargs):
            return SimpleNamespace(embeddings=[SimpleNamespace(values=[1.0, 0.0])])

    monkeypatch.setattr(genai, "Client", lambda api_key: SimpleNamespace(models=WrongModels()))
    provider = providers.GeminiProvider(api_key="test-secret", allow_cloud_ai=True, dimension=768)
    database_path = tmp_path / "recall.sqlite3"
    initialize_database(database_path)
    with get_connection(database_path) as connection:
        with pytest.raises(RuntimeError, match="expected 768"):
            provider.embed_text("content", "hash", connection)

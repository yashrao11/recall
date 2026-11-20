from pathlib import Path
import os

import pytest
from fastapi.testclient import TestClient

from app import config
from app.ai import providers
from app.config import Settings
from app.main import create_app


def test_project_dotenv_loads_and_os_environment_wins(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".env").write_text(
        "APP_NAME=from-dotenv\nALLOW_CLOUD_AI=true\nGEMINI_API_KEY=dotenv-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("APP_NAME", raising=False)
    monkeypatch.delenv("ALLOW_CLOUD_AI", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    config.get_settings.cache_clear()

    loaded = config.get_settings()
    assert loaded.app_name == "from-dotenv"
    assert loaded.allow_cloud_ai is True
    assert loaded.gemini_api_key == "dotenv-secret"

    monkeypatch.setenv("APP_NAME", "from-os")
    monkeypatch.setenv("ALLOW_CLOUD_AI", "false")
    config.get_settings.cache_clear()
    overridden = config.get_settings()
    assert overridden.app_name == "from-os"
    assert overridden.allow_cloud_ai is False
    config.get_settings.cache_clear()


def test_generation_fallback_models_are_configurable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("GEMINI_GENERATION_MODEL", "primary-model")
    monkeypatch.setenv("GEMINI_GENERATION_FALLBACK_MODELS", "fallback-a, fallback-b, fallback-a")
    config.get_settings.cache_clear()

    settings = config.get_settings()

    assert settings.gemini_generation_model == "primary-model"
    assert settings.gemini_generation_fallback_models == ("fallback-a", "fallback-b", "fallback-a")
    config.get_settings.cache_clear()


def test_cloud_disabled_blocks_gemini_factory_and_execution(monkeypatch) -> None:
    settings = Settings(allow_cloud_ai=False, embedding_provider="gemini", gemini_api_key="secret")
    real_gemini_provider = providers.GeminiProvider
    constructed = False

    def forbidden_constructor(*args, **kwargs):
        nonlocal constructed
        constructed = True
        raise AssertionError("Gemini must not be constructed")

    monkeypatch.setattr(providers, "GeminiProvider", forbidden_constructor)
    with pytest.raises(providers.CloudAIDisabled):
        providers.build_embedding_provider(settings)
    assert constructed is False
    assert isinstance(providers.build_metadata_provider(settings), providers.LocalMetadataProvider)

    provider = object.__new__(real_gemini_provider)
    provider.allow_cloud_ai = False
    with pytest.raises(providers.CloudAIDisabled):
        provider.embed_text("private document", "hash", None)


def test_cloud_disabled_search_reports_local_only_and_does_not_construct_gemini(monkeypatch, tmp_path: Path) -> None:
    def forbidden_constructor(*args, **kwargs):
        raise AssertionError("Gemini must not be constructed during search")

    monkeypatch.setattr(providers, "GeminiProvider", forbidden_constructor)
    settings = Settings(
        data_dir=tmp_path / "data", allow_cloud_ai=False, embedding_provider="gemini", gemini_api_key="secret"
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/search", params={"q": "private topic", "mode": "semantic"})
        assert response.status_code == 200
        payload = response.json()
        assert payload["embedding_operational"] is False
        assert "Cloud AI is disabled" in payload["notice"]


def test_cloud_disabled_indexing_never_constructs_gemini(monkeypatch, tmp_path: Path) -> None:
    def forbidden_constructor(*args, **kwargs):
        raise AssertionError("Gemini must not be constructed during indexing")

    monkeypatch.setattr(providers, "GeminiProvider", forbidden_constructor)
    private_file = tmp_path / "private.md"
    private_file.write_text("This content must remain local.", encoding="utf-8")
    settings = Settings(
        data_dir=tmp_path / "data", allow_cloud_ai=False, embedding_provider="gemini", gemini_api_key="secret"
    )
    with TestClient(create_app(settings)) as client:
        assert client.post("/sources/folders", json={"path": str(tmp_path)}).status_code == 201
        response = client.post("/index/run")
        payload = response.json()
        assert response.status_code == 200
        assert payload["completed"] == 0
        assert payload["partial"] == 1
        assert payload["embedding_operational"] is False
        assert payload["cloud_ai_permitted"] is False
        assert any("Cloud AI is disabled" in warning for warning in payload["warnings"])


def test_cloud_enabled_factory_uses_gemini_without_real_network(monkeypatch) -> None:
    constructed = {}

    class FakeGemini:
        name = "gemini"
        dimension = 768

        def __init__(self, **kwargs):
            constructed.update(kwargs)

    monkeypatch.setattr(providers, "GeminiProvider", FakeGemini)
    settings = Settings(allow_cloud_ai=True, embedding_provider="gemini", gemini_api_key="secret")
    provider = providers.build_embedding_provider(settings)
    assert provider.name == "gemini"
    assert constructed == {
        "api_key": "secret",
        "allow_cloud_ai": True,
        "model": "gemini-embedding-2",
        "metadata_model": "gemini-2.5-flash",
        "dimension": 768,
    }


def test_missing_gemini_credentials_are_explicit() -> None:
    settings = Settings(allow_cloud_ai=True, embedding_provider="gemini", gemini_api_key=None)
    with pytest.raises(providers.ProviderConfigurationError, match="GEMINI_API_KEY"):
        providers.build_embedding_provider(settings)
    with pytest.raises(providers.ProviderConfigurationError, match="GEMINI_API_KEY"):
        providers.build_metadata_provider(settings)


def test_configuration_status_is_safe(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data", allow_cloud_ai=False, embedding_provider="gemini", gemini_api_key="do-not-leak"
    )
    with TestClient(create_app(settings)) as client:
        payload = client.get("/index/config").json()
    assert payload["cloud_ai_permitted"] is False
    assert payload["embedding_operational"] is False
    assert payload["visual_retrieval_operational"] is False
    assert payload["active_embedding_provider"] == "disabled (cloud AI disabled)"
    assert "do-not-leak" not in str(payload)
    assert "api_key" not in str(payload).lower()


from __future__ import annotations

from src.config import Settings


def test_production_contract_rejects_missing_managed_secrets(monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    settings = Settings(app_env="production")
    try:
        settings.validate_production_contract()
    except ValueError as exc:
        assert "Production configuration incomplete" in str(exc)
        assert "FIREBASE_SERVICE_ACCOUNT_JSON" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected production contract failure")


def test_production_contract_rejects_malformed_firebase_json(monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    settings = Settings(app_env="production", firebase_service_account_json="not-json")
    try:
        settings.validate_production_contract()
    except ValueError as exc:
        assert "valid service-account JSON" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected malformed Firebase credential failure")


def test_non_production_contract_allows_local_defaults():
    Settings(app_env="test").validate_production_contract()


def test_production_contract_allows_guest_mode_without_firebase(monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    settings = Settings(
        app_env="production",
        allow_guest_access=True,
        database_url="postgresql+asyncpg://example",
        qdrant_url="https://example.qdrant.io",
        qdrant_api_key="test-key",
        neo4j_uri="neo4j+s://example.databases.neo4j.io",
        neo4j_password="test-password",
        openai_api_key="test-key",
        model_name="gpt-4o-mini",
        metrics_token="test-metrics-token",
        cors_origins="https://example.vercel.app",
    )

    settings.validate_production_contract()

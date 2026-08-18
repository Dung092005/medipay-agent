from src.config import get_settings
from src.integrations.langfuse import tracing_enabled


def test_tracing_disabled_in_test_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")
    get_settings.cache_clear()
    try:
        assert tracing_enabled() is False
    finally:
        get_settings.cache_clear()


def test_langfuse_configured_when_keys_present(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")
    get_settings.cache_clear()
    try:
        assert get_settings().langfuse_configured is True
        assert tracing_enabled() is True
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        get_settings.cache_clear()

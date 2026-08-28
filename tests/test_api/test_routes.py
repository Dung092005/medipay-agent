from unittest.mock import AsyncMock, patch

import pytest

from src.api.auth import get_current_user
from src.main import app
from src.models.graph import RetrievalResult


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_metrics_endpoint_is_prometheus_text(client):
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert "medipay_metrics_registry_info" in response.text


@pytest.mark.asyncio
async def test_readiness(client):
    response = await client.get("/ready")
    assert response.status_code in {200, 503}
    assert response.json()["status"] in {"ready", "degraded"}


@pytest.mark.asyncio
async def test_chat_success(client):
    result = {
        "response": "Đã xử lý",
        "citations": [
            RetrievalResult(
                chunk_id="chunk-1",
                document_id="doc-1",
                content="Nguồn pháp lý",
                title="Luật BHYT",
                channels=["semantic"],
            ).model_dump()
        ],
    }
    with patch("src.api.routes.get_agent") as get_agent:
        get_agent.return_value.ainvoke = AsyncMock(return_value=result)
        response = await client.post("/api/v1/chat", json={"message": "Tôi cần hỗ trợ"})

    assert response.status_code == 200
    assert response.json()["response"] == "Đã xử lý"
    assert response.json()["citations"][0]["title"] == "Luật BHYT"


@pytest.mark.asyncio
async def test_chat_stream_emits_only_verified_final_event(client):
    async def events(*_args, **_kwargs):
        yield {"event": "on_chain_start", "name": "retrieve_vectors", "data": {}}
        yield {
            "event": "on_chain_end",
            "name": "guardrail",
            "data": {"output": {"response": "Đã kiểm chứng", "citations": []}},
        }

    with patch("src.api.routes.get_agent") as get_agent:
        get_agent.return_value.astream_events = events
        response = await client.post("/api/v1/chat/stream", json={"message": "Câu hỏi"})

    assert response.status_code == 200
    assert "event: status" in response.text
    assert '"response": "Đã kiểm chứng"' in response.text
    assert "event: done" in response.text


@pytest.mark.asyncio
async def test_chat_requires_authentication(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ALLOW_GUEST_ACCESS", "false")
    from src.config import get_settings
    get_settings.cache_clear()
    app.dependency_overrides.pop(get_current_user, None)
    try:
        response = await client.post("/api/v1/chat", json={"message": "Tôi cần hỗ trợ"})
    finally:
        from tests.conftest import _test_user

        app.dependency_overrides[get_current_user] = _test_user
        get_settings.cache_clear()

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_chat_rejects_removed_public_history_field(client):
    result = {"response": "Xin chào", "citations": []}
    with patch("src.api.routes.get_agent") as get_agent:
        get_agent.return_value.ainvoke = AsyncMock(return_value=result)
        response = await client.post(
            "/api/v1/chat",
            json={
                "message": "xin chào",
                "chat_history": [{"role": "user", "content": "xin chào"}],
            },
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_rejects_empty_agent_response(client):
    with patch("src.api.routes.get_agent") as get_agent:
        get_agent.return_value.ainvoke = AsyncMock(
            return_value={"response": "   ", "citations": []}
        )
        response = await client.post("/api/v1/chat", json={"message": "Xin chào"})

    assert response.status_code == 502
    assert response.json()["code"] == "provider_unavailable"
    assert "empty response" in response.json()["message"]


@pytest.mark.asyncio
async def test_chat_empty_message(client):
    response = await client.post("/api/v1/chat", json={"message": "   "})
    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_chat_message_too_long(client):
    response = await client.post("/api/v1/chat", json={"message": "x" * 5001})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_chat_rejects_oversized_http_body_before_parsing(client):
    response = await client.post(
        "/api/v1/chat",
        headers={"content-length": "200000"},
        content=b"{}",
    )
    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"
    assert response.headers["x-request-id"] == response.json()["request_id"]


@pytest.mark.asyncio
async def test_chat_rejects_history_payload_after_migration(client):
    response = await client.post(
        "/api/v1/chat",
        json={
            "message": "question",
            "chat_history": [
                {"role": "user", "content": "x" * 5000},
                {"role": "assistant", "content": "x" * 5000},
                {"role": "user", "content": "x" * 5000},
                {"role": "assistant", "content": "x" * 5000},
                {"role": "user", "content": "x"},
            ],
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_analyze_success(client):
    with patch("src.api.routes.get_agent") as get_agent:
        get_agent.return_value.ainvoke = AsyncMock(return_value={"response": "Phân tích hoàn tất"})
        response = await client.post("/api/v1/analyze", json={"message": "Phân tích hóa đơn"})

    assert response.status_code == 200
    assert response.json() == {"analysis": "Phân tích hoàn tất"}


@pytest.mark.asyncio
async def test_analyze_empty_message(client):
    response = await client.post("/api/v1/analyze", json={"message": ""})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_agent_status(client):
    response = await client.get("/api/v1/status")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_agent_failure_does_not_expose_internal_error(client):
    with patch("src.api.routes.get_agent") as get_agent:
        get_agent.return_value.ainvoke = AsyncMock(side_effect=ValueError("secret provider failure"))
        response = await client.post("/api/v1/chat", json={"message": "Xin chào"})

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "secret" not in response.text


@pytest.mark.asyncio
async def test_swagger_and_openapi(client):
    docs_response = await client.get("/docs")
    openapi_response = await client.get("/openapi.json")

    assert docs_response.status_code == 200
    assert openapi_response.status_code == 200
    paths = openapi_response.json()["paths"]
    assert {"/health", "/ready", "/api/v1/status", "/api/v1/chat", "/api/v1/chat/stream", "/api/v1/analyze"} <= paths.keys()
    assert paths["/api/v1/chat"]["post"]["requestBody"]
    assert paths["/api/v1/chat"]["post"]["responses"]["200"]

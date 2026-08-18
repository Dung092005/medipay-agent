from unittest.mock import AsyncMock, patch

import pytest

from src.models.graph import RetrievalResult


@pytest.mark.asyncio
async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_readiness(client):
    response = await client.get("/ready")
    assert response.status_code == 200
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
    assert response.json()["citations"][0]["chunk_id"] == "chunk-1"


@pytest.mark.asyncio
async def test_chat_accepts_frontend_history(client):
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

    assert response.status_code == 200
    assert response.json()["response"] == "Xin chào"


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
    assert {"/health", "/ready", "/api/v1/status", "/api/v1/chat", "/api/v1/analyze"} <= paths.keys()
    assert paths["/api/v1/chat"]["post"]["requestBody"]
    assert paths["/api/v1/chat"]["post"]["responses"]["200"]

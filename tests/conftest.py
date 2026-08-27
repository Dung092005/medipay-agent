import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
# Keep developer secrets in .env from changing deterministic test behaviour.
# Production authentication/configuration is covered by explicit contract tests.
os.environ["METRICS_TOKEN"] = ""
os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"] = ""
os.environ["CORS_ORIGINS"] = "http://localhost:3000"

from unittest.mock import AsyncMock  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from src.api.auth import get_current_user  # noqa: E402
from src.main import app  # noqa: E402


async def _test_user() -> dict[str, str]:
    return {"uid": "test-user", "email": "test@example.com", "role": "user"}


app.dependency_overrides[get_current_user] = _test_user


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def mock_llm():
    mock = AsyncMock()
    mock.ainvoke.return_value = AsyncMock(content="Mocked LLM response")
    return mock

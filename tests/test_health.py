import httpx
import pytest
import pytest_asyncio

from maintainer_os.main import app


@pytest_asyncio.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["version"] == "0.1.0"


@pytest.mark.asyncio
async def test_capabilities_are_explicit(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/capabilities")
    assert response.status_code == 200
    assert "repositories" in response.json()["planned"]

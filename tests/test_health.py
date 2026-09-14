from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.main import app


def test_health_with_application_lifespan():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_health_async():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

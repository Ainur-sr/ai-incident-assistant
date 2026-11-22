import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

pytestmark = pytest.mark.anyio


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setenv("ANALYSIS_MODE", "mock")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as test_client:
        yield test_client


async def test_health(client):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "payload",
    [
        {"description": "The synthetic request timed out."},
        {"description": "Checkout request failed.", "logs": "ERROR upstream TIMEOUT"},
    ],
)
async def test_timeout_analysis_is_deterministic(client, payload):
    response = await client.post("/analyze", json=payload)

    assert response.status_code == 200
    result = response.json()
    repeated_response = await client.post("/analyze", json=payload)
    assert result == repeated_response.json()
    assert result["severity"] == "unknown"
    assert result["summary"] == "The incident text contains a timeout signal."
    assert result["likely_causes"]
    assert result["recommended_actions"]
    assert result["sources"] == []
    assert result["needs_human_review"] is True
    assert any("user impact" in item for item in result["limitations"])


@pytest.mark.parametrize("logs", [{}, {"logs": None}, {"logs": "   "}])
async def test_insufficient_evidence(client, logs):
    response = await client.post("/analyze", json={"description": "Something failed.", **logs})

    assert response.status_code == 200
    result = response.json()
    assert result["severity"] == "unknown"
    assert result["likely_causes"] == []
    assert result["needs_human_review"] is True
    assert "No application logs were provided." in result["limitations"]


async def test_accepts_field_length_limits(client):
    response = await client.post(
        "/analyze", json={"description": "x" * 2000, "logs": "y" * 20000}
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"description": ""},
        {"description": " \t\n"},
        {"description": 42},
        {"description": "x" * 2001},
        {"description": "Synthetic failure", "logs": "y" * 20001},
        {"description": "Synthetic failure", "logs": 42},
        {"description": "Synthetic failure", "unexpected": True},
    ],
)
async def test_rejects_invalid_input(client, payload):
    response = await client.post("/analyze", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]
    assert all("input" not in issue for issue in response.json()["detail"])

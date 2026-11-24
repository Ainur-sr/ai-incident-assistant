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
    assert result["summary"] == "The incident text mentions a timeout."
    assert result["likely_causes"] == []
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


async def test_misleading_sample_error_labels_do_not_create_a_diagnosis(client):
    response = await client.post("/analyze", json={
        "description": "A docs preview shows CPU high, OOM, network timeout and HTTP 503. "
                       "Monitoring is normal and every request succeeds.",
        "logs": "test fixture displayed sample labels only",
    })
    assert response.status_code == 200
    assert response.json()["likely_causes"] == []
    assert response.json()["needs_human_review"] is True


@pytest.mark.parametrize("streaming", [False, True])
async def test_rejects_oversized_body_before_parsing(client, streaming):
    async def chunks():
        yield b"x" * (144 * 1024)
        yield b"y" * (145 * 1024)

    response = await client.post("/analyze", content=chunks() if streaming else b"x" * (289 * 1024))
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body is too large."}


async def test_saturated_analysis_rejects_work_and_keeps_health_available(client):
    import asyncio

    started = asyncio.Event()
    release = asyncio.Event()
    count = 0

    async def held_body():
        nonlocal count
        count += 1
        if count == 4:
            started.set()
        await release.wait()
        yield b'{"description":"Synthetic"}'

    async with asyncio.TaskGroup() as group:
        inflight = [group.create_task(client.post(
            "/analyze", content=held_body(), headers={"Content-Type": "application/json"}
        )) for _ in range(4)]
        await asyncio.wait_for(started.wait(), timeout=1)
        assert (await client.post("/analyze", json={"description": "Synthetic"})).status_code == 503
        assert (await client.get("/health")).status_code == 200
        release.set()
    assert all(task.result().status_code == 200 for task in inflight)


async def test_validation_does_not_echo_unexpected_field_names(client):
    marker = "synthetic-sensitive-field-name"
    response = await client.post("/analyze", json={"description": "Synthetic", marker: True})
    assert response.status_code == 422
    assert marker not in response.text


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


@pytest.mark.parametrize("enabled,top_k", [("maybe", "3"), ("true", "0"), ("true", "bad")])
async def test_invalid_rag_setting_keeps_health_available(client, monkeypatch, enabled, top_k):
    monkeypatch.setenv("RAG_ENABLED", enabled)
    monkeypatch.setenv("RAG_TOP_K", top_k)

    assert (await client.get("/health")).status_code == 200
    response = await client.post("/analyze", json={"description": "Synthetic failure"})
    assert response.status_code == 503
    assert response.json() == {"detail": "RAG is misconfigured."}


async def test_rag_unavailable_returns_sanitized_error(client, monkeypatch, inline_retrieval):
    from app import retrieval

    def unavailable():
        raise OSError("private path")

    monkeypatch.setenv("RAG_ENABLED", "true")
    monkeypatch.setattr(retrieval, "_load_index", unavailable)
    response = await client.post("/analyze", json={"description": "Synthetic failure"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Runbook retrieval is unavailable."}

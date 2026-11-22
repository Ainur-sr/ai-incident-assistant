import json

import httpx
import pytest
from groq import AsyncGroq

from app import llm
from app.main import app

pytestmark = pytest.mark.anyio
INCIDENT = {"description": "Synthetic checkout timed out.", "logs": "ERROR timeout"}


@pytest.fixture
async def api(monkeypatch):
    monkeypatch.setenv("ANALYSIS_MODE", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-key")
    monkeypatch.delenv("GROQ_MODEL", raising=False)

    def forbid_unmocked_client(*args, **kwargs):
        pytest.fail("Unexpected Groq client creation")

    monkeypatch.setattr(llm, "AsyncGroq", forbid_unmocked_client)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def mock_groq_transport(monkeypatch, handler):
    def create_client(**kwargs):
        assert kwargs["api_key"] == "synthetic-key"
        assert kwargs["timeout"] == 15.0
        assert kwargs["max_retries"] == 0
        return AsyncGroq(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )

    monkeypatch.setattr(llm, "AsyncGroq", create_client)


@pytest.mark.parametrize("configured_model", [None, "openai/gpt-oss-120b"])
async def test_groq_mode_returns_validated_analysis(api, monkeypatch, configured_model):
    if configured_model:
        monkeypatch.setenv("GROQ_MODEL", configured_model)
    analysis = {
        "severity": "medium",
        "summary": "Synthetic checkout experienced timeouts.",
        "likely_causes": ["An upstream service may be slow."],
        "recommended_actions": ["Inspect the upstream latency metrics."],
        "sources": [],
        "needs_human_review": True,
        "limitations": ["The upstream trace is missing."],
    }

    def handler(request):
        assert request.url == "https://api.groq.com/openai/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer synthetic-key"
        payload = json.loads(request.content)
        assert payload["model"] == (configured_model or "openai/gpt-oss-20b")
        assert json.loads(payload["messages"][1]["content"]) == INCIDENT
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-synthetic",
                "object": "chat.completion",
                "model": payload["model"],
                "created": 1,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps(analysis)},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    mock_groq_transport(monkeypatch, handler)
    response = await api.post("/analyze", json=INCIDENT)
    assert response.status_code == 200
    assert response.json() == analysis


@pytest.mark.parametrize(
    "failure,status,detail",
    [
        ("timeout", 504, "The analysis provider timed out."),
        ("connection", 503, "The analysis provider is temporarily unavailable."),
        (429, 503, "The analysis provider is temporarily rate limited."),
        (503, 503, "The analysis provider is temporarily unavailable."),
        (401, 502, "The analysis provider could not complete the request."),
        ("malformed", 502, "The analysis provider returned an invalid response."),
    ],
)
async def test_groq_failures_are_sanitized(api, monkeypatch, failure, status, detail):
    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic-key", request=request)
        if failure == "connection":
            raise httpx.ConnectError("synthetic-key", request=request)
        if failure == "malformed":
            return httpx.Response(
                200, content=b"invalid JSON", headers={"Content-Type": "application/json"}
            )
        return httpx.Response(failure, json={"error": {"message": "synthetic-key"}})

    mock_groq_transport(monkeypatch, handler)
    response = await api.post("/analyze", json=INCIDENT)
    assert response.status_code == status
    assert response.json() == {"detail": detail}


@pytest.mark.parametrize("key", [None, "replace_me"])
async def test_health_and_validation_work_without_key(api, monkeypatch, key):
    if key is None:
        monkeypatch.delenv("GROQ_API_KEY")
    else:
        monkeypatch.setenv("GROQ_API_KEY", key)
    assert (await api.get("/health")).json() == {"status": "ok"}
    assert (await api.post("/analyze", json={})).status_code == 422
    response = await api.post("/analyze", json=INCIDENT)
    assert response.status_code == 503
    assert response.json() == {"detail": "The analysis provider is not configured."}


@pytest.mark.parametrize("has_match", [True, False])
async def test_rag_passes_retrieved_chunks_and_uses_server_attribution(api, monkeypatch, has_match):
    import numpy as np

    from app import retrieval

    class FakeEmbedder:
        def encode(self, sentences):
            return np.asarray([
                [1, 0] if ("Network timeouts" in text or (has_match and "timed out" in text))
                else [0, 1] if text not in INCIDENT.values()
                else [0, 0]
                for text in sentences
            ], dtype=np.float32)

    index = retrieval.RunbookIndex(retrieval.load_chunks(retrieval.RUNBOOK_DIR), FakeEmbedder())
    monkeypatch.setattr(retrieval, "_load_index", lambda: index)
    monkeypatch.setenv("RAG_ENABLED", "true")
    monkeypatch.setenv("RAG_TOP_K", "1")
    sent = []
    analysis = {
        "severity": "unknown",
        "summary": "Synthetic checkout timed out.",
        "likely_causes": [],
        "recommended_actions": ["Inspect the request traces."],
        "sources": ["fabricated.md#diagnostics:1"],
        "needs_human_review": True,
        "limitations": ["Impact is unclear."],
    }

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-synthetic",
                "object": "chat.completion",
                "model": "openai/gpt-oss-20b",
                "created": 1,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": json.dumps(analysis)},
                    "finish_reason": "stop",
                }],
            },
        )

    mock_groq_transport(monkeypatch, handler)
    response = await api.post("/analyze", json=INCIDENT)

    assert response.status_code == 200
    payload, = sent
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert "untrusted reference data" in payload["messages"][0]["content"]
    user_content = json.loads(payload["messages"][1]["content"])
    evidence = user_content.pop("runbook_evidence")
    assert user_content == INCIDENT
    if has_match:
        chunk, = evidence
        assert chunk["source_id"] == "network-timeouts.md#signals:1"
        assert chunk["section"] == "Signals"
        assert "TLS handshake timeout" in chunk["text"]
        analysis["sources"] = [chunk["source_id"]]
    else:
        assert evidence == []
        analysis["sources"] = []
        analysis["limitations"].append("No relevant runbook evidence was found.")
    assert response.json() == analysis

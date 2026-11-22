import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def disable_rag_by_default(monkeypatch):
    monkeypatch.setenv("RAG_ENABLED", "false")

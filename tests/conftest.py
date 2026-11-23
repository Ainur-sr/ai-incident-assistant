import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def disable_rag_by_default(monkeypatch):
    monkeypatch.setenv("RAG_ENABLED", "false")


@pytest.fixture
def inline_retrieval(monkeypatch):
    from app import main

    async def run_inline(function, *args):
        # These route tests inject synchronous retrieval doubles. The event
        # loop's cross-thread wakeup is outside the route contract they test.
        return function(*args)

    monkeypatch.setattr(main, "run_in_threadpool", run_inline)


@pytest.fixture
def groq_client_factory():
    import httpx
    from groq import AsyncGroq

    def factory(handler, **kwargs):
        client = AsyncGroq(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False),
        )
        # Keep the SDK's OS-header discovery out of the mock HTTP path.
        client._platform = "Linux"
        return client

    return factory

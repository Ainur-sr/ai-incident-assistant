import os

DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"


def analysis_mode() -> str:
    return os.getenv("ANALYSIS_MODE", "mock").strip().lower()


def groq_api_key() -> str | None:
    key = os.getenv("GROQ_API_KEY", "").strip()
    return key if key and key != "replace_me" else None


def groq_model() -> str:
    return os.getenv("GROQ_MODEL", "").strip() or DEFAULT_GROQ_MODEL


def rag_top_k() -> int | None:
    enabled = os.getenv("RAG_ENABLED", "false").strip().lower()
    if enabled == "false":
        return None
    if enabled != "true":
        raise ValueError("RAG_ENABLED must be true or false.")
    top_k = int(os.getenv("RAG_TOP_K", "3"))
    if top_k < 1:
        raise ValueError("RAG_TOP_K must be positive.")
    return top_k

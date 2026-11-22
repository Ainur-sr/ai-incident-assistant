import os

DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"


def analysis_mode() -> str:
    return os.getenv("ANALYSIS_MODE", "mock").strip().lower()


def groq_api_key() -> str | None:
    key = os.getenv("GROQ_API_KEY", "").strip()
    return key if key and key != "replace_me" else None


def groq_model() -> str:
    return os.getenv("GROQ_MODEL", "").strip() or DEFAULT_GROQ_MODEL

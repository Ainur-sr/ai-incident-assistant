from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import config
from app.analysis import analyze_incident
from app.llm import AnalysisError, Failure, analyze_with_generation_retry, create_groq_client
from app.models import IncidentAnalysis, IncidentRequest
from app.request_limits import AnalysisRequestLimits
from app.retrieval import retrieve


@asynccontextmanager
async def lifespan(application: FastAPI):
    key = config.groq_api_key() if config.analysis_mode() == "groq" else None
    if key is None:
        yield
        return
    async with create_groq_client(key) as client:
        application.state.groq_client = client
        try:
            yield
        finally:
            del application.state.groq_client


app = FastAPI(title="AI Incident Triage Assistant", lifespan=lifespan)
app.add_middleware(AnalysisRequestLimits)

PROVIDER_ERROR_STATUS = {
    Failure.TIMEOUT: 504,
    Failure.RATE_LIMIT: 503,
    Failure.UNAVAILABLE: 503,
    Failure.INVALID_RESPONSE: 502,
    Failure.REQUEST_FAILED: 502,
}


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    _request: Request, error: RequestValidationError
) -> JSONResponse:
    details = [
        {"loc": ("body",) if issue["type"] == "extra_forbidden" else issue["loc"],
         "msg": issue["msg"], "type": issue["type"]}
        for issue in error.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": details})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze")
async def analyze(incident: IncidentRequest, request: Request) -> IncidentAnalysis:
    mode = config.analysis_mode()
    if mode not in {"mock", "groq"}:
        raise HTTPException(503, "Analysis mode is misconfigured.")
    api_key = config.groq_api_key() if mode == "groq" else None
    if mode == "groq" and not api_key:
        raise HTTPException(503, "The analysis provider is not configured.")

    try:
        top_k = config.rag_top_k()
    except ValueError:
        raise HTTPException(503, "RAG is misconfigured.") from None

    evidence = None
    if top_k is not None:
        try:
            evidence = await run_in_threadpool(retrieve, incident.description, incident.logs, top_k)
        except (ImportError, OSError, RuntimeError, ValueError):
            raise HTTPException(503, "Runbook retrieval is unavailable.") from None

    try:
        if api_key is None:
            result = analyze_incident(incident)
            result.retrieved_sources = [chunk.source_id for chunk in evidence or []]
        else:
            client = getattr(request.app.state, "groq_client", None)
            if client is None:
                async with create_groq_client(api_key) as client:
                    result = await analyze_with_generation_retry(incident, client, config.groq_model(), evidence)
            else:
                result = await analyze_with_generation_retry(incident, client, config.groq_model(), evidence)
    except AnalysisError as error:
        raise HTTPException(
            PROVIDER_ERROR_STATUS[error.failure], error.failure.value
        ) from None

    if evidence == []:
        result.limitations.append("No relevant runbook evidence was found.")
    return result

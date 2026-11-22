from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app import config
from app.analysis import analyze_incident
from app.llm import AnalysisError, Failure, analyze_with_groq, create_groq_client
from app.models import IncidentAnalysis, IncidentRequest

app = FastAPI(title="AI Incident Triage Assistant")

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
        {"loc": issue["loc"], "msg": issue["msg"], "type": issue["type"]}
        for issue in error.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": details})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze")
async def analyze(incident: IncidentRequest) -> IncidentAnalysis:
    mode = config.analysis_mode()
    if mode == "mock":
        return analyze_incident(incident)
    if mode != "groq":
        raise HTTPException(503, "Analysis mode is misconfigured.")

    api_key = config.groq_api_key()
    if not api_key:
        raise HTTPException(503, "The analysis provider is not configured.")

    try:
        async with create_groq_client(api_key) as client:
            return await analyze_with_groq(incident, client, config.groq_model())
    except AnalysisError as error:
        raise HTTPException(
            PROVIDER_ERROR_STATUS[error.failure], error.failure.value
        ) from None

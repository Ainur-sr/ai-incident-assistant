from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.analysis import analyze_incident
from app.models import IncidentAnalysis, IncidentRequest

app = FastAPI(title="AI Incident Triage Assistant")


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
    return analyze_incident(incident)

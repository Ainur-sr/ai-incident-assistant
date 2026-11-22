# AI Incident Triage Assistant

A local FastAPI service for triaging synthetic software incidents. Milestone 1 uses deterministic rules and runs without credentials or external services.

## Setup and run

Use Python 3.12 or newer. If the repository already has a compatible `.venv`, reuse it and skip the first command.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m uvicorn app.main:app --reload
```

FastAPI serves interactive API docs at http://127.0.0.1:8000/docs.

```bash
curl http://127.0.0.1:8000/health

curl -X POST http://127.0.0.1:8000/analyze \
  -H 'Content-Type: application/json' \
  -d '{"description":"Synthetic checkout request timed out.","logs":"ERROR upstream timeout"}'
```

`GET /health` returns `{"status":"ok"}`. `POST /analyze` accepts a nonblank description up to 2,000 characters and optional logs up to 20,000 characters. Invalid requests return HTTP 422. Use only synthetic incident data.

## Architecture

`app/main.py` defines the HTTP routes. `app/models.py` defines the Pydantic request and response schemas. `app/analysis.py` applies a local rule to the incident text and returns a structured result. Timeout-related wording produces a hypothesis and diagnostic steps. Other inputs produce generic investigation steps. Both paths return an unknown severity, request human review, and return an empty sources list.

FastAPI exposes the HTTP API, Pydantic validates the schemas, and Uvicorn runs the local server. Pytest runs the offline tests, AnyIO provides its async test integration, and HTTPX sends requests directly to the ASGI app.

## Tests

```bash
python -m pytest
```

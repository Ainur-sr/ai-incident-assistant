# AI Incident Triage Assistant

A local FastAPI service for triaging synthetic software incidents. It runs offline by default and can use GroqCloud for analysis when explicitly enabled.

## Run with Docker

With Docker and the Compose plugin installed, build and start the Python 3.14
service in its default mock mode with RAG disabled:

```bash
docker compose --env-file /dev/null up --build --wait
curl --fail http://127.0.0.1:8000/health
```

The API listens on local port 8000 and Compose waits for the container's
`GET /health` check. The commands use `--env-file /dev/null` on Linux or WSL
to skip automatic loading of the project's `.env` file. Compose still uses
exported shell variables. After trying the API examples below, stop the service
with `docker compose --env-file /dev/null down`.

To use GroqCloud in Docker, enter and export `GROQ_API_KEY` as shown in
[Enable GroqCloud](#enable-groqcloud), then run
`ANALYSIS_MODE=groq docker compose --env-file /dev/null up --build --wait`.
Compose passes the key at runtime and accepts the optional `GROQ_MODEL` override.

To use local RAG in Docker, build with the optional CPU dependencies, cache the
public model in the Compose volume, and enable retrieval:

```bash
INSTALL_RAG=true docker compose --env-file /dev/null build
INSTALL_RAG=true docker compose --env-file /dev/null run --rm api python -c 'from sentence_transformers import SentenceTransformer; SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", device="cpu", token=False)'
INSTALL_RAG=true RAG_ENABLED=true docker compose --env-file /dev/null up --wait
```

The one-time model download needs internet access. The cache volume survives
`docker compose down`. Export `RAG_TOP_K` to override the default of three
chunks. For GroqCloud with RAG, also set `ANALYSIS_MODE=groq` and export the key.

## Setup and run

Use Python 3.14 for the documented setup. If the repository already has a compatible `.venv`, reuse it and skip the first command.

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

## Enable GroqCloud

In the shell that runs Uvicorn, enter the API key at the hidden prompt and export it:

```bash
read -rsp 'Groq API key: ' GROQ_API_KEY
printf '\n'
export GROQ_API_KEY
export ANALYSIS_MODE=groq
export GROQ_MODEL=openai/gpt-oss-20b
python -m uvicorn app.main:app --reload
```

`GROQ_MODEL` is optional and defaults to `openai/gpt-oss-20b`. Any override must
support strict Structured Outputs. `.env.example` lists the available settings
with placeholder values. The application reads process environment variables.
Use `ANALYSIS_MODE=mock` for deterministic offline analysis.

In Groq mode, `POST /analyze` sends the description and logs to GroqCloud. Use
only synthetic data. Responses include severity, hypotheses, safe diagnostics,
evidence limitations, and an indicator for human review. With RAG disabled, `sources` is empty.

| HTTP status | Meaning |
| --- | --- |
| 422 | Invalid incident input |
| 502 | Invalid upstream response or upstream request rejection |
| 503 | Invalid or missing service configuration, upstream rate limit, provider unavailability, or local retrieval failure |
| 504 | Upstream timeout |

The client uses a 15-second SDK timeout and disables automatic retries.

## Enable local runbook retrieval

With the Python 3.14 virtual environment active, install a CPU build of PyTorch
and the RAG dependencies. Pre-download the public embedding model once:

```bash
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements-rag.txt
python -c 'from sentence_transformers import SentenceTransformer; SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", device="cpu", token=False)'
```

Then start the API with retrieval enabled:

```bash
export RAG_ENABLED=true
export RAG_TOP_K=3
python -m uvicorn app.main:app --reload
```

Set `ANALYSIS_MODE=groq` and export the key as shown above to include the
retrieved excerpts in the Groq request. Mock mode can also return local sources
while keeping its deterministic analysis. The application only loads cached
model files at runtime. Missing dependencies or weights produce HTTP 503 on
analysis requests. `GET /health` stays independent of retrieval.

`app/retrieval.py` reads the eight synthetic Markdown files in `runbooks/`,
splits them on headings and caps long sections at 120 words per chunk. The first
RAG request embeds the corpus once per process. Restart the process to pick up
runbook edits. Each search embeds the description and, if present, up to 600
characters from each end of the logs. NumPy ranks chunks by their highest cosine
similarity across those query vectors. The cutoff of 0.30 is a starting
heuristic. Up to `RAG_TOP_K` chunks are returned in descending relevance.

The API attaches source IDs from the selected chunks, such as
`network-timeouts.md#diagnostics:1`. IDs use the filename, section slug, and
one-based part number. They identify the excerpts supplied as context. When
nothing meets the cutoff, analysis continues with empty sources and a limitation
indicating that no relevant runbook was found. Logs and excerpts are placed in
the user message as reference data. Use only synthetic incident data and
runbooks, since Groq mode sends them to the provider.

## Architecture

`app/main.py` defines the HTTP routes and maps analysis errors to safe HTTP
responses. `app/config.py` reads the process environment and `app/models.py`
defines the request and response schemas. `app/analysis.py` provides deterministic
mock analysis. `app/llm.py` uses the official async Groq SDK to call Chat
Completions. It generates the strict JSON Schema from the Pydantic response
model and independently validates the returned JSON before releasing the result.

FastAPI exposes the HTTP API and Uvicorn runs the local server. Pytest and AnyIO
run the offline tests. HTTPX sends requests directly to the ASGI app and mocks
Groq's HTTP transport in integration tests. API tests using retrieval doubles run those
doubles inline at the thread-pool boundary. Mock Groq clients also use a fixed
OS header to skip background platform detection. This keeps the ASGI tests
independent of cross-thread socket wakeups in restricted environments.
Retrieval tests use deterministic
NumPy embeddings, so the suite requires neither model weights nor network access.

## Tests

```bash
python -m pytest
```

GitHub Actions runs CI on pushes and pull requests. It installs the development
dependencies on Python 3.14, runs the offline pytest suite, validates Compose,
and builds and starts the default image to check `GET /health`. CI uses mock
mode with RAG disabled and requires internet access only for dependencies,
actions, and the base image. To reproduce the Docker checks locally, run
`docker compose --env-file /dev/null config --quiet` and the default Docker
commands above.

For an optional manual integration check, enable Groq mode and run the
synthetic `/analyze` curl example above. Expect HTTP 200 with the structured
analysis. Check that the hypotheses and limitations reflect the synthetic input
and that any `sources` resolve to the local runbooks. With RAG disabled, expect
an empty source list. This check makes a real GroqCloud request and may consume
provider quota or incur charges.

## Evaluation

The versioned benchmark contains 30 synthetic incidents covering the eight
runbooks, ambiguity, out-of-domain incidents and misleading inputs. See the
[labeling guide and metric definitions](evals/README.md).

```bash
# Real local embeddings. Pre-cache the model as shown above.
HF_HUB_OFFLINE=1 python -m evals retrieval

# Deterministic analysis baseline using the application's local rules.
python -m evals mock
```

Retrieval reports document-level Recall@1, Recall@3, Precision@3, MRR and
negative-case false positives. Analysis reports severity accuracy and macro-F1,
leading-cause category accuracy on successful responses, overall success rate,
schema validity, latency, errors, uncertainty
and citation validity. The mock run checks the pipeline and measures the local
baseline. It makes no claim about model quality. Both modes run without Groq.

### Saved 30-case results

Dataset 1.0.0, `openai/gpt-oss-20b`, 2026-11-23 UTC, provider sampling
defaults, three RAG chunks, six seconds between requests, and one allowed retry.

| Metric | Without RAG | With RAG |
| --- | ---: | ---: |
| API analysis success, all 30 scheduled cases per arm | 26/30 (86.67%) | 26/30 (86.67%) |
| Severity accuracy, 22 shared successes | 14/22 (63.64%) | 15/22 (68.18%) |
| Leading-cause category accuracy, 22 shared successes | 18/22 (81.82%) | 20/22 (90.91%) |
| Severity accuracy, each arm's 26 successes | 17/26 (65.38%) | 16/26 (61.54%) |
| Leading-cause category accuracy, each arm's 26 successes | 21/26 (80.77%) | 24/26 (92.31%) |

The separate offline retrieval report measured document Recall@1 of 91.67%,
Recall@3 of 100%, Precision@3 of 36.11%, and MRR of 0.9722 on 24 cases with
labeled runbooks. One of six negatives returned a document. These scores
measure ranking and use no model-generated analyses.

To recompute the tables from the saved run, use the offline `rescore` command
below with `live.json` and `retrieval.json`. The original reports are local
artifacts under `evals/results/`. This single run uses a small, unbalanced
synthetic dataset. Provider sampling and failures can change scores on rerun.
The paired comparison excludes cases that failed in either arm, and the
lexical cause score measures agreement with category labels. See the
[full audit and uncertainty results](evals/README.md#full-30-case-run-audit).

When you explicitly choose to run the live comparison, export the Groq key as
shown above and use:

```bash
python -m evals live --allow-live --request-interval 3 --retries 2
```

Live mode compares paired runs on the same cases and model settings. Pass
`--case-ids ID [ID ...]` to select distinct dataset cases in the given order.
A live comparison makes two initial requests per selected case, plus any retries,
and may incur charges. A full run schedules 60 initial requests. Requests are
sequential, start at least three seconds apart by default, and pause for at
least 60 seconds on rate limits or longer if a numeric `Retry-After` header
requests it. Increase the interval to fit your quota. Timeouts and temporary
unavailability are retried with bounded exponential backoff.

Rescore a saved comparison offline with
`python -m evals rescore --saved-report evals/results/live.json`.
The paired comparison uses incident IDs that succeeded in both arms.

Each command writes JSON and Markdown under the Git-ignored `evals/results/`
directory, replacing only the previous reports for the selected mode. Reports
include run settings and sample counts. Pytest exercises the evaluator with
doubles and does not invoke GroqCloud or download model weights.

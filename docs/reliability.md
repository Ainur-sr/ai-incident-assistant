# Reliability and local deployment

## Groq findings

The historical run recorded four terminal HTTP 400 errors in each arm with
`type=invalid_request_error` and `code=json_validate_failed`. The same schema
and model delivered 52 valid responses. This pattern is consistent with
intermittent provider generation or validation failures. The archived error
metadata cannot establish the provider's root cause, deployment fingerprint,
or whether reasoning output or truncation contributed.

The [Groq Structured Outputs guide](https://console.groq.com/docs/structured-outputs)
lists GPT-OSS 20B for strict constrained decoding. Both the original schema
and the current provider schema mark every property required and set
`additionalProperties: false` on each object, including the nested hypothesis
definition. They use strings, enums, booleans, arrays, objects, and local
schema references. The provider schema is derived from the Pydantic model and
validated locally with strict types. A successful live request with the new
schema remains to be verified.

Groq distinguishes best-effort JSON failures from its strict-mode guarantee
and asks for reproductions if strict calls return 400. The application labels
the exact known generation code separately from explicit configuration codes
such as `invalid_json_schema`. The HTTP adapter makes at most one extra call
for the observed code. The evaluator also caps such retries at one per case
and arm, inside its configured retry budget. It records each attempt and
keeps its pacing rules. Generic HTTP 400, configuration errors, non-stop
completions, and local schema validation errors are terminal. Provider bodies,
including failed generation text, are never returned or persisted by default.

The [API reference](https://console.groq.com/docs/api-reference) documents
generation controls and GPT-OSS reasoning effort. Model-specific argument
values can themselves cause HTTP 400. Token ceilings can shorten completions,
and sampling controls change the distribution of output. The current request
keeps provider defaults and uses no tools, streaming, or caller stop
sequences. Any experiment on temperature, reasoning effort, or completion
budget should change one variable at a time using the same cases and save
each run separately.

## Security review

| Surface | Current behavior and limit |
| --- | --- |
| Prompt injection | Data is JSON-encoded in the user message. System instructions treat logs and runbooks as untrusted. No tools or remediation actions are wired to the model. Advisory text still needs human judgment. |
| Sensitive data | Validation omits raw inputs and unexpected field names. Provider errors are mapped to fixed HTTP messages. Groq mode sends the incident and retrieved excerpts upstream. Use synthetic inputs. There is no universal secret detector or output scrubber. |
| Request size | The ASGI layer caps `POST /analyze` at 288 KiB, including streamed bodies (413), and allows 10 seconds to receive them (408). Pydantic applies character limits afterwards. |
| Exhaustion and quota | At most four analyses per worker are admitted, including body reads. Excess requests return 503. Retrieval serializes access to the shared CPU encoder. Sequential traffic can still exhaust provider quota. |
| Provider lifecycle | The lifespan owns and closes one pooled async client per Groq-enabled worker. The SDK uses a 15-second timeout and disables automatic retries. Provider timeouts map to 504, throttling and unavailability to 503, and other provider failures to 502. |
| Dependencies | The pinned FastAPI version 0.165.0 exceeds the 0.163.2 fix for [GHSA-f56c-v883-vx8r](https://github.com/fastapi/fastapi/security/advisories/GHSA-f56c-v883-vx8r). Version pins reduce drift. They are not a vulnerability scan. |
| Docker and Actions | The image runs as a non-root user. The build context is an allowlist and the port binds to loopback. Secrets enter Groq mode through the runtime environment. Docker administrators can inspect container environment values. CI uses read-only repo permissions and no provider secrets. |

Before public exposure, add authentication, per-identity rate and quota
limits, an ingress with request/header/connection limits, a data retention and
redaction policy, and operational logging that excludes request bodies.
Keep dependency and base-image advisories under review with a scanner such as
[pip-audit](https://pypi.org/project/pip-audit/). This milestone did not
run a complete advisory or container image scan.

## Reproducibility

The two constraint files pin the active dependency closure from the tested
CPython 3.14 Linux x86_64 environment. Base requirements stay readable and
express direct dependency ranges. To refresh the pins, resolve the direct
requirements in a fresh environment for the target platform, capture exact
transitive versions with `pip freeze`, separate optional CPU RAG packages,
and run `pip check`, pytest, and both Docker smoke checks. For stronger supply
chain reproducibility, collect and verify wheel hashes and pin the base image
digest and embedding snapshot revision.

Milestone 6 was checked with the local offline pytest suite, local mock and
cached retrieval evaluations, and offline rescoring of both published reports.
The HTTP smoke script passed against native Uvicorn in mock mode with RAG off
and on. The RAG process emitted a `loky` semaphore cleanup warning at shutdown.
Docker build and container smoke verification for this revision are in the CI
workflows. Docker was unavailable in the implementation environment. A new live
GroqCloud evaluation was not executed.

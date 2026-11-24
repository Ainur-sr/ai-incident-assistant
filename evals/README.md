# Incident evaluation

## Dataset and labeling

`incidents-v1.json` is version 1.0.0 and contains 30 synthetic cases. Three cases
cover each of the eight runbook topics. Two cases in that group have multiple
relevant documents. The six negative cases include ambiguous, out-of-domain
and misleading inputs. There are no real logs, customer records or credentials.

- **Severity** reflects reported impact. `low` means limited or internal impact,
  `medium` means partial functional degradation, `high` means major degradation,
  and `critical` means widespread unavailability of the affected workflow or
  service. `unknown` means impact cannot be inferred from the incident or the
  request is unrelated to incident triage.
- **Cause** labels the leading supported explanation. Categories correspond to
  the eight runbook topics plus expired certificates, missing permissions and
  localization defects. The HTTP 503 category represents gateway or upstream
  availability problems. In an incident that emits 503s after a release,
  evidence may support a deployment regression as the primary cause. Use
  `unknown` when there is no supported cause. A claim in a fixture or a negated
  symptom does not establish a cause.
- **Relevant documents** are the runbook filenames whose sections provide
  actionable diagnostic guidance for the evidence. Label all such documents
  even if only one gives the primary cause. An empty list means no document
  applies. A real issue can be out of scope for this corpus.
- **Tags** identify coverage and edge cases. Expected labels were assigned to
  the synthetic input before benchmark execution. These examples are a
  development benchmark for this corpus. They are small and intentionally
  unbalanced.

## Retrieval

The runner uses the application encoder, similarity threshold and ranking,
requesting up to the number of indexed chunks. It collapses repeated document
IDs at their first occurrence before taking the top one or three documents.
This gives each document the rank of its highest-scoring chunk. The live
comparison still uses the application's default top three **chunks**.

Recall@k divides the number of relevant documents in the top k by the number
of labeled relevant documents. Precision@3 divides the number of relevant
results by three, so empty slots earn no credit. MRR averages the reciprocal
rank of the first relevant document across the full above-threshold ranking.
Misses contribute zero. These four metrics are macro-averaged over cases with
at least one relevant document. For negatives, the false-positive rate@3 is
the fraction with at least one returned document. Reports include both counts
and the individual document rankings.

## Analysis and comparison

- Severity accuracy uses exact labels. Macro-F1 gives all five labels equal
  weight. A label with no reference or predicted instances gets F1 of zero.
- Cause accuracy compares the **first** hypothesis to the case label.
  `metrics.py` defines case-insensitive regular expressions for common terms
  and paraphrases. A single category match counts as that prediction.
  Multiple matches and unmatched text are `unclassified`. An empty list or an
  explicit abstention phrase is `unknown`. This lexical heuristic can miss
  paraphrases, match a negated claim, or penalize a correct lower-ranked cause.
- Success rate divides cases yielding a schema-valid analysis by all scheduled
  cases. Accuracy and macro-F1 use only those successful cases. Wrong
  predictions and terminal failures have separate counts. Attempt errors
  include errors from calls that later recovered on retry.
- Schema validity divides passes by cases with an observed validation outcome.
  A strict incident payload rejection counts as `schema_validation`, whether
  detected by the provider adapter or by the evaluator. Missing, truncated,
  or malformed completions use `invalid_response` and remain unassessed, as
  do transport failures. Legacy failed rows are unassessed because their
  schema flag represented whether analysis succeeded.
- The uncertainty check applies to successful responses when expected
  severity or cause is `unknown`. It requires both a human-review flag and a
  nonempty limitations list. The count of all cases that expected uncertainty
  is reported separately. Metrics with an empty denominator are JSON `null`.
- Citation validity is the fraction of checked, nonempty source lists for
  which every chunk ID was supplied to the analyzer. Source coverage is the
  fraction of successful analyses with a nonempty source list, even if a
  citation is invalid. Relevant source recall averages, across successful
  cases with labeled runbooks, the fraction of distinct expected documents
  represented by verified chunks. Empty lists score zero on positive cases.
  For legacy rows, recall uses the saved citation verdict. A legacy nonempty
  list without a passing verdict is omitted because individual chunk membership
  is unknown. Historical runs attached retrieved IDs automatically. Current
  runs use the union of valid IDs explicitly cited per hypothesis.
- Latency uses a monotonic wall clock and covers retrieval, analysis requests,
  pacing, retries and retry delays. It includes failures. Live mode builds the
  index before timing begins. Current live reports separately record time
  awaiting the provider SDK across attempts, pacing (including the rate-limit
  cooldown), retry backoff, and total analysis time excluding pacing. SDK time
  includes network and SDK processing. Historical rows only have wall time.
- Live mode pairs the same selected cases with and without RAG using one model and
  the same provider sampling settings and strict schema. It alternates arm
  order and reports RAG minus baseline deltas on cases that succeeded in both
  arms, matched by incident ID. Provider sampling, deployment,
  network conditions and rate limits can affect results across runs. A single
  run does not establish a reliable improvement.

### Failure classification

New analysis rows record terminal categories in `error`. The
`attempt_error_details` list records each failed attempt, including errors
that recovered on retry. It contains the one-based attempt number, category,
and upstream HTTP status when available. Categories include `http_429`,
`http_4xx` (excluding 429), `http_5xx`, `timeout`, `connection`,
`schema_validation`, `invalid_response`, `provider_generation`,
`request_configuration`, and `unknown`. A retrieval failure
before a provider call is `retrieval_unavailable` and has zero attempts.
The evaluator records only controlled categories and numeric metadata.
Exception messages, headers, bodies, and validation inputs are excluded.
Historic error codes survive rescoring as recorded.

### Offline report rescoring

```bash
python -m evals rescore --saved-report evals/benchmarks/v1/live.json
```

The command writes `live-rescored.json` and `live-rescored.md` under
`evals/results/` by default. Pass `--output-dir` to choose another destination.
It also supports saved `mock` and
`retrieval` reports. It uses saved case outcomes without provider calls or
retrieval. For older analysis reports, it restores expected document labels
only if the local dataset matches the saved SHA-256. It preserves run
settings and timestamps and adds a separate rescoring timestamp.

### Five-case pilot

On dataset 1.0.0 with `openai/gpt-oss-20b`, zero retries, and up to three RAG
chunks, baseline success was 4/5 and RAG success was 3/5. Among successes,
severity and leading-cause category accuracy were 2/4 and 2/4 without RAG
and 0/3 and 3/3 with RAG. The common cases were INC-001, INC-004 and INC-014.
On those cases, severity changed from 1/3 to 0/3 and leading-cause accuracy
changed from 1/3 to 3/3. INC-004 lost its severity match and gained a cause
match. INC-014 gained a cause match. INC-001 kept its cause match and missed
severity in both arms.

The saved error was `request_failed` for INC-025 in both arms and INC-023
with RAG. The adapter maps non-5xx HTTP status errors to this category after
handling rate limits, and also uses it for other uncategorized SDK API
errors. The report does not preserve HTTP status, provider error codes or
bodies, so finer attribution is unavailable. The only expected-uncertainty
case failed in both arms. All seven delivered analyses passed schema
validation. Baseline source coverage was 0/4, with no lists to score for
citation validity. RAG source coverage and recorded citation validity were
both 3/3, and every successful RAG case covered all its expected runbooks.
The original report stores the citation verdict but lacks the full retrieved
chunk inputs and raw model output, so independent revalidation is unavailable.

### Pilot label audit

| Case | Severity | Evidence and leading-cause category |
| --- | --- | --- |
| INC-001 | high | Most checkout attempts fail while direct database health queries still work. Pool utilization, zero idle clients, and queued borrowers support `db_pool`. |
| INC-004 | critical | Every request at the public entry point returns unavailable. Zero eligible backends support `http_503` (gateway or upstream availability). |
| INC-014 | low | A brief internal audit delay affects one Kafka partition. The isolated backlog supports `kafka_lag`. |

The dataset labels and version are unchanged. Regression cases cover narrow
paraphrases for checked-out clients, zero eligible backends, and a partition
backlog. The saved pilot contains category labels only, so rescoring keeps the
historical classifications. The unclassified baseline predictions for INC-004
and INC-014 cannot be traced to a specific phrase.

### Manual quality rubric

Review generated hypotheses and diagnostic actions separately when doing a
qualitative assessment. For each criterion record `pass`, `partial`, or
`fail`, and include a short rationale tied to the incident evidence:

1. The summary and leading hypothesis distinguish observations from conjecture.
2. Diagnostics are safe, prioritized, and help discriminate among hypotheses.
3. Limitations communicate meaningful gaps in impact or cause evidence.
4. Runbook excerpts support the specific claims for which they are used.

The automatic reports contain category labels and source IDs. They do not
store raw generated text or assign subjective scores.

### Saved follow-up run

The follow-up report records HTTP 400 for INC-025 in both arms, with one
attempt per arm and no schema verdict. The synthetic input has no logs. The
serialized input uses `logs: null`, and the RAG arm adds an empty
`runbook_evidence` list. Mocked transport tests verify this request shape,
strict response schema settings, and 400 handling. The report contains no
provider error reason, so it cannot explain the rejection.

INC-014 was classified as `kafka_lag` without RAG and `unclassified` with
RAG in the follow-up run. The original pilot above is a separate run.
The follow-up report lacks the leading hypothesis, so the mismatch cannot
be attributed to the model or the classifier. New reports include
`cause_category_matches` to distinguish zero regex hits from multiple hits
without keeping hypothesis text. A failed analysis has a null match list.
Rescoring preserves historical predictions and does not infer missing hits.

For HTTP 400, future attempts also include a `provider_error` object when
known structured values are present. It may contain `type`, `code` and
`param`, for example `invalid_request_error`, `json_validate_failed` and
`response_format`. Fields outside the per-field allowlists are omitted.
Provider messages, failed generations, request and response bodies, and
headers are excluded. The original status and error category still drive
the existing metrics. Historical reports cannot supply missing metadata.

### Full 30-case run audit

The saved run completed on 2026-11-23 UTC with dataset 1.0.0,
`openai/gpt-oss-20b`, provider sampling defaults, three RAG chunks, a
six-second request interval, and one allowed retry. The eight unpaired
cases each succeeded in the opposite arm.

| Failed arm | Incident IDs | Stored category | HTTP status |
| --- | --- | --- | --- |
| Without RAG | INC-002, INC-004, INC-019, INC-027 | `http_4xx` | 400 |
| With RAG | INC-007, INC-013, INC-024, INC-025 | `http_4xx` | 400 |

All eight failures occurred on attempt one and recorded
`type=invalid_request_error` and `code=json_validate_failed`. None recorded
a parameter. The provider reported a JSON validation problem across both
arms and multiple incident types. The saved metadata cannot identify the
violated constraint or determine what caused it. The three HTTP 429 attempts
(INC-006 and INC-023 without RAG, INC-014 with RAG) recovered on retry.

The existing uncertainty rate checks human-review and limitations fields
among successful cases with at least one expected `unknown` label. It was
2/3 without RAG and 0/2 with RAG. The new `unknown_label_match_rate` measures
whether all fields expected to be `unknown` were predicted as `unknown`,
using the same denominator. It was 1/3 and 0/2 respectively on rescore.

- INC-025 lacks impact and diagnostic evidence. Without RAG it returned both
  unknown labels and expressed uncertainty. With RAG the HTTP 400 left
  uncertainty unassessed.
- INC-026 is an out-of-domain support request. Both arms returned unknown
  cause but assigned low severity and missed the uncertainty field check.
- INC-030 contains literal alert phrases in a documentation preview, with
  monitoring reporting normal operation. Both arms returned low severity
  and an unclassified cause. The baseline passed the uncertainty field check.
  An unclassified cause can reflect either zero or multiple lexical hits.

Cause scores measure a lexical category match on the first hypothesis. They
cannot establish that the hypothesis is supported, account for negation
reliably, or give credit for a later correct hypothesis. Runbook IDs indicate
supplied evidence, and manual review is needed to determine which claims it
actually supports. The paired set excludes eight cases with failures, so
paired and all-successful scores describe different populations.

## Reproduce or extend the benchmark

The [published benchmark](benchmarks/v1/README.md) contains the original
30-case live and retrieval reports. Use a fresh directory for each experiment.
Live and retrieval modes refuse to overwrite an existing report.

Install the optional dependencies and cache the public CPU embedding model once:

```bash
python -m pip install --no-deps -c requirements-rag-lock.txt torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -c requirements-lock.txt -c requirements-rag-lock.txt -r requirements-rag.txt
python -c 'from sentence_transformers import SentenceTransformer; SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", device="cpu", token=False)'
```

```bash
HF_HUB_OFFLINE=1 python -m evals retrieval --output-dir evals/results/m6-retrieval
python -m evals mock --output-dir evals/results/m6-mock

# Explicit live opt-in with synthetic inputs only. This uses provider quota.
read -rsp 'Groq API key: ' GROQ_API_KEY
printf '\n'
export GROQ_API_KEY
GROQ_MODEL=openai/gpt-oss-20b HF_HUB_OFFLINE=1 python -m evals live \
  --allow-live --request-interval 6 --retries 1 --capture-claims \
  --output-dir evals/results/m6-full-ab
unset GROQ_API_KEY
```

First compare the terminal generation error rate, retry attempts and recoveries,
schema outcomes, and valid responses over all 30 scheduled cases per arm.
Then compare severity and leading-cause scores on the paired intersection,
on each arm's successes, and over all scheduled cases. Include the uncertainty
denominator and distinguish the review-plus-limitations check from the actual
unknown label match. The new prompt is an intervention alongside the schema and
retry changes, so a single rerun cannot isolate their effects.

### Claim-level groundedness protocol

Use the versioned inputs, hashed runbooks, and the optional local
`--capture-claims` report. This flag records free-form generated hypothesis text
for synthetic cases. Review that text for sensitive content before sharing any
derived artifact. Historical reports lack the hypothesis text and cannot be
scored with this protocol.

1. Review every successful RAG-arm hypothesis, including uncited hypotheses,
   for the fixed set INC-001, INC-023, and INC-025 through INC-030. Record missing
   responses separately. Use the saved incident ID, hypothesis ordinal, and
   exact cited chunk IDs so another reviewer can reproduce the comparison.
2. For each hypothesis and chunk pair, check whether the ID was in the actual
   `retrieved_source_ids` for that case. Then label the pair `supports`,
   `related_only`, or `contradicts` from the chunk text. `supports` requires
   that the chunk explicitly connects an observed symptom or fact to the
   proposed explanation. Resolve chunk text using `load_chunks` and the
   recorded runbook hashes.
3. Separately label the hypothesis `consistent`, `insufficient`, or
   `contradicted` against the incident observations, including negations and
   quoted text. Assess uncited claims in this step. An incident outside the
   corpus can legitimately support an uncited explanation.
4. Report valid ID pairs over all cited pairs, supporting pairs over reviewed
   valid pairs, and claims with at least one supporting chunk over all
   hypotheses reviewed. Also report unsupported or contradicted claims over
   all reviewed hypotheses and abstentions over delivered cases with an
   unknown cause label. Record numerator and denominator for each metric.
   Have a second reviewer label independently on the same rows and record
   disagreements before adjudicating.

Calibration examples for reviewers:

| Case | Hypothesis and source | Expected review |
| --- | --- | --- |
| INC-001 | A saturated database connection pool may explain the acquire timeouts. `db-pool-exhaustion.md#signals:1` | Supports if supplied. The incident independently reports pool saturation and acquire timeouts. |
| INC-030 | Transport timeouts interrupted production requests. `network-timeouts.md#signals:1` | A matching source ID can be valid membership. The quoted symptom is insufficient support and successful requests contradict the claim. |
| INC-027 | An expired signing certificate may explain failed sign-ins. No source. | Incident-consistent and uncited. The runbook corpus has no certificate guidance. |

Cases INC-025 and INC-026 exercise abstention for a vague complaint and a badge
design request. INC-027 through INC-029 contain real synthetic observations
outside the corpus, including signing, permission, and localization issues.
INC-030 measures resistance to sample error labels. Dataset labels were kept
unchanged.

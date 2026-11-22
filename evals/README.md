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
- Validation uses the strict Pydantic response schema. Both accuracy
  denominators include failed cases, which count as incorrect. Reports also
  count terminal failures, attempted calls and errors on individual attempts,
  including attempts that subsequently recovered.
- The uncertainty check applies when expected severity or cause is `unknown`.
  It requires both a human-review flag and a nonempty limitations list.
  Citation validity is the fraction of valid responses for which every cited
  ID belongs to the supplied chunks. Empty lists pass. This measures attribution
  integrity and does not assess whether the answer used the evidence correctly.
- Latency uses a monotonic wall clock and covers retrieval, analysis requests,
  pacing, retries and retry delays. It includes failures. Live mode builds the
  index before timing begins.
- Live mode pairs the same 30 cases with and without RAG using one model and
  the same provider sampling settings and strict schema. It alternates arm
  order and reports RAG minus baseline deltas. Provider sampling, deployment,
  network conditions and rate limits can affect results across runs. A single
  run does not establish a reliable improvement.

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

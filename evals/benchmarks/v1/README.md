# Historical 30-case benchmark

These files are byte-for-byte copies of the saved synthetic [live A/B report](live.json)
and [local retrieval report](retrieval.json). They predate the Milestone 6 prompt,
schema, retry policy, and citation changes. The reports store labels, runbook and
case IDs, timing, allowlisted error codes, and run metadata. Incident texts,
raw model responses, provider error bodies, headers, and credentials are absent.
Original local reports were preserved.

| Input or artifact | SHA-256 |
| --- | --- |
| [Dataset 1.0.0](../../incidents-v1.json) | `794dd911f3340c8896b9c22d37a2fe85b69bc4af023c60e907828cb9082e4a5b` |
| `live.json` | `bdeae9d72f80bbb17d11af632e2bc60b6a8acb7b48f25c2fe66ca550ac14a53f` |
| `retrieval.json` | `c7875ae3fa09898293f6644ac020fb820c9094d8c5aed34f899c658653f22f48` |

Live run finished November 23, 2026 UTC. Both arms used Groq-hosted
`openai/gpt-oss-20b`, provider sampling defaults, six seconds between requests,
and at most one retry. RAG used up to three above-threshold chunks from
`sentence-transformers/all-MiniLM-L6-v2` at cosine similarity ≥ 0.30.
The retrieval run finished November 22, 2026 UTC. Both record hashes for the
eight runbooks and versions for NumPy (2.5.5), sentence-transformers (5.7.0),
CPU PyTorch (2.15.0+cpu), and Groq SDK (1.7.0). A model revision and provider
deployment fingerprint were not recorded.

| Live metric | Without RAG | With RAG |
| --- | ---: | ---: |
| Successful per scheduled | 26/30 | 26/30 |
| Severity correct, paired successes | 14/22 | 15/22 |
| Leading-cause category correct, paired successes | 18/22 (81.82%) | 20/22 (90.91%) |
| Severity correct, own-arm successes | 17/26 | 16/26 |
| Leading cause correct, own-arm successes | 21/26 | 24/26 |
| Severity correct per scheduled case | 17/30 | 16/30 |
| Leading cause correct per scheduled case | 21/30 | 24/30 |
| Terminal HTTP 400 with `json_validate_failed` | 4/30 | 4/30 |
| Rate-limited attempts, recovered | 2/32 | 1/31 |
| Review flag and limitations on successful uncertainty cases | 2/3 | 0/2 |
| Correct unknown labels on successful uncertainty cases | 1/3 | 0/2 |

Three cases per arm had an expected unknown label. With RAG, one of those
cases failed. The unknown-label metric comes from offline rescoring.
There were 52 schema-valid delivered analyses across 60 scheduled arm cases.
The 8 terminal failures had no locally assessable completion. The paired
subset excludes eight incidents that failed in one arm. The historical timings
include pacing and delays. Historical source IDs represent automatically
attached retrieval context and support no semantic groundedness estimate.

Document-level retrieval reported Recall@1 91.67%, Recall@3 100%, Precision@3
36.11%, and MRR 0.9722 over 24 positives. One of six negatives matched. The
retrieval evaluator ranks up to 16 chunks before deduplicating by document.
Production selects up to three chunks by default and may include multiple
chunks from one document.

From the repository root, recompute metrics locally from the saved rows:

```bash
sha256sum evals/incidents-v1.json evals/benchmarks/v1/{live,retrieval}.json
python -m evals rescore --saved-report evals/benchmarks/v1/live.json
python -m evals rescore --saved-report evals/benchmarks/v1/retrieval.json
```

Rescores go to ignored `evals/results/`. See the [evaluation guide](../../README.md)
for scoring definitions, limitations, and the claim review protocol.

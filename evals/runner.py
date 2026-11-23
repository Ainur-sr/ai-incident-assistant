from asyncio import sleep
from collections import Counter
from collections.abc import Awaitable, Callable
from statistics import mean, median
from time import perf_counter

from pydantic import BaseModel, ConfigDict, ValidationError

from app.llm import AnalysisError, ErrorCategory, Failure
from app.models import IncidentAnalysis, IncidentRequest, Severity
from app.retrieval import RunbookChunk
from evals.metrics import CAUSE_PATTERNS, classify_cause, ratio, retrieval_metrics, severity_metrics


class Case(IncidentRequest):
    id: str
    expected_severity: Severity
    expected_cause: str
    relevant_documents: list[str]
    tags: list[str]

    def incident(self) -> IncidentRequest:
        return IncidentRequest(description=self.description, logs=self.logs)


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    cases: list[Case]

    def check_labels(self, documents: set[str]) -> None:
        if not self.cases or len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Evaluation requires a nonempty dataset with unique case IDs.")
        for case in self.cases:
            if case.expected_cause not in {*CAUSE_PATTERNS, "unknown"}:
                raise ValueError("Unsupported cause label in dataset.")
            if not set(case.relevant_documents) <= documents:
                raise ValueError("Unknown runbook label in dataset.")


Retrieve = Callable[[str, str | None, int], list[RunbookChunk]]
Analyze = Callable[[IncidentRequest, list[RunbookChunk] | None], Awaitable[object]]
RETRYABLE = {Failure.TIMEOUT, Failure.RATE_LIMIT, Failure.UNAVAILABLE}


def evaluate_retrieval(cases: list[Case], retrieve: Retrieve, chunk_count: int) -> dict:
    rows = []
    for case in cases:
        chunks = retrieve(case.description, case.logs, chunk_count)
        rows.append({
            "id": case.id,
            "expected_documents": case.relevant_documents,
            "retrieved_documents": list(dict.fromkeys(chunk.document for chunk in chunks)),
        })
    return {
        "metrics": retrieval_metrics([
            (row["expected_documents"], row["retrieved_documents"]) for row in rows
        ]),
        "cases": rows,
    }


def pace(analyze: Analyze, interval: float) -> Analyze:
    next_request = 0.0

    async def call(incident: IncidentRequest, evidence: list[RunbookChunk] | None) -> object:
        nonlocal next_request
        delay = next_request - perf_counter()
        if delay > 0:
            await sleep(delay)
        next_request = perf_counter() + interval
        try:
            return await analyze(incident, evidence)
        except AnalysisError as error:
            if error.failure is Failure.RATE_LIMIT:
                next_request = perf_counter() + max(60.0, interval, error.retry_after or 0.0)
            raise

    return call


async def evaluate_case(
    case: Case, analyze: Analyze, retrieve: Retrieve | None, top_k: int, retries: int
) -> dict:
    end_to_end_start = perf_counter()
    evidence = None
    result = None
    error = None
    schema_valid = None
    attempts = 0
    attempt_errors = []
    attempt_error_details = []
    if retrieve is not None:
        try:
            evidence = retrieve(case.description, case.logs, top_k)
        except (ImportError, OSError, RuntimeError, ValueError):
            error = "retrieval_unavailable"

    if error is None:
        for attempt in range(retries + 1):
            attempts += 1
            retryable = False
            http_status = None
            provider_error = {}
            try:
                raw = await analyze(case.incident(), evidence)
                result = IncidentAnalysis.model_validate(raw, strict=True)
            except ValidationError:
                schema_valid = False
                error = ErrorCategory.SCHEMA_VALIDATION.value
            except AnalysisError as exc:
                error = exc.category.value
                http_status = exc.http_status
                provider_error = exc.provider_error
                retryable = exc.failure in RETRYABLE
                if exc.category is ErrorCategory.SCHEMA_VALIDATION:
                    schema_valid = False
            except Exception:
                error = ErrorCategory.UNKNOWN.value
            else:
                schema_valid = True
                error = None
                break

            attempt_errors.append(error)
            detail = {"attempt": attempts, "category": error, "http_status": http_status}
            if provider_error:
                detail["provider_error"] = provider_error
            attempt_error_details.append(detail)
            if not retryable:
                break
            if attempt < retries and error != ErrorCategory.HTTP_429:
                await sleep(2 ** attempt)

    predicted_cause, cause_matches = classify_cause(result.likely_causes) if result else (None, None)
    uncertainty_expected = "unknown" in (case.expected_severity, case.expected_cause)
    return {
        "id": case.id,
        "expected_severity": case.expected_severity,
        "predicted_severity": result.severity if result else None,
        "expected_cause": case.expected_cause,
        "predicted_cause": predicted_cause,
        "cause_category_matches": cause_matches,
        "schema_valid": schema_valid,
        "error": error,
        "attempts": attempts,
        "latency_ms": (perf_counter() - end_to_end_start) * 1000,
        "attempt_errors": attempt_errors,
        "attempt_error_details": attempt_error_details,
        "uncertainty_expected": uncertainty_expected,
        "uncertainty_present": bool(result and result.needs_human_review and result.limitations),
        "sources": result.sources if result else [],
        "expected_documents": case.relevant_documents,
        "retrieved_source_ids": (
            [chunk.source_id for chunk in evidence or []] if error != "retrieval_unavailable" else None
        ),
        "citations_valid": (
            set(result.sources) <= {chunk.source_id for chunk in evidence or []}
            if result else None
        ),
    }


def successful(row: dict) -> bool:
    return row["error"] is None and row["schema_valid"] is True


def analysis_report(rows: list[dict]) -> dict:
    count = len(rows)
    successes = [row for row in rows if successful(row)]
    failures = [row for row in rows if not successful(row)]
    valid = sum(row["schema_valid"] is True for row in rows)
    invalid = sum(row["schema_valid"] is False for row in rows)
    uncertain = [row for row in successes if row["uncertainty_expected"]]
    abstained = sum(
        all(row[f"predicted_{field}"] == "unknown"
            for field in ("severity", "cause") if row[f"expected_{field}"] == "unknown")
        for row in uncertain
    )
    citing = [row for row in successes if row["sources"]]
    checked_citations = [row for row in citing if row["citations_valid"] is not None]
    coverage = []
    for row in successes:
        expected = set(row.get("expected_documents") or [])
        if not expected:
            continue
        sources = set(row["sources"])
        retrieved_ids = row.get("retrieved_source_ids")
        if retrieved_ids is not None:
            sources.intersection_update(retrieved_ids)
        elif sources and row["citations_valid"] is not True:
            # Historical rows have only a verdict for the complete citation list.
            continue
        cited_documents = {source.split("#", 1)[0] for source in sources}
        coverage.append(len(expected & cited_documents) / len(expected))
    latency = [row["latency_ms"] for row in rows]
    return {
        "metrics": {
            "sample_count": count,
            "success_count": len(successes),
            "success_rate": ratio(len(successes), count),
            "severity_incorrect_count": sum(
                row["expected_severity"] != row["predicted_severity"] for row in successes
            ),
            **severity_metrics([
                (row["expected_severity"], row["predicted_severity"]) for row in successes
            ]),
            "cause_incorrect_count": sum(
                row["expected_cause"] != row["predicted_cause"] for row in successes
            ),
            "cause_accuracy": ratio(sum(
                row["expected_cause"] == row["predicted_cause"] for row in successes
            ), len(successes)),
            "schema_valid_count": valid,
            "schema_invalid_count": invalid,
            "schema_unassessed_count": count - valid - invalid,
            "schema_valid_rate": ratio(valid, valid + invalid),
            "error_count": len(failures),
            "errors_by_type": dict(Counter(row["error"] for row in failures)),
            "attempt_errors_by_type": dict(Counter(
                error for row in rows for error in row["attempt_errors"]
            )),
            "analysis_attempts": sum(row["attempts"] for row in rows),
            "uncertainty_expected_count": sum(row["uncertainty_expected"] for row in rows),
            "uncertainty_sample_count": len(uncertain),
            "unknown_label_match_count": abstained,
            "unknown_label_match_rate": ratio(abstained, len(uncertain)),
            "uncertainty_rate": ratio(sum(row["uncertainty_present"] for row in uncertain), len(uncertain)),
            "responses_with_sources": len(citing),
            "source_coverage_rate": ratio(len(citing), len(successes)),
            "citation_checked_count": len(checked_citations),
            "citation_validity_rate": ratio(
                sum(row["citations_valid"] is True for row in checked_citations), len(checked_citations)
            ),
            "relevant_source_sample_count": len(coverage),
            "relevant_source_recall": mean(coverage) if coverage else None,
            "latency_mean_ms": mean(latency) if latency else None,
            "latency_median_ms": median(latency) if latency else None,
        },
        "cases": rows,
    }


def comparison_report(without_rag: list[dict], with_rag: list[dict]) -> dict:
    baseline = analysis_report(without_rag)
    treatment = analysis_report(with_rag)
    treatment_by_id = {row["id"]: row for row in with_rag if successful(row)}
    paired_without = [row for row in without_rag if successful(row) and row["id"] in treatment_by_id]
    paired_with = [treatment_by_id[row["id"]] for row in paired_without]
    paired_baseline = analysis_report(paired_without)
    paired_treatment = analysis_report(paired_with)

    differences = {}
    for metric in ("severity_accuracy", "severity_macro_f1", "cause_accuracy",
                   "latency_mean_ms", "latency_median_ms"):
        before, after = paired_baseline["metrics"][metric], paired_treatment["metrics"][metric]
        differences[metric] = after - before if before is not None and after is not None else None
    return {
        "without_rag": baseline,
        "with_rag": treatment,
        "paired": {
            "case_ids": [row["id"] for row in paired_without],
            "without_rag": paired_baseline["metrics"],
            "with_rag": paired_treatment["metrics"],
            "with_minus_without": differences,
        },
        "with_minus_without": differences,
    }


async def evaluate_analysis(
    cases: list[Case],
    analyze: Analyze,
    retrieve: Retrieve | None = None,
    top_k: int = 3,
    retries: int = 0,
) -> dict:
    return analysis_report([
        await evaluate_case(case, analyze, retrieve, top_k, retries) for case in cases
    ])


async def compare_rag(
    cases: list[Case], analyze: Analyze, retrieve: Retrieve, top_k: int, retries: int
) -> dict:
    arms = {"without_rag": [], "with_rag": []}
    for i, case in enumerate(cases):
        treatments = [("without_rag", None), ("with_rag", retrieve)]
        # Alternate order to reduce the effect of time and provider conditions.
        for name, lookup in treatments[::(-1 if i % 2 else 1)]:
            arms[name].append(await evaluate_case(case, analyze, lookup, top_k, retries))

    return comparison_report(arms["without_rag"], arms["with_rag"])

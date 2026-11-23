import json
from unittest.mock import AsyncMock

import pytest

from app.llm import AnalysisError, Failure
from app.retrieval import RunbookChunk
from evals import __main__ as cli
from evals import runner
from evals.metrics import cause_category, retrieval_metrics, severity_metrics

pytestmark = pytest.mark.anyio


def test_retrieval_metrics_deduplicate_documents_and_penalize_misses():
    metrics = retrieval_metrics([
        (["one", "two"], ["one", "one", "unrelated", "two"]),
        (["three"], ["extra1", "extra2", "extra3", "three"]),
        (["four"], []),
        ([], ["bad", "bad"]),
        ([], []),
    ])

    assert metrics["recall_at_1"] == pytest.approx(1 / 6)
    assert metrics["recall_at_3"] == pytest.approx(1 / 3)
    assert metrics["precision_at_3"] == pytest.approx(2 / 9)
    assert metrics["mrr"] == pytest.approx((1 + 1 / 4) / 3)
    assert metrics["positive_count"] == 3
    assert metrics["negative_count"] == 2
    assert metrics["negative_false_positive_count_at_3"] == 1
    assert metrics["negative_false_positive_rate_at_3"] == 0.5


def test_metrics_handle_empty_populations():
    assert retrieval_metrics([])["mrr"] is None
    assert retrieval_metrics([([], ["one"])])["recall_at_1"] is None
    assert retrieval_metrics([(["one"], [])])["negative_false_positive_rate_at_3"] is None
    assert severity_metrics([])["severity_accuracy"] is None


def test_severity_metrics_weights_labels_equally():
    metrics = severity_metrics([("low", "low"), ("low", "medium"), ("high", "critical")])

    assert metrics["severity_accuracy"] == pytest.approx(1 / 3)
    assert metrics["severity_macro_f1"] == pytest.approx((2 / 3) / 5)


@pytest.mark.parametrize(
    "hypotheses,expected",
    [
        ([], "unknown"),
        (["Cause is unclear."], "unknown"),
        (["The upstream connection pool may be exhausted."], "db_pool"),
        (["CPU usage may explain the slowdown.", "Kafka consumer lag"], "high_cpu"),
        (["The CPU spike may be related to the deployment."], "unclassified"),
        (["A spectral shift could explain it."], "unclassified"),
    ],
)
def test_cause_category_uses_only_the_leading_hypothesis(hypotheses, expected):
    assert cause_category(hypotheses) == expected


def test_dataset_covers_all_documents_and_includes_negatives():
    dataset, digest = cli.load_dataset()

    assert len(digest) == 64
    assert len(dataset.cases) == 30
    assert len({doc for case in dataset.cases for doc in case.relevant_documents}) == 8
    assert sum(not case.relevant_documents for case in dataset.cases) == 6
    assert sum(len(case.relevant_documents) > 1 for case in dataset.cases) == 2


def test_retrieval_evaluation_keeps_highest_chunk_rank_per_document():
    dataset, _ = cli.load_dataset()
    case = dataset.cases[0]
    doc = case.relevant_documents[0]

    def lookup(description, logs, limit):
        assert (description, logs, limit) == (case.description, case.logs, 16)
        return [
            RunbookChunk("1", "other.md", "", "", ""),
            RunbookChunk("2", "other.md", "", "", ""),
            RunbookChunk("3", doc, "", "", ""),
        ]

    results = runner.evaluate_retrieval([case], lookup, 16)

    assert results["cases"][0]["retrieved_documents"] == ["other.md", doc]
    assert results["metrics"]["mrr"] == 0.5


def valid_analysis(severity="high", sources=None):
    return {
        "severity": severity,
        "summary": "Synthetic analysis.",
        "likely_causes": ["The connection pool may be exhausted."],
        "recommended_actions": ["Check pool utilization."],
        "sources": sources or [],
        "needs_human_review": True,
        "limitations": ["Pool metrics are incomplete."],
    }


async def test_compare_rag_pairs_inputs_and_validates_attribution():
    dataset, _ = cli.load_dataset()
    cases = dataset.cases[:2]
    calls = []
    chunk = RunbookChunk("db-pool-exhaustion.md#signals:1", "db-pool-exhaustion.md", "", "", "")

    async def analyze(incident, evidence):
        calls.append((incident.description, evidence))
        if evidence:
            severity = {case.description: case.expected_severity for case in cases}
            return valid_analysis(severity[incident.description], [chunk.source_id])
        return valid_analysis("unknown", ["fabricated.md"])

    report = await runner.compare_rag(cases, analyze, lambda *_: [chunk], 3, 0)

    assert calls == [
        (cases[0].description, None), (cases[0].description, [chunk]),
        (cases[1].description, [chunk]), (cases[1].description, None),
    ]
    assert report["with_minus_without"]["severity_accuracy"] == 1
    assert report["with_rag"]["metrics"]["citation_validity_rate"] == 1
    assert report["without_rag"]["metrics"]["citation_validity_rate"] == 0


async def test_retry_records_recovery_and_rejects_invalid_responses(monkeypatch):
    dataset, _ = cli.load_dataset()
    pause = AsyncMock()
    monkeypatch.setattr(runner, "sleep", pause)
    analyze = AsyncMock(side_effect=[
        AnalysisError(Failure.TIMEOUT), valid_analysis(), {"severity": "low"}
    ])

    report = await runner.evaluate_analysis(dataset.cases[:2], analyze, retries=2)

    pause.assert_awaited_once_with(1)
    metrics = report["metrics"]
    assert metrics["schema_valid_count"] == 1
    assert metrics["schema_invalid_count"] == 1
    assert metrics["schema_valid_rate"] == 0.5
    assert metrics["severity_accuracy"] == 1
    assert metrics["cause_accuracy"] == 1
    assert metrics["analysis_attempts"] == 3
    assert metrics["errors_by_type"] == {"schema_validation": 1}
    assert metrics["attempt_errors_by_type"] == {"timeout": 1, "schema_validation": 1}
    assert all(row["latency_ms"] >= 0 for row in report["cases"])


async def test_rate_limit_paces_subsequent_calls_even_without_retry(monkeypatch):
    pause = AsyncMock()
    monkeypatch.setattr(runner, "sleep", pause)
    times = iter([10, 10, 11, 12, 91])
    monkeypatch.setattr(runner, "perf_counter", lambda: next(times))
    analyze = runner.pace(AsyncMock(side_effect=[
        AnalysisError(Failure.RATE_LIMIT, retry_after=80), valid_analysis(),
    ]), 3)
    dataset, _ = cli.load_dataset()
    incident = dataset.cases[0].incident()

    with pytest.raises(AnalysisError):
        await analyze(incident, None)
    await analyze(incident, None)
    pause.assert_awaited_once_with(79)


def test_live_requires_opt_in_before_loading_any_configuration(monkeypatch):
    monkeypatch.setattr(cli.config, "groq_api_key", lambda: pytest.fail("Read live key"))
    monkeypatch.setattr(cli.llm, "create_groq_client", lambda *_: pytest.fail("Created SDK client"))

    with pytest.raises(SystemExit, match="2"):
        cli.main(["live"])


def test_mock_cli_generates_reports_without_provider_or_model(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.config, "groq_api_key", lambda: pytest.fail("Read live key"))
    monkeypatch.setattr(cli.llm, "create_groq_client", lambda *_: pytest.fail("Created SDK client"))
    monkeypatch.setattr(cli.retrieval, "retrieve", lambda *_: pytest.fail("Loaded embeddings"))
    writer = cli.write_reports
    monkeypatch.setattr(cli, "write_reports", lambda report: writer(report, tmp_path))

    cli.main(["mock"])

    report = json.loads((tmp_path / "mock.json").read_text())
    assert report["sample_count"] == 30
    assert report["results"]["metrics"]["schema_valid_count"] == 30
    assert len(report["results"]["cases"]) == 30
    assert "Subjective quality was not scored." in (tmp_path / "mock.md").read_text()


PILOT_IDS = ["INC-001", "INC-004", "INC-014", "INC-023", "INC-025"]


def test_five_case_live_cli_schedules_ten_calls_with_a_mocked_provider(monkeypatch, tmp_path):
    from contextlib import asynccontextmanager

    from app.config import DEFAULT_GROQ_MODEL

    client = object()
    calls = []

    @asynccontextmanager
    async def create_client(api_key):
        assert api_key == "test-placeholder"
        yield client

    async def analyze(incident, sdk_client, model, evidence):
        calls.append((incident, sdk_client, model, evidence))
        return valid_analysis()

    monkeypatch.setattr(cli.config, "groq_api_key", lambda: "test-placeholder")
    monkeypatch.setenv("GROQ_MODEL", DEFAULT_GROQ_MODEL)
    monkeypatch.setattr(cli.llm, "create_groq_client", create_client)
    monkeypatch.setattr(cli.llm, "analyze_with_groq", analyze)
    monkeypatch.setattr(cli.retrieval, "retrieve", lambda *_: [])
    monkeypatch.setattr(cli, "pace", lambda analyze, _: analyze)
    writer = cli.write_reports
    monkeypatch.setattr(cli, "write_reports", lambda report: writer(report, tmp_path))

    cli.main(["live", "--allow-live", "--case-ids", *PILOT_IDS,
              "--request-interval", "3", "--retries", "0"])

    report = json.loads((tmp_path / "live.json").read_text())
    assert report["sample_count"] == 5
    assert report["settings"]["case_ids"] == PILOT_IDS
    assert len(calls) == 10
    assert all(call[1] is client and call[2] == DEFAULT_GROQ_MODEL for call in calls)
    for i in range(0, len(calls), 2):
        assert calls[i][0] == calls[i + 1][0]
        assert {calls[i][3] is None, calls[i + 1][3] is None} == {True, False}
    for arm in ["without_rag", "with_rag"]:
        assert [case["id"] for case in report["results"][arm]["cases"]] == PILOT_IDS
        assert report["results"][arm]["metrics"]["analysis_attempts"] == 5


@pytest.mark.parametrize("ids", [["INC-001", "INC-001"], ["INC-999"]])
def test_cli_rejects_invalid_case_selection_before_reading_key(monkeypatch, ids):
    monkeypatch.setattr(cli.config, "groq_api_key", lambda: pytest.fail("Read live key"))
    with pytest.raises(SystemExit, match="2"):
        cli.main(["live", "--allow-live", "--case-ids", *ids])


async def test_source_coverage_and_recall_distinguish_empty_valid_and_invalid_citations():
    dataset, _ = cli.load_dataset()
    case = dataset.cases[0]
    document, = case.relevant_documents
    first = RunbookChunk(f"{document}#signals:1", document, "", "", "")
    second = RunbookChunk(f"{document}#signals:2", document, "", "", "")
    lookup = lambda *_: [first, second]
    analyze = AsyncMock(side_effect=[
        valid_analysis(),
        valid_analysis(sources=[first.source_id, second.source_id]),
        valid_analysis(sources=[f"{document}#fabricated:1"]),
    ])

    report = await runner.evaluate_analysis([case] * 3, analyze, lookup)
    metrics = report["metrics"]

    assert [row["citations_valid"] for row in report["cases"]] == [True, True, False]
    assert metrics["source_coverage_rate"] == pytest.approx(2 / 3)
    assert metrics["citation_checked_count"] == 2
    assert metrics["citation_validity_rate"] == 0.5
    assert metrics["relevant_source_sample_count"] == 3
    assert metrics["relevant_source_recall"] == pytest.approx(1 / 3)


async def test_rescore_legacy_reports_uses_intersection_and_preserves_failures(monkeypatch, tmp_path):
    dataset, digest = cli.load_dataset()
    cases = [dataset.cases[0], dataset.cases[3], dataset.cases[24]]
    first, second, unknown = cases
    before = await runner.evaluate_analysis(cases, AsyncMock(side_effect=[
        valid_analysis(first.expected_severity),
        valid_analysis(second.expected_severity),
        AnalysisError(Failure.REQUEST_FAILED),
    ]))
    after = await runner.evaluate_analysis(cases[::-1], AsyncMock(side_effect=[
        AnalysisError(Failure.REQUEST_FAILED),
        AnalysisError(Failure.REQUEST_FAILED),
        valid_analysis("low"),
    ]))
    for arm in (before, after):
        for row in arm["cases"]:
            row.pop("retrieved_source_ids")
            row.pop("attempt_error_details")
            if row["error"] == "unknown":
                row["error"] = "request_failed"
                row["attempt_errors"] = ["request_failed"]
            row.pop("expected_documents")
            row["schema_valid"] = not row["error"]

    source = tmp_path / "live.json"
    original = json.dumps({
        "mode": "live", "generated_at_utc": "original", "dataset_version": dataset.version,
        "sample_count": len(cases), "settings": {"dataset_sha256": digest},
        "results": {"without_rag": before, "with_rag": after},
    })
    source.write_text(original)
    monkeypatch.setattr(cli.config, "groq_api_key", lambda: pytest.fail("Read live key"))
    monkeypatch.setattr(cli.llm, "create_groq_client", lambda *_: pytest.fail("Created client"))
    monkeypatch.setattr(cli.retrieval, "retrieve", lambda *_: pytest.fail("Ran retrieval"))

    cli.main(["rescore", "--saved-report", str(source)])

    assert source.read_text() == original
    report = json.loads((tmp_path / "live-rescored.json").read_text())
    assert report["generated_at_utc"] == "original"
    results = report["results"]
    assert results["paired"]["case_ids"] == [first.id]
    assert results["with_minus_without"]["severity_accuracy"] == -1
    assert results["without_rag"]["metrics"]["severity_accuracy"] == 1
    assert results["without_rag"]["metrics"]["success_rate"] == pytest.approx(2 / 3)
    metrics = results["with_rag"]["metrics"]
    assert metrics["severity_incorrect_count"] == 1
    assert metrics["schema_invalid_count"] == 0
    assert metrics["schema_unassessed_count"] == 2
    assert metrics["schema_valid_rate"] == 1
    assert metrics["citation_validity_rate"] is None
    assert metrics["uncertainty_expected_count"] == 1
    assert metrics["uncertainty_rate"] is None
    assert results["with_rag"]["cases"][0]["id"] == unknown.id
    assert results["with_rag"]["cases"][0]["error"] == "request_failed"
    assert results["with_rag"]["cases"][-1]["expected_documents"] == first.relevant_documents
    assert "request_failed" in (tmp_path / "live-rescored.md").read_text()


def test_empty_comparison_has_no_accuracy_or_delta():
    report = runner.comparison_report([], [])

    assert report["paired"]["case_ids"] == []
    assert report["with_minus_without"]["severity_accuracy"] is None
    assert report["without_rag"]["metrics"]["success_rate"] is None
    assert report["without_rag"]["metrics"]["schema_valid_rate"] is None


@pytest.mark.parametrize(
    "case_id,severity,cause,hypothesis",
    [
        ("INC-001", "high", "db_pool", "Database clients are all checked out."),
        ("INC-004", "critical", "http_503", "The gateway reports zero eligible backends."),
        ("INC-014", "low", "kafka_lag", "Partition 4 has a backlog."),
    ],
)
def test_pilot_labels_and_cause_paraphrases(case_id, severity, cause, hypothesis):
    dataset, _ = cli.load_dataset()
    case = next(case for case in dataset.cases if case.id == case_id)

    assert (case.expected_severity, case.expected_cause) == (severity, cause)
    assert cause_category([hypothesis]) == cause


@pytest.mark.parametrize(
    "failure,category,status,schema_valid",
    [
        (429, "http_429", 429, None),
        (400, "http_4xx", 400, None),
        (401, "http_4xx", 401, None),
        (503, "http_5xx", 503, None),
        ("timeout", "timeout", None, None),
        ("connection", "connection", None, None),
        ("schema", "schema_validation", None, False),
        ("truncated", "invalid_response", None, None),
        ("unknown", "unknown", None, None),
    ],
)
async def test_evaluation_records_safe_provider_error_details(
    failure, category, status, schema_valid, groq_client_factory
):
    import httpx

    secret = "synthetic-sensitive-marker"

    def handler(request):
        if failure == "timeout":
            raise httpx.ReadTimeout(secret, request=request)
        if failure == "connection":
            raise httpx.ConnectError(secret, request=request)
        if isinstance(failure, int):
            return httpx.Response(failure, json={"error": {"message": secret}})
        return httpx.Response(200, json={"choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": json.dumps({"severity": secret})},
            "finish_reason": "length" if failure == "truncated" else "stop",
        }]})

    dataset, _ = cli.load_dataset()
    async with groq_client_factory(handler, api_key=secret, max_retries=0) as client:
        async def analyze(incident, evidence):
            if failure == "unknown":
                raise RuntimeError(secret)
            return await cli.llm.analyze_with_groq(incident, client, "synthetic-model", evidence)

        report = await runner.evaluate_analysis(dataset.cases[:1], analyze)

    row, = report["cases"]
    assert row["schema_valid"] is schema_valid
    assert row["error"] == category
    assert row["attempt_error_details"] == [
        {"attempt": 1, "category": category, "http_status": status}
    ]
    assert report["metrics"]["errors_by_type"] == {category: 1}
    assert report["metrics"]["severity_accuracy"] is None
    assert secret not in json.dumps(report)


@pytest.mark.parametrize("with_rag", [False, True])
async def test_inc025_request_and_http_400(with_rag, groq_client_factory):
    import httpx

    dataset, _ = cli.load_dataset()
    case = next(case for case in dataset.cases if case.id == "INC-025")
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(400, json={"error": {"message": "synthetic rejection"}})

    async with groq_client_factory(handler, api_key="synthetic", max_retries=0) as client:
        async def analyze(incident, evidence):
            return await cli.llm.analyze_with_groq(incident, client, "synthetic-model", evidence)

        report = await runner.evaluate_analysis(
            [case], analyze, (lambda *_: []) if with_rag else None, retries=2
        )

    payload, = requests
    expected = {"description": case.description, "logs": None}
    if with_rag:
        expected["runbook_evidence"] = []
    assert json.loads(payload["messages"][1]["content"]) == expected
    assert payload["response_format"]["type"] == "json_schema"
    spec = payload["response_format"]["json_schema"]
    assert spec["strict"] is True
    schema = spec["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    row, = report["cases"]
    assert row["schema_valid"] is None
    assert row["attempt_error_details"] == [
        {"attempt": 1, "category": "http_4xx", "http_status": 400}
    ]


@pytest.mark.parametrize(
    "hypothesis,expected_matches",
    [("The backlog may reflect worker contention.", []),
     ("Kafka consumer lag may coincide with a slow query.", ["kafka_lag", "slow_query"])],
)
async def test_unclassified_cause_records_only_matching_categories(hypothesis, expected_matches):
    dataset, _ = cli.load_dataset()
    case = next(case for case in dataset.cases if case.id == "INC-014")
    response = {**valid_analysis(), "likely_causes": [hypothesis]}
    report = await runner.evaluate_analysis([case], AsyncMock(return_value=response))
    row, = report["cases"]

    assert row["predicted_cause"] == "unclassified"
    assert row["cause_category_matches"] == expected_matches
    assert hypothesis not in json.dumps(report)


@pytest.mark.parametrize("wrapped", [True, False])
async def test_http_400_keeps_only_allowlisted_provider_fields(wrapped, groq_client_factory):
    import httpx

    secret = "synthetic-private-value"
    fields = {
        "type": "invalid_request_error",
        "code": "json_validate_failed",
        "param": "response_format",
        "message": secret,
        "failed_generation": secret,
    }

    def handler(request):
        return httpx.Response(
            400, json={"error": fields} if wrapped else fields,
            headers={"x-private": secret},
        )

    dataset, _ = cli.load_dataset()
    async with groq_client_factory(handler, api_key=secret, max_retries=0) as client:
        async def analyze(incident, evidence):
            return await cli.llm.analyze_with_groq(incident, client, "synthetic-model", evidence)

        report = await runner.evaluate_analysis(dataset.cases[:1], analyze)

    attempt, = report["cases"][0]["attempt_error_details"]
    assert attempt == {
        "attempt": 1, "category": "http_4xx", "http_status": 400,
        "provider_error": {"type": fields["type"], "code": fields["code"], "param": fields["param"]},
    }
    assert report["metrics"]["errors_by_type"] == {"http_4xx": 1}
    assert secret not in json.dumps(report)


@pytest.mark.parametrize("body", [
    "synthetic-private-value",
    {"error": {"type": "synthetic-private-value", "code": [], "param": {"private": "value"}}},
])
def test_provider_metadata_omits_unknown_values_and_non_object_bodies(body):
    assert cli.llm.safe_provider_error(body) == {}


async def test_uncertainty_flags_and_expected_unknown_labels_are_independent():
    dataset, _ = cli.load_dataset()
    cases = [case for case in dataset.cases if case.id in {"INC-025", "INC-026", "INC-030"}]
    analyze = AsyncMock(side_effect=[
        AnalysisError(Failure.REQUEST_FAILED),
        {**valid_analysis("low"), "likely_causes": []},
        {**valid_analysis("low"), "likely_causes": [], "needs_human_review": False},
    ])

    metrics = (await runner.evaluate_analysis(cases, analyze))["metrics"]

    assert metrics["uncertainty_expected_count"] == 3
    assert metrics["uncertainty_sample_count"] == 2
    assert metrics["uncertainty_rate"] == 0.5
    assert metrics["unknown_label_match_count"] == 1
    assert metrics["unknown_label_match_rate"] == 0.5

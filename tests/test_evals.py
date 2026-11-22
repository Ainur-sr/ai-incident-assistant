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


def test_severity_metrics_count_failures_and_weight_labels_equally():
    metrics = severity_metrics([("low", "low"), ("low", None), ("high", "critical")])

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
    assert metrics["severity_accuracy"] == 0.5
    assert metrics["cause_accuracy"] == 0.5
    assert metrics["analysis_attempts"] == 3
    assert metrics["errors_by_type"] == {"invalid_response": 1}
    assert metrics["attempt_errors_by_type"] == {"timeout": 1, "invalid_response": 1}
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

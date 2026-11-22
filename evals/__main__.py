import argparse
import asyncio
import math
from datetime import UTC, datetime
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from json import dumps
from pathlib import Path

from app import config, llm, retrieval
from app.analysis import analyze_incident
from app.models import IncidentRequest
from app.retrieval import RunbookChunk
from evals.runner import Case, Dataset, compare_rag, evaluate_analysis, evaluate_retrieval, pace

EVAL_DIR = Path(__file__).resolve().parent
DATASET_PATH = EVAL_DIR / "incidents-v1.json"
RESULTS_DIR = EVAL_DIR / "results"


def installed_versions(*names: str) -> dict:
    versions = {}
    for name in names:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = None
    return versions


def write_reports(report: dict, directory: Path = RESULTS_DIR) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{report['mode']}.json"
    markdown_path = directory / f"{report['mode']}.md"
    json_path.write_text(dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    lines = [
        f"# Incident evaluation — {report['mode']}", "",
        f"Dataset version {report['dataset_version']} ({report['sample_count']} cases).", "",
        "| Run setting | Value |", "| --- | --- |",
    ]
    lines.extend(
        f"| {key} | `{dumps(value)}` |"
        for key, value in report["settings"].items()
    )
    results = report["results"]
    sections = (
        [("Without RAG", results["without_rag"]["metrics"]),
         ("With RAG", results["with_rag"]["metrics"]),
         ("With minus without", results["with_minus_without"])]
        if report["mode"] == "live" else [("Metrics", results["metrics"])]
    )
    for heading, metrics in sections:
        lines += ["", f"## {heading}", "", "| Metric | Value |", "| --- | --- |"]
        for key, value in metrics.items():
            display = f"{value:.4f}" if isinstance(value, float) else dumps(value)
            lines.append(f"| {key} | {display} |")
    lines += [
        "", "See the JSON report for individual case outcomes.",
        "Subjective quality was not scored. See the manual rubric in evals/README.md.",
    ]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def load_dataset() -> tuple[Dataset, str]:
    raw = DATASET_PATH.read_bytes()
    dataset = Dataset.model_validate_json(raw, strict=True)
    dataset.check_labels({chunk.document for chunk in retrieval.load_chunks(retrieval.RUNBOOK_DIR)})
    return dataset, sha256(raw).hexdigest()


async def mock_analysis(incident: IncidentRequest, _evidence: list[RunbookChunk] | None) -> dict:
    return analyze_incident(incident).model_dump()


async def run_live(
    cases: list[Case], api_key: str, model: str, interval: float, retries: int
) -> dict:
    async with llm.create_groq_client(api_key) as client:
        async def analyze(incident, evidence):
            return await llm.analyze_with_groq(incident, client, model, evidence)

        return await compare_rag(cases, pace(analyze, interval), retrieval.retrieve, 3, retries)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate synthetic incidents.")
    parser.add_argument("mode", choices=("retrieval", "mock", "live"))
    parser.add_argument("--allow-live", action="store_true", help="Authorize real GroqCloud requests.")
    parser.add_argument("--case-ids", nargs="+", metavar="ID", help="Evaluate selected cases in the given order.")
    parser.add_argument("--request-interval", type=float, default=3.0)
    parser.add_argument("--retries", type=int, default=2, help="Retries per live case and arm (0–5).")
    args = parser.parse_args(argv)
    if args.mode == "live" and not args.allow_live:
        parser.error("Live evaluation requires explicit --allow-live.")
    if args.mode != "live" and args.allow_live:
        parser.error("--allow-live is only valid in live mode.")
    if not math.isfinite(args.request_interval) or args.request_interval < 0 or not 0 <= args.retries <= 5:
        parser.error("Request interval must be finite and nonnegative, and retries must be 0–5.")

    dataset, dataset_digest = load_dataset()
    cases = dataset.cases
    if args.case_ids:
        by_id = {case.id: case for case in cases}
        if len(set(args.case_ids)) != len(args.case_ids):
            parser.error("--case-ids must be unique.")
        if not set(args.case_ids) <= by_id.keys():
            parser.error("--case-ids includes an unknown dataset ID.")
        cases = [by_id[case_id] for case_id in args.case_ids]

    settings = {
        "dataset_sha256": dataset_digest,
        "case_ids": [case.id for case in cases],
        "packages": installed_versions("numpy", "sentence-transformers", "torch", "groq"),
    }
    if args.mode != "mock":
        corpus = {path.name: sha256(path.read_bytes()).hexdigest()
                  for path in sorted(retrieval.RUNBOOK_DIR.glob("*.md")) if not path.is_symlink()}
        settings.update(embedding_model=retrieval.MODEL_ID,
                        minimum_cosine_similarity=retrieval.MIN_SIMILARITY, runbook_sha256=corpus)

    if args.mode == "retrieval":
        chunk_count = len(retrieval.load_chunks(retrieval.RUNBOOK_DIR))
        settings.update(chunk_limit=chunk_count, rank_unit="unique document")
        try:
            results = evaluate_retrieval(cases, retrieval.retrieve, chunk_count)
        except (ImportError, OSError, RuntimeError, ValueError):
            parser.exit(1, "Local retrieval failed. Check the RAG dependencies and cached model.\n")
    elif args.mode == "mock":
        settings["analysis"] = "deterministic local rules"
        results = asyncio.run(evaluate_analysis(cases, mock_analysis))
    else:
        api_key = config.groq_api_key()
        if not api_key:
            parser.error("Set GROQ_API_KEY in the process environment.")
        # Build the index before any billable request and exclude startup from timing.
        try:
            retrieval.retrieve("Index warm-up.", None, 1)
        except (ImportError, OSError, RuntimeError, ValueError):
            parser.exit(1, "Local retrieval failed. Check the RAG dependencies and cached model.\n")
        model = config.groq_model()
        settings.update(model=model, rag_top_k=3, request_interval_seconds=args.request_interval,
                        retries=args.retries, sampling="provider defaults for both arms")
        results = asyncio.run(run_live(cases, api_key, model, args.request_interval, args.retries))

    paths = write_reports({
        "mode": args.mode,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "dataset_version": dataset.version,
        "sample_count": len(cases),
        "settings": settings,
        "results": results,
    })
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()

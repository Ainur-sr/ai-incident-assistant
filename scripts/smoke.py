"""Exercise the local container's HTTP interface using only the standard library."""

from argparse import ArgumentParser
from json import dumps, load
from urllib.request import Request, urlopen


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument("--rag", action="store_true")
    args = parser.parse_args()
    base_url = "http://127.0.0.1:8000"
    with urlopen(f"{base_url}/health", timeout=5) as response:
        if load(response) != {"status": "ok"}:
            raise SystemExit("Unexpected health response.")
    request = Request(
        f"{base_url}/analyze",
        data=dumps({
            "description": "Synthetic checkout cannot borrow a database connection. The connection pool is exhausted.",
            "logs": "pool acquire timeout; active=50; idle=0; waiting=150",
        }).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=60) as response:
        analysis = load(response)
    if (analysis["severity"] != "unknown" or analysis["needs_human_review"] is not True
            or analysis["likely_causes"] or analysis["sources"] or analysis["hypothesis_evidence"]):
        raise SystemExit("Unexpected mock analysis.")
    retrieved = analysis["retrieved_sources"]
    if args.rag:
        if not any(source.startswith("db-pool-exhaustion.md#") for source in retrieved):
            raise SystemExit("Expected the database pool runbook in retrieved context.")
    elif retrieved:
        raise SystemExit("Default analysis unexpectedly retrieved runbooks.")
    print("Health and analysis smoke checks passed.")


if __name__ == "__main__":
    main()

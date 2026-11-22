import re
from statistics import mean

SEVERITIES = ("low", "medium", "high", "critical", "unknown")
# Match the leading hypothesis against domain signals or common paraphrases.
CAUSE_PATTERNS = {
    "db_pool": r"\b(?:connection|client) pool\b|pool (?:checkout|exhaust|saturat)|no (?:idle|available) (?:database )?connections",
    "http_503": r"no (?:healthy|ready|eligible) (?:upstreams?|backends?|targets?)|readiness (?:probe|check|fail)|\b503\b|upstream (?:unavailable|overload)",
    "high_cpu": r"\bcpu\b|processor saturat|hot loop|tight (?:polling )?loop",
    "memory_oom": r"memory (?:leak|exhaust|limit|pressure)|out.of.memory|\boom(?:killed)?\b|(?:retained|growing) heap",
    "kafka_lag": r"\bkafka\b|consumer (?:group|lag|backlog)|(?:partition|commit) offsets?",
    "network_timeout": r"\b(?:dns|tcp|tls|etimedout)\b|network (?:timeout|connect|latency)|connection (?:establishment|timed? ?out)",
    "slow_query": r"(?:slow|expensive) (?:sql |database )?quer|query (?:plan|execution|latency|runtime)|lock (?:wait|contention)|(?:index|table|sequential) scan",
    "deploy_regression": r"deploy|rollout|canary|feature flag|(?:new|latest|recent) (?:release|version|revision|config)",
    "certificate": r"certificat|signing key expir",
    "authorization": r"authori[sz]ation|permission|access (?:denied|control)|\b403\b",
    "localization": r"locali[sz]ation|translation|currency (?:symbol|format)",
}
ABSTENTION = re.compile(
    r"unknown(?: cause)?|undetermined|insufficient (?:evidence|data)(?: to determine (?:the )?cause)?|"
    r"(?:root )?cause (?:is )?(?:unknown|unclear|undetermined)|no (?:supported |identifiable )?cause",
    re.IGNORECASE,
)


def cause_category(hypotheses: list[str]) -> str:
    if not hypotheses:
        return "unknown"
    leading = hypotheses[0].strip().rstrip(".")
    if ABSTENTION.fullmatch(leading):
        return "unknown"
    matches = [
        category for category, pattern in CAUSE_PATTERNS.items()
        if re.search(pattern, leading, re.IGNORECASE)
    ]
    return matches[0] if len(matches) == 1 else "unclassified"


def ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def retrieval_metrics(
    samples: list[tuple[list[str], list[str]]],
) -> dict[str, float | int | None]:
    positive = []
    negative_hits = 0
    negatives = 0
    for expected, retrieved in samples:
        rank = list(dict.fromkeys(retrieved))
        relevant = set(expected)
        if not relevant:
            negatives += 1
            negative_hits += bool(rank[:3])
            continue
        top_1_hits = len(relevant.intersection(rank[:1]))
        top_3_hits = len(relevant.intersection(rank[:3]))
        reciprocal_rank = next(
            (1 / i for i, doc in enumerate(rank, 1) if doc in relevant), 0.0
        )
        positive.append((
            top_1_hits / len(relevant),
            top_3_hits / len(relevant),
            top_3_hits / 3,
            reciprocal_rank,
        ))

    result = {
        name: mean(values) for name, values in zip(
            ("recall_at_1", "recall_at_3", "precision_at_3", "mrr"), zip(*positive)
        )
    } if positive else dict.fromkeys(("recall_at_1", "recall_at_3", "precision_at_3", "mrr"))
    return {
        **result,
        "sample_count": len(samples),
        "positive_count": len(positive),
        "negative_count": negatives,
        "negative_false_positive_count_at_3": negative_hits,
        "negative_false_positive_rate_at_3": ratio(negative_hits, negatives),
    }


def severity_metrics(samples: list[tuple[str, str | None]]) -> dict[str, float | None]:
    if not samples:
        return {"severity_accuracy": None, "severity_macro_f1": None}
    f1 = []
    for label in SEVERITIES:
        true_positives = sum(expected == predicted == label for expected, predicted in samples)
        support = sum(expected == label for expected, _ in samples)
        predictions = sum(predicted == label for _, predicted in samples)
        f1.append(2 * true_positives / (support + predictions) if support + predictions else 0)
    return {
        "severity_accuracy": mean(expected == predicted for expected, predicted in samples),
        "severity_macro_f1": mean(f1),
    }

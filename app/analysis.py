import re

from app.models import IncidentAnalysis, IncidentRequest

TIMEOUT_SIGNAL = re.compile(r"\b(?:timeouts?|timed out|deadline exceeded)\b", re.IGNORECASE)


def analyze_incident(incident: IncidentRequest) -> IncidentAnalysis:
    limitations = ["Rule-based assessment. Verify impact and hypotheses with incident evidence."]
    if not (incident.logs or "").strip():
        limitations.append("No application logs were provided.")

    if TIMEOUT_SIGNAL.search(incident.description) or TIMEOUT_SIGNAL.search(incident.logs or ""):
        return IncidentAnalysis(
            severity="unknown",
            summary="The incident text mentions a timeout.",
            likely_causes=[],
            recommended_actions=[
                "Check dependency latency and error rates around the incident time.",
                "Inspect request traces for the operation associated with the timeout.",
            ],
            sources=[],
            needs_human_review=True,
            limitations=[*limitations, "A timeout mention alone does not establish a cause or user impact."],
        )

    return IncidentAnalysis(
        severity="unknown",
        summary="The incident text provides no recognized diagnostic signal.",
        likely_causes=[],
        recommended_actions=[
            "Identify affected services and quantify the scope of user impact.",
            "Collect relevant timestamps, error messages, metrics, and request traces.",
        ],
        sources=[],
        needs_human_review=True,
        limitations=[*limitations, "More evidence is needed to suggest a cause or severity."],
    )

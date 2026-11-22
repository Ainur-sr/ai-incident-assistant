from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Severity = Literal["low", "medium", "high", "critical", "unknown"]


class IncidentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    description: Annotated[str, Field(min_length=1, max_length=2000)]
    logs: Annotated[str, Field(max_length=20000)] | None = None

    @field_validator("description")
    @classmethod
    def reject_blank_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Description must contain non-whitespace text.")
        return value


class IncidentAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Severity
    summary: str
    likely_causes: list[str]
    recommended_actions: list[str]
    sources: list[str]
    needs_human_review: bool
    limitations: list[str]

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class ValidationIssue(BaseModel):
    """Issue discovered during deterministic validation."""

    code: str
    severity: Literal["error", "warning"]
    field_path: str
    message: str
    auto_fixed: bool = False


class ValidationAutoFix(BaseModel):
    """Audit trail for safe deterministic auto-fixes."""

    code: str
    field_path: str
    original_value: Optional[JsonValue] = None
    fixed_value: Optional[JsonValue] = None
    reason: str


class SpotCheckDiscrepancy(BaseModel):
    """One discrepancy found by the capable-model spot-check."""

    field_path: str = Field(description="Path in extracted payload, e.g. attributes.extracted.languages")
    kind: Literal["contradiction", "omission", "unsupported"] = Field(
        default="omission",
        description="Discrepancy class: contradiction, omission, or unsupported.",
    )
    issue: str = Field(description="Short discrepancy label")
    evidence: Optional[str] = Field(
        default=None,
        description="Short source snippet supporting the discrepancy claim.",
    )
    cheap_value: Optional[JsonValue] = None
    capable_value: Optional[JsonValue] = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class SpotCheckOutput(BaseModel):
    """Structured output from capable-model spot-check."""

    has_discrepancy: bool = Field(
        default=False,
        description=(
            "True only for actionable discrepancies (contradiction or unsupported). "
            "Omission-only findings are monitoring signals."
        ),
    )
    discrepancies: list[SpotCheckDiscrepancy] = Field(default_factory=list)
    summary: Optional[str] = None


class ValidationReport(BaseModel):
    """Persisted validation report in schools.attributes.data_validation."""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: int = Field(default=1, alias="_schema_version")
    validated_at: str
    status: Literal["ok", "needs_review"]
    issue_counts: dict[str, int]
    issues: list[ValidationIssue] = Field(default_factory=list)
    auto_fixes: list[ValidationAutoFix] = Field(default_factory=list)
    spot_check: Optional[dict] = None

"""Issue and auto-fix entries on a ``ValidationReport``, shared by the validation sections."""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any, Literal

from app.schemas.validation import ValidationAutoFix, ValidationIssue, ValidationReport


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _to_json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_to_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _to_json_value(v) for k, v in value.items()}
    return value


def _add_issue(
    report: ValidationReport,
    *,
    code: str,
    severity: Literal["error", "warning"],
    field_path: str,
    message: str,
    auto_fixed: bool = False,
) -> None:
    report.issues.append(
        ValidationIssue(
            code=code,
            severity=severity,
            field_path=field_path,
            message=message,
            auto_fixed=auto_fixed,
        )
    )
    report.issue_counts[severity] = int(report.issue_counts.get(severity, 0)) + 1


def _add_fix(
    report: ValidationReport,
    *,
    code: str,
    field_path: str,
    original_value: Any,
    fixed_value: Any,
    reason: str,
) -> None:
    report.auto_fixes.append(
        ValidationAutoFix(
            code=code,
            field_path=field_path,
            original_value=_to_json_value(original_value),
            fixed_value=_to_json_value(fixed_value),
            reason=reason,
        )
    )

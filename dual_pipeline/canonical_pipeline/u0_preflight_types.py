"""
dual_pipeline/canonical_pipeline/u0_preflight_types.py

Preflight diagnostic data structures, batch reports, and cascade error definitions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from dual_pipeline.exceptions import HITLRequiredError


@dataclass
class PreflightDiagnostic:
    check_name: str
    severity: str  # "BLOCKING" | "WARNING"
    field_name: str
    message: str
    remediation: str


@dataclass
class PreflightBatchReport:
    is_valid: bool
    missing_fields: List[str] = field(default_factory=list)
    diagnostics: List[PreflightDiagnostic] = field(default_factory=list)
    hydrated_fields: List[str] = field(default_factory=list)
    review_contexts: List[Dict[str, Any]] = field(default_factory=list)
    speaker_options: List[Dict[str, Any]] = field(default_factory=list)

    def summary(self) -> str:
        if self.is_valid:
            return f"Preflight PASS: {len(self.hydrated_fields)} fields hydrated cleanly."
        diag_lines = [
            f"- [{d.severity}] {d.field_name}: {d.message} (Remediation: {d.remediation})"
            for d in self.diagnostics
        ]
        return (
            f"Preflight FAILED ({len(self.missing_fields)} missing fields):\n"
            + "\n".join(diag_lines)
        )


class UnifiedPreflightCascadeError(HITLRequiredError, RuntimeError):
    """Batched preflight error reporting all missing U0/L1/C0.6 requirements in one pass."""

    def __init__(
        self,
        report: PreflightBatchReport,
        options: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.report = report
        opts = list(options or [])
        if not opts and report.diagnostics:
            opts = [
                {
                    "label": f"Resolve {d.field_name}",
                    "description": f"{d.message} (Remediation: {d.remediation})",
                    "field_name": d.field_name,
                }
                for d in report.diagnostics
            ]
        if report.speaker_options and not options:
            opts.extend(report.speaker_options)
        super().__init__(
            message=report.summary(),
            hitl_type="preflight_cascade",
            missing_fields=list(report.missing_fields),
            options=opts,
            prompt=report.summary(),
            review_contexts=list(report.review_contexts),
        )


__all__ = [
    "PreflightDiagnostic",
    "PreflightBatchReport",
    "UnifiedPreflightCascadeError",
]

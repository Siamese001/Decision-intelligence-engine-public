"""Reject checkpoint reuse when observed calls disagree with the bound configuration."""
from __future__ import annotations

from typing import Any


def execution_reuse_blockers(binding: dict[str, Any], evaluation: Any) -> list[str]:
    components = binding["components"]
    report = evaluation.judge_report
    blockers = []
    for kind, observed in (("extraction", report.get("extraction_execution")),
                           ("judge", report.get("execution"))):
        expected = components[kind]
        if expected.get("identity_scope") == "deterministic_fixture_code":
            continue
        label = kind.upper()
        if not isinstance(observed, dict) or observed.get("status") != "COMPLETED":
            blockers.append(label + "_EXECUTION_UNBOUND")
            continue
        if observed.get("effective_model") != expected.get("declared_model"):
            blockers.append(label + "_OBSERVED_MODEL_MISMATCH")
        effort = (expected.get("settings", {}).get("reasoning_effort") if kind == "extraction"
                  else expected.get("reasoning_effort"))
        if observed.get("effective_effort") != effort:
            blockers.append(label + "_OBSERVED_EFFORT_MISMATCH")
        revision = observed.get("observed_revision")
        if revision is not None and revision != expected.get("declared_revision"):
            blockers.append(label + "_OBSERVED_REVISION_MISMATCH")
    return blockers

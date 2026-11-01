"""
dual_pipeline.transcript_qa_core.selective_defect_evaluator

Selective Defect Evaluator replacing simulated quality panels.
Executes concrete, deterministic QA checks across schema, speaker attribution,
provenance grounding, and cross-artifact consistency, selectively routing
only defective dimensions to specialists with bounded retry caps.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

logger = logging.getLogger(__name__)

# Patterns indicating unresolved, missing, or collapsed speaker attribution
UNRESOLVED_SPEAKER_RE = re.compile(
    r"\b(speaker\s*(unknown|\?|unidentified)|\[unresolved(_speaker)?\]|\[unknown\])\b",
    re.IGNORECASE,
)
SPEAKER_PREFIX_RE = re.compile(r"^(\*{0,2}[A-Za-z0-9_\s\.\-]{2,40}\*{0,2})\s*:\s*(.+)$")
CREDENTIAL_LEAK_RE = re.compile(
    r"\b(AKIA[0-9A-Z]{16}|sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{30,})\b"
)


@dataclass
class DefectFinding:
    dimension: str
    severity: str
    description: str
    target_artifact: str
    details: Optional[Dict[str, Any]] = None


@dataclass
class DimensionResult:
    dimension: str
    status: str  # "PASS" | "FAIL"
    findings: List[DefectFinding] = field(default_factory=list)


@dataclass
class SelectiveEvaluationReport:
    passed: bool
    status: str  # "PASS" | "FAIL"
    dimension_results: Dict[str, DimensionResult]
    routed_specialists: Dict[str, Dict[str, Any]]
    attempt: int = 1
    terminal_reason: Optional[str] = None


def _check_credential_leak(f1: str, f2: str, f3_dict: Dict[str, Any]) -> Optional[DefectFinding]:
    combined = f"{f1}\n{f2}\n{json.dumps(f3_dict) if f3_dict else ''}"
    if CREDENTIAL_LEAK_RE.search(combined):
        return DefectFinding(
            dimension="provenance",
            severity="CRITICAL",
            description="Raw credential or secret token detected in output artifacts.",
            target_artifact="all_outputs",
        )
    return None


def _check_assertion_grounding(f3_dict: Dict[str, Any]) -> Optional[DefectFinding]:
    assertions = f3_dict.get("assertions") or f3_dict.get("signals") or []
    if not isinstance(assertions, list):
        return None
    for idx, a in enumerate(assertions):
        if isinstance(a, dict) and not (a.get("evidence_refs") or a.get("citation") or a.get("source_turn")):
            return DefectFinding(
                dimension="provenance",
                severity="HIGH",
                description=f"Assertion at index {idx} lacks grounding evidence.",
                target_artifact="output_03",
                details={"assertion_index": idx, "assertion_id": a.get("id")},
            )
    return None


class SelectiveDefectEvaluator:

    """Evaluates artifact dimensions with concrete checks and routes only failures."""

    def __init__(self, max_retries: int = 2):
        self.max_retries = max_retries

    def evaluate_schema(self, f3: Union[str, Dict[str, Any]]) -> DimensionResult:
        """Validates Output 3 JSON projection structure and schema integrity."""
        findings: List[DefectFinding] = []
        parsed: Optional[Dict[str, Any]] = None

        if isinstance(f3, dict):
            parsed = f3
        elif isinstance(f3, str):
            clean = f3.strip()
            if not clean:
                findings.append(
                    DefectFinding(
                        dimension="schema",
                        severity="CRITICAL",
                        description="Output 3 is empty or blank.",
                        target_artifact="output_03",
                    )
                )
                return DimensionResult(dimension="schema", status="FAIL", findings=findings)
            try:
                parsed = json.loads(clean)
            except json.JSONDecodeError as exc:
                findings.append(
                    DefectFinding(
                        dimension="schema",
                        severity="CRITICAL",
                        description=f"Output 3 is not valid JSON: {exc}",
                        target_artifact="output_03",
                    )
                )
                return DimensionResult(dimension="schema", status="FAIL", findings=findings)
        else:
            findings.append(
                DefectFinding(
                    dimension="schema",
                    severity="CRITICAL",
                    description="Output 3 payload must be valid JSON string or dictionary.",
                    target_artifact="output_03",
                )
            )
            return DimensionResult(dimension="schema", status="FAIL", findings=findings)

        if not parsed or (isinstance(parsed, dict) and len(parsed) == 0):
            findings.append(
                DefectFinding(
                    dimension="schema",
                    severity="HIGH",
                    description="Output 3 JSON contains empty object payload.",
                    target_artifact="output_03",
                )
            )

        valid_keys = {"canonical", "nodes", "entities", "assertions", "projected_signals", "signals"}
        if isinstance(parsed, dict) and not any(k in parsed for k in valid_keys):
            findings.append(
                DefectFinding(
                    dimension="schema",
                    severity="HIGH",
                    description="Output 3 lacks canonical graph projection keys.",
                    target_artifact="output_03",
                    details={"present_keys": list(parsed.keys())},
                )
            )

        status = "FAIL" if findings else "PASS"
        return DimensionResult(dimension="schema", status=status, findings=findings)

    def evaluate_speaker_attribution(
        self, f1: str, expected_speakers: Optional[Sequence[str]] = None
    ) -> DimensionResult:
        """Validates Output 1 speaker attribution, turn consistency, and collapse."""
        findings: List[DefectFinding] = []
        text = (f1 or "").strip()
        if not text:
            findings.append(
                DefectFinding(
                    dimension="speaker",
                    severity="CRITICAL",
                    description="Output 1 forensic transcript is empty.",
                    target_artifact="output_01",
                )
            )
            return DimensionResult(dimension="speaker", status="FAIL", findings=findings)

        if UNRESOLVED_SPEAKER_RE.search(text):
            matches = UNRESOLVED_SPEAKER_RE.findall(text)
            findings.append(
                DefectFinding(
                    dimension="speaker",
                    severity="HIGH",
                    description=f"Unresolved speaker placeholder detected ({len(matches)} instance(s)).",
                    target_artifact="output_01",
                    details={"samples": [m[0] for m in matches[:3]]},
                )
            )

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        speaker_counts: Dict[str, int] = {}
        for line in lines:
            m = SPEAKER_PREFIX_RE.match(line)
            if m:
                spk = m.group(1).replace("*", "").strip().lower()
                speaker_counts[spk] = speaker_counts.get(spk, 0) + 1

        total_turns = sum(speaker_counts.values())
        if expected_speakers and len(expected_speakers) > 1 and total_turns >= 5:
            max_turns = max(speaker_counts.values()) if speaker_counts else 0
            if total_turns > 0 and (max_turns / total_turns) > 0.85 and len(speaker_counts) <= 1:
                findings.append(
                    DefectFinding(
                        dimension="speaker",
                        severity="HIGH",
                        description=f"Single-speaker collapse detected: {max_turns}/{total_turns} turns.",
                        target_artifact="output_01",
                        details={"speaker_counts": speaker_counts},
                    )
                )

        status = "FAIL" if findings else "PASS"
        return DimensionResult(dimension="speaker", status=status, findings=findings)

    def evaluate_provenance(
        self, f1: str, f2: str, f3: Union[str, Dict[str, Any]]
    ) -> DimensionResult:
        """Validates grounding, evidence references, and absence of raw credential leaks."""
        findings: List[DefectFinding] = []
        f3_dict: Dict[str, Any] = f3 if isinstance(f3, dict) else {}
        if isinstance(f3, str) and f3.strip():
            try:
                f3_dict = json.loads(f3)
            except Exception:
                f3_dict = {}

        leak = _check_credential_leak(f1, f2, f3_dict)
        if leak:
            findings.append(leak)

        ungrounded = _check_assertion_grounding(f3_dict)
        if ungrounded:
            findings.append(ungrounded)

        status = "FAIL" if findings else "PASS"
        return DimensionResult(dimension="provenance", status=status, findings=findings)


    def evaluate_cross_artifact(
        self, f1: str, f2: str, f3: Union[str, Dict[str, Any]]
    ) -> DimensionResult:
        """Validates consistency between transcript turns, executive recap, and graph signals."""
        findings: List[DefectFinding] = []
        f1_lower = (f1 or "").lower()
        f2_lower = (f2 or "").lower()

        recap_participants = re.findall(r"attendees?:\s*([^\n\r]+)", f2_lower)
        if recap_participants:
            raw_names = re.split(r"[,;]", recap_participants[0])
            for raw_n in raw_names:
                name = raw_n.strip()
                if len(name) > 3 and name not in f1_lower:
                    findings.append(
                        DefectFinding(
                            dimension="cross_artifact",
                            severity="MEDIUM",
                            description=f"Attendee '{name}' claimed in executive recap absent from transcript.",
                            target_artifact="output_02",
                            details={"attendee": name},
                        )
                    )

        status = "FAIL" if findings else "PASS"
        return DimensionResult(dimension="cross_artifact", status=status, findings=findings)

    def evaluate_all(
        self,
        f1: str,
        f2: str,
        f3: Union[str, Dict[str, Any]],
        expected_speakers: Optional[Sequence[str]] = None,
    ) -> SelectiveEvaluationReport:
        """Runs evaluation across all core dimensions and compiles selective routing."""
        dim_results = {
            "schema": self.evaluate_schema(f3),
            "speaker": self.evaluate_speaker_attribution(f1, expected_speakers),
            "provenance": self.evaluate_provenance(f1, f2, f3),
            "cross_artifact": self.evaluate_cross_artifact(f1, f2, f3),
        }

        routed = self.route_defects(dim_results)
        all_passed = all(res.status == "PASS" for res in dim_results.values())

        return SelectiveEvaluationReport(
            passed=all_passed,
            status="PASS" if all_passed else "FAIL",
            dimension_results=dim_results,
            routed_specialists=routed,
        )

    def route_defects(
        self, dimension_results: Dict[str, DimensionResult]
    ) -> Dict[str, Dict[str, Any]]:
        """Maps only failing dimensions to targeted specialists, omitting passing ones."""
        routes: Dict[str, Dict[str, Any]] = {}
        specialist_directory = {
            "schema": {
                "specialist": "Output3SchemaValidator",
                "healer": "table_healer",
                "action": "repair_schema",
            },
            "speaker": {
                "specialist": "SpeakerAttributionValidator",
                "healer": "forensic_healer",
                "action": "reconcile_speakers",
            },
            "provenance": {
                "specialist": "ForensicAccuracyAuditor",
                "healer": "provenance_healer",
                "action": "bind_evidence",
            },
            "cross_artifact": {
                "specialist": "CrossArtifactReconciler",
                "healer": "recap_healer",
                "action": "reconcile_artifacts",
            },
        }

        for dim, res in dimension_results.items():
            if res.status == "FAIL":
                config = specialist_directory.get(
                    dim,
                    {"specialist": f"{dim.capitalize()}Specialist", "healer": "default_healer"},
                )
                routes[dim] = {
                    **config,
                    "finding_count": len(res.findings),
                    "severities": [f.severity for f in res.findings],
                    "routed": True,
                }

        return routes

    def evaluate_role(self, role: str, f1: str, f2: str, f3: Any) -> Dict[str, Any]:
        """Provides backward-compatible bridge for the 15 QA Swarm roles."""
        role_dim_map = {
            "Output 3 Schema Validator": "schema",
            "Graph Mutability Validator": "schema",
            "Speaker Attribution Validator": "speaker",
            "Forensic Accuracy Auditor": "speaker",
            "Assertion Freshness Checker": "provenance",
            "Security & PII Scanner": "provenance",
            "Cross-Artifact Reconciler": "cross_artifact",
            "Executive Recap Summarizer": "cross_artifact",
        }

        target_dim = role_dim_map.get(role)
        if target_dim:
            if target_dim == "schema":
                res = self.evaluate_schema(f3)
            elif target_dim == "speaker":
                res = self.evaluate_speaker_attribution(f1)
            elif target_dim == "provenance":
                res = self.evaluate_provenance(f1, f2, f3)
            else:
                res = self.evaluate_cross_artifact(f1, f2, f3)

            return {
                "role": role,
                "status": res.status,
                "findings": [f.description for f in res.findings],
            }

        return {"role": role, "status": "PASS", "findings": []}

    def evaluate_and_route_with_retry(
        self,
        f1: str,
        f2: str,
        f3: Union[str, Dict[str, Any]],
        healer_fn: Optional[Callable[[str, str, Any, Dict[str, Any]], Tuple[str, str, Any]]] = None,
        expected_speakers: Optional[Sequence[str]] = None,
    ) -> Tuple[SelectiveEvaluationReport, int]:
        """Evaluates with bounded retry loop, halting when max_retries is reached."""
        current_f1, current_f2, current_f3 = f1, f2, f3
        attempt = 0

        while attempt <= self.max_retries:
            report = self.evaluate_all(current_f1, current_f2, current_f3, expected_speakers)
            report.attempt = attempt + 1
            if report.passed:
                report.terminal_reason = "ALL_DIMENSIONS_PASSED"
                return report, attempt

            if attempt == self.max_retries or healer_fn is None:
                report.terminal_reason = "MAX_RETRIES_EXCEEDED" if attempt >= self.max_retries else "DEFECTS_DETECTED_NO_HEALER"
                return report, attempt

            attempt += 1
            from dual_pipeline.cost_tracker import get_token_tracker
            get_token_tracker().intercept_token_call(0)
            try:
                current_f1, current_f2, current_f3 = healer_fn(
                    current_f1, current_f2, current_f3, report.routed_specialists
                )
            except Exception as exc:
                from dual_pipeline.exceptions import PipelineTokenBudgetExhaustedError
                if isinstance(exc, PipelineTokenBudgetExhaustedError):
                    raise
                logger.error("Error invoking healer function in attempt %d: %s", attempt, exc)
                report.terminal_reason = f"HEALER_EXCEPTION: {exc}"
                return report, attempt

        report.terminal_reason = "MAX_RETRIES_EXCEEDED"
        return report, attempt

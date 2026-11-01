"""
Structured diagnostics collector and serializer for judge and QA failure analysis.
Records itemized failure records to reports/judge_failure_diagnostics.json.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class FailureType(str, Enum):
    CONTENT = "CONTENT"
    GROUNDING = "GROUNDING"
    EVALUATOR = "EVALUATOR"
    TIMEOUT = "TIMEOUT"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    ATTRIBUTION = "ATTRIBUTION"


@dataclass
class JudgeFailureRecord:
    output: str
    attempt: int
    judge: str
    gate: Optional[str]
    criterion: str
    status: str
    score: Optional[Union[int, float]]
    threshold: Optional[Union[int, float]]
    failure_type: str
    expected: str
    observed: str
    evidence_ids: Optional[List[str]]
    root_cause: str
    remediation: str
    retry_scope: Optional[str]
    llm_required: bool
    retry_count: int
    elapsed_ms: Optional[Union[int, float]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "output": self.output,
            "attempt": self.attempt,
            "judge": self.judge,
            "gate": self.gate,
            "criterion": self.criterion,
            "status": self.status,
            "score": self.score,
            "threshold": self.threshold,
            "failure_type": self.failure_type,
            "expected": self.expected,
            "observed": self.observed,
            "evidence_ids": self.evidence_ids,
            "root_cause": self.root_cause,
            "remediation": self.remediation,
            "retry_scope": self.retry_scope,
            "llm_required": self.llm_required,
            "retry_count": self.retry_count,
            "elapsed_ms": self.elapsed_ms,
        }


def classify_qa_failure(gate_name: str, message: str) -> FailureType:
    msg_lower = f"{gate_name} {message}".lower()
    if any(k in msg_lower for k in ("collapse", "single_speaker", "contradiction", "unconsumed", "partner_organization", "generic_speaker", "unattributed", "attribution")):
        return FailureType.ATTRIBUTION
    if any(k in msg_lower for k in ("timeout", "deadline", "timed out")):
        return FailureType.TIMEOUT
    if any(k in msg_lower for k in ("rate limit", "429", "503", "socket", "connection", "locked")):
        return FailureType.INFRASTRUCTURE
    if any(k in msg_lower for k in ("schema_version", "syntax_valid", "jsondecode", "evaluator")):
        return FailureType.EVALUATOR
    if any(k in msg_lower for k in ("evidence", "citation", "grounding", "spliced", "locator", "topical")):
        return FailureType.GROUNDING
    return FailureType.CONTENT


def suggest_remediation(gate_name: str, message: str, failure_type: FailureType) -> tuple[str, bool]:
    msg_lower = f"{gate_name} {message}".lower()
    if failure_type == FailureType.ATTRIBUTION:
        return (
            "Upstream attribution recovery retry required (invalidate seeded ASR cache, re-run stereo/pyannote sub-diarization, and re-apply turn hints); blind LLM text retry is strictly prohibited.",
            False,
        )
    if failure_type == FailureType.GROUNDING:
        if "topical" in msg_lower or "overlap" in msg_lower:
            return "Prune invalid citation or align with topic", False
        if "spliced" in msg_lower or "locator" in msg_lower:
            return "Align timestamp locators with authentic dialogue turns", False
        return "Prune ungrounded citations and verify transcript evidence", False
    if failure_type == FailureType.TIMEOUT:
        return "Increase stage deadline or route to faster model tier", False
    if failure_type == FailureType.INFRASTRUCTURE:
        return "Retry request with exponential backoff on active provider", False
    if failure_type == FailureType.EVALUATOR:
        return "Correct payload schema or JSON syntax formatting", False
    return "Regenerate artifact with grounded context and depth requirements", True


class JudgeFailureDiagnosticsCollector:
    """Collects judge failures, retry history, and successful results."""

    def __init__(self, root_dir: Optional[Path] = None):
        self.root_dir = root_dir or Path.cwd()
        self.failures: List[JudgeFailureRecord] = []
        self.successful_results: Dict[str, Any] = {}
        self.retry_history: List[Dict[str, Any]] = []

    def load_existing(self, path: Path) -> None:
        """Load prior failures, retry history, and successful results from existing report."""
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            for f_dict in data.get("failures", []):
                try:
                    rec = JudgeFailureRecord(**f_dict)
                    if not any(
                        r.output == rec.output and r.attempt == rec.attempt and r.criterion == rec.criterion and r.observed == rec.observed
                        for r in self.failures
                    ):
                        self.failures.append(rec)
                except Exception:
                    pass
            for h in data.get("retry_history", []):
                att = h.get("attempt")
                if att is not None and not any(r.get("attempt") == att for r in self.retry_history):
                    self.retry_history.append(h)
            for k, s in data.get("successful_results", {}).items():
                self.successful_results.setdefault(k, s)
        except Exception:
            pass

    def record_failure(self, record: JudgeFailureRecord) -> None:
        self.failures.append(record)

    def record_success(
        self,
        output: str,
        judge: str,
        score: Optional[Union[int, float]] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        key = f"output_{output}_{judge.lower()}"
        self.successful_results[key] = {
            "output": output,
            "judge": judge,
            "status": "PASS",
            "score": score,
            "details": details or {},
        }

    def record_attempt(
        self,
        attempt: int,
        outputs: Dict[str, Any],
        elapsed_ms: Optional[float] = None,
    ) -> None:
        self.retry_history.append({
            "attempt": attempt,
            "outputs": outputs,
            "elapsed_ms": elapsed_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def extract_from_qa_report(
        self,
        qa_report: Dict[str, Any],
        attempt: int = 1,
        retry_count: int = 0,
        elapsed_ms: Optional[float] = None,
    ) -> int:
        count = 0
        gate_map = {
            "output_01_forensic_transcript": ("01", "ForensicTranscriptGate"),
            "output_02_executive_recap": ("02", "ExecutiveRecapGate"),
            "output_03_canonical_signal_projection": ("03", "GraphExtractionGate"),
        }
        for section_key, (out_num, judge_name) in gate_map.items():
            sec = qa_report.get(section_key, {})
            if not isinstance(sec, dict):
                continue
            gates = sec.get("gates", {})
            for g_name, g_val in gates.items():
                if isinstance(g_val, dict) and g_val.get("passed") is False:
                    fail_recs = self._build_qa_gate_failures(
                        out_num, judge_name, g_name, g_val, attempt, retry_count, elapsed_ms
                    )
                    for rec in fail_recs:
                        self.record_failure(rec)
                        count += 1
        return count

    def _build_qa_gate_failures(
        self,
        out_num: str,
        judge_name: str,
        gate_name: str,
        gate_val: Dict[str, Any],
        attempt: int,
        retry_count: int,
        elapsed_ms: Optional[float],
    ) -> List[JudgeFailureRecord]:
        failures_list = gate_val.get("failures") or [f"Gate check failed: {gate_name}"]
        res = []
        gate_num = self._extract_gate_number(gate_name)
        for msg in failures_list:
            ftype = classify_qa_failure(gate_name, str(msg))
            remediation, llm_req = suggest_remediation(gate_name, str(msg), ftype)
            ev_ids = self._extract_evidence_ids(str(msg))
            rec = JudgeFailureRecord(
                output=out_num,
                attempt=attempt,
                judge=judge_name,
                gate=gate_num,
                criterion=gate_name,
                status="FAIL",
                score=None,
                threshold=None,
                failure_type=ftype.value,
                expected="Pass all gate validation rules",
                observed=str(msg),
                evidence_ids=ev_ids if ev_ids else None,
                root_cause=f"Deterministic QA failure in {gate_name}: {msg}",
                remediation=remediation,
                retry_scope="upstream_attribution" if ftype == FailureType.ATTRIBUTION else f"output_{int(out_num)}",
                llm_required=llm_req,
                retry_count=retry_count,
                elapsed_ms=elapsed_ms,
            )
            res.append(rec)
        return res

    def extract_from_judge_report(
        self,
        judge_report: Dict[str, Any],
        attempt: int = 1,
        retry_count: int = 0,
        elapsed_ms: Optional[float] = None,
    ) -> int:
        count = 0
        from dual_pipeline.release_thresholds import (
            OUTPUT1_PRODUCTION_PASS_THRESHOLD,
            OUTPUT2_PRODUCTION_PASS_THRESHOLD,
            OUTPUT3_PRODUCTION_PASS_THRESHOLD,
        )
        judge_keys = {
            "output_1_evaluation": ("01", "ForensicTranscriptJudge", OUTPUT1_PRODUCTION_PASS_THRESHOLD),
            "output_2_evaluation": ("02", "ExecutiveRecapJudge", OUTPUT2_PRODUCTION_PASS_THRESHOLD),
            "output_3_evaluation": ("03", "GraphExtractionJudge", OUTPUT3_PRODUCTION_PASS_THRESHOLD),
        }
        for eval_key, (out_num, judge_name, default_thresh) in judge_keys.items():
            eval_data = judge_report.get(eval_key, {})
            if not isinstance(eval_data, dict):
                continue
            if eval_data.get("status") in ("SKIPPED", "NOT_APPLICABLE") or eval_data.get("verdict") in ("SKIPPED", "NOT_APPLICABLE"):
                continue
            verdict = eval_data.get("verdict", "PASS")
            score = eval_data.get("composite_score")
            thresh = eval_data.get("release_threshold", default_thresh)
            if (verdict == "PASS" and (score is None or score >= thresh)) or (
                verdict == "PASS_AUTHORIZED_EXCEPTION" and (score is None or score >= 85)
            ):
                self.record_success(out_num, judge_name, score, eval_data)
                continue

            rationale = eval_data.get("forensic_rationale", "Score below release threshold")
            remediation_diff = eval_data.get("actionable_remediation_diff", "")
            ftype = FailureType.GROUNDING if "grounding" in rationale.lower() else FailureType.CONTENT
            remediation = remediation_diff if remediation_diff else f"Address shortcomings noted: {rationale}"

            rec = JudgeFailureRecord(
                output=out_num,
                attempt=attempt,
                judge=judge_name,
                gate=None,
                criterion="LLM Judge composite quality and rubric threshold",
                status="FAIL",
                score=score,
                threshold=thresh,
                failure_type=ftype.value,
                expected=f"Composite score >= {thresh} and all dimension floors met",
                observed=f"Composite score={score}, verdict={verdict}. {rationale}",
                evidence_ids=None,
                root_cause=rationale,
                remediation=remediation,
                retry_scope=f"output_{int(out_num)}",
                llm_required=True,
                retry_count=retry_count,
                elapsed_ms=elapsed_ms,
            )
            self.record_failure(rec)
            count += 1
        return count

    def build_summary(self) -> str:
        if not self.failures:
            return "No judge or QA failures recorded. All evaluation criteria satisfied."
        lines = [
            f"Judge Failure Diagnostics Summary: {len(self.failures)} failure(s) identified across attempts."
        ]
        for idx, f in enumerate(self.failures, 1):
            gate_str = f" [Gate {f.gate}]" if f.gate else ""
            lines.append(
                f"{idx}. Output {f.output} ({f.judge}){gate_str} - Type: {f.failure_type}\n"
                f"   Root Cause: {f.root_cause}\n"
                f"   Corrective Action: {f.remediation}\n"
                f"   Retry Scope: {f.retry_scope} (LLM required: {f.llm_required})"
            )
        return "\n".join(lines)

    def to_diagnostics_payload(self) -> Dict[str, Any]:
        attempts_from_failures = {f.attempt for f in self.failures}
        attempts_from_history = {r.get("attempt") for r in self.retry_history if r.get("attempt") is not None}
        attempts_seen = attempts_from_failures | attempts_from_history
        max_attempt_num = max(attempts_seen, default=0)
        total_attempts = max(max_attempt_num, len(self.retry_history), 1 if (self.failures or self.successful_results) else 0)
        return {
            "metadata": {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "total_failures": len(self.failures),
                "total_successes": len(self.successful_results),
                "total_attempts": total_attempts,
            },
            "summary": self.build_summary(),
            "failures": [f.to_dict() for f in self.failures],
            "retry_history": self.retry_history,
            "successful_results": self.successful_results,
        }

    def save_report(self, dest_path: Optional[Path] = None) -> Path:
        out_path = dest_path or (self.root_dir / "reports" / "judge_failure_diagnostics.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_diagnostics_payload()
        with open(out_path, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, indent=2)
        return out_path

    @staticmethod
    def _extract_gate_number(name: str) -> Optional[str]:
        import re
        m = re.search(r"Gate_?(\d+)_?(\d+)?", name, re.IGNORECASE)
        if m:
            major, minor = m.group(1), m.group(2)
            return f"{major}.{minor}" if minor else major
        return None

    @staticmethod
    def _extract_evidence_ids(text: str) -> List[str]:
        import re
        return re.findall(r"\b(ev-[a-zA-Z0-9_\-]+)\b", text)

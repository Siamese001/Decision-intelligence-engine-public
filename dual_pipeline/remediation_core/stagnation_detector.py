"""
dual_pipeline/remediation_core/stagnation_detector.py

Provides deterministic failure signature calculation and stagnation diagnostics
to detect non-converging remediation loops and prevent infinite retries.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Set

from dual_pipeline.core import digest
from dual_pipeline.storage.healing_decision_store import record_healing_decision


def record_regression_healing_event(key: str, attempt: int, restored: Optional[str], hwm_score: int) -> None:
    """Persist monotonic regression rollback to the governed healing decision store."""
    record_healing_decision({
        "run_id": f"run-state-machine-att-{attempt}",
        "subsystem": "remediation_state_machine",
        "component": f"SelectiveRemediationStateMachine:{key}",
        "scope": key,
        "repair_class": "MONOTONIC_REGRESSION_ROLLBACK",
        "action_taken": f"Restored high-water mark candidate for {key} on attempt {attempt}",
        "outcome_status": "HEALED",
        "before_digest": digest(f"regressed:{key}:{attempt}"),
        "after_digest": digest(restored or ""),
        "confidence_top": 0.96,
        "confidence_second": 0.04,
        "ambiguity_margin": 0.92,
        "hitl_escalated": False,
        "invariants_preserved": True,
        "defect_reasons": [f"Monotonic score regression on {key} at attempt {attempt}"],
        "audit_trail": [f"Restored high-water mark score={hwm_score}"],
    })


def extract_qa_entry(stg: str, qa_report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract QA report entry for a given stage."""
    if not isinstance(qa_report, dict):
        return None
    qa_entry = qa_report.get(stg) or qa_report.get(f"output_0{stg[-1]}_" if stg.startswith("output_") else stg)
    if qa_entry:
        return qa_entry
    for k, v in qa_report.items():
        if stg in k and isinstance(v, dict):
            return v
    return None


def extract_stage_failure_details(
    stg: str,
    qa_report: Dict[str, Any],
    judge_report: Dict[str, Any],
) -> Dict[str, Any]:
    """Extract failure attributes across QA and Judge reports for deterministic fingerprinting."""
    qa_entry = extract_qa_entry(stg, qa_report)
    failures = sorted(str(f) for f in qa_entry.get("failures", [])) if isinstance(qa_entry, dict) else []
    judge_entry = judge_report.get(f"{stg}_evaluation", {}) if isinstance(judge_report, dict) else {}
    j_verdict = judge_entry.get("verdict") if isinstance(judge_entry, dict) else None
    j_score = judge_entry.get("composite_score") if isinstance(judge_entry, dict) else None
    return {
        "qa_failures": failures,
        "judge_verdict": j_verdict,
        "judge_score": j_score,
    }


def compute_failure_signature(
    failed_stages: Set[str],
    qa_report: Dict[str, Any],
    judge_report: Dict[str, Any],
    final_decision: Dict[str, Any],
) -> str:
    """Computes a deterministic SHA-256 signature of failure modes to detect remediation stagnation."""
    sig_data: Dict[str, Any] = {
        "failed_stages": sorted(list(failed_stages)),
        "decision_status": final_decision.get("status") if isinstance(final_decision, dict) else None,
        "stage_details": {
            stg: extract_stage_failure_details(stg, qa_report, judge_report)
            for stg in sorted(list(failed_stages))
        },
    }
    encoded = json.dumps(sig_data, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_stagnant_diagnosis(
    current_attempt: int,
    signature: str,
    failed_stages: Set[str],
) -> Dict[str, Any]:
    """Constructs structured diagnostic payload for stagnant remediation loop termination."""
    sorted_stages = sorted(list(failed_stages))
    diag = {
        "stagnant_attempt": current_attempt,
        "failure_signature": signature,
        "failed_stages": sorted_stages,
        "diagnosis": (
            f"Non-converging remediation loop detected: attempt {current_attempt} "
            f"produced identical failure signature as a prior attempt. Halting retries early."
        ),
        "actionable_critique": [
            f"{stg}: persistent failure across attempts without improvement."
            for stg in sorted_stages
        ],
    }
    record_healing_decision({
        "run_id": f"run-state-machine-att-{current_attempt}",
        "subsystem": "remediation_state_machine",
        "component": "SelectiveRemediationStateMachine:stagnation_guard",
        "scope": ",".join(sorted_stages) or "all_stages",
        "repair_class": "STAGNATION_EARLY_TERMINATION",
        "action_taken": f"Terminated stagnant remediation loop on signature {signature[:12]}",
        "outcome_status": "UNREPAIRABLE",
        "before_digest": signature,
        "after_digest": signature,
        "confidence_top": 0.40,
        "confidence_second": 0.60,
        "ambiguity_margin": 0.0,
        "hitl_escalated": True,
        "invariants_preserved": True,
        "defect_reasons": [f"Identical failure signature {signature[:12]} repeated across attempts"],
        "audit_trail": [json.dumps(diag, sort_keys=True)],
    })
    return diag

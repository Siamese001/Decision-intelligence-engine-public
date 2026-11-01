"""Transcript Pipeline L1 Post-L2 Review.

Provides deterministic verification and semantic sufficiency review for
Artifacts 01 (Forensic Transcript), 02 (Executive Recap), and 03 (Signal Projection)
immediately following L2 stage execution and before exit gate evaluation.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Tuple

from dual_pipeline.l1_review_contracts import (
    DeterministicCheckItem,
    DeterministicReasonCode,
    L1PostL2ReviewContract,
    L1ReviewVerdict,
)


def _check_output_1(f1_text: str) -> DeterministicCheckItem:
    if not isinstance(f1_text, str) or len(f1_text.strip()) < 30:
        return DeterministicCheckItem(
            name="output_01_forensic_presence",
            passed=False,
            details="Output 01 forensic transcript is empty or too short (< 30 chars)",
            reason_code=DeterministicReasonCode.ANSWER_MISSING.value,
        )
    return DeterministicCheckItem(
        name="output_01_forensic_presence",
        passed=True,
        details=f"Output 01 forensic transcript present ({len(f1_text)} chars)",
    )


def _check_output_2(f2_text: str) -> DeterministicCheckItem:
    if not isinstance(f2_text, str) or len(f2_text.strip()) < 30:
        return DeterministicCheckItem(
            name="output_02_recap_presence",
            passed=False,
            details="Output 02 executive recap is empty or too short (< 30 chars)",
            reason_code=DeterministicReasonCode.ANSWER_MISSING.value,
        )
    return DeterministicCheckItem(
        name="output_02_recap_presence",
        passed=True,
        details=f"Output 02 executive recap present ({len(f2_text)} chars)",
    )


def _parse_graph_payload(f3_data: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if isinstance(f3_data, Mapping):
        return dict(f3_data), None
    if isinstance(f3_data, str) and f3_data.strip():
        try:
            parsed = json.loads(f3_data)
            if isinstance(parsed, dict):
                return parsed, None
            return None, "Output 03 JSON root is not a mapping"
        except Exception as ex:
            return None, f"Output 03 JSON parsing error: {ex}"
    return None, "Output 03 signal projection is empty or missing"


def _check_output_3(f3_data: Any) -> Tuple[DeterministicCheckItem, Optional[Dict[str, Any]]]:
    parsed, err = _parse_graph_payload(f3_data)
    if err is not None:
        return (
            DeterministicCheckItem(
                name="output_03_graph_schema",
                passed=False,
                details=err,
                reason_code=DeterministicReasonCode.SCHEMA_INVALID.value,
            ),
            None,
        )
    return (
        DeterministicCheckItem(
            name="output_03_graph_schema",
            passed=True,
            details="Output 03 canonical signal projection is valid JSON mapping",
        ),
        parsed,
    )


def _evaluate_semantic_sufficiency(
    f1_text: str,
    f2_text: str,
    f3_dict: Optional[Dict[str, Any]],
    metadata: Mapping[str, Any],
) -> Tuple[bool, Dict[str, Any], Optional[Dict[str, Any]]]:
    missing_aspects: List[str] = []
    replan_directive: Dict[str, Any] = {}

    f2_lower = f2_text.lower()
    has_objective_or_recap = any(
        kw in f2_lower for kw in ("objective", "recap", "summary", "context", "meeting")
    )
    if not has_objective_or_recap:
        missing_aspects.append("output_2_missing_objective_or_recap")
        replan_directive["remediation_directive_recap"] = (
            "Ensure executive recap includes an explicit Meeting Objective or Summary section."
        )

    if f3_dict is not None:
        statements = f3_dict.get("statements", []) or f3_dict.get("evidence_index", [])
        mutations = f3_dict.get("graph_mutations", {})
        entities = (
            f3_dict.get("entities", [])
            or (mutations.get("entity_upserts", []) if isinstance(mutations, dict) else [])
        )
        if not statements and not entities and len(f1_text.strip()) > 100:
            missing_aspects.append("output_3_empty_signal_projection")
            replan_directive["remediation_directive_graph"] = (
                "Extract discrete canonical statements and entities from the forensic transcript."
            )

    is_sufficient = len(missing_aspects) == 0
    reasoning = {
        "sufficiency_score": 1.0 if is_sufficient else 0.5,
        "missing_aspects": missing_aspects,
        "rationale": "All 3 outputs satisfy structural and semantic baselines."
        if is_sufficient
        else f"Outputs deficient in: {missing_aspects}",
    }
    return is_sufficient, reasoning, (replan_directive if not is_sufficient else None)


class TranscriptL1PostExecutionReviewer:
    """L1 cognitive evaluator for RawTranscriptPipeline outputs."""

    def review_execution(
        self,
        *,
        artifacts: Mapping[str, Any],
        plan: Any,
        metadata: Mapping[str, Any],
        attempt: int = 1,
    ) -> L1PostL2ReviewContract:
        f1_text = str(artifacts.get("01_hitl_resolved_transcript.md", ""))
        f2_text = str(artifacts.get("02_executive_recap.md", ""))
        f3_data = artifacts.get("03_canonical_signal_projection.json", "")

        is_notes_mode = bool(
            metadata.get("notes_mode")
            or metadata.get("is_notes")
            or metadata.get("source_type") in ("copilot_notes", "meeting_notes", "notes")
            or metadata.get("outputs") == ["02", "03"]
        )

        if is_notes_mode:
            c1 = DeterministicCheckItem(
                name="output_01_forensic_presence",
                passed=True,
                details="Output 01 forensic transcript skipped (notes_mode)",
            )
        else:
            c1 = _check_output_1(f1_text)

        c2 = _check_output_2(f2_text)
        c3, parsed_f3 = _check_output_3(f3_data)
        deterministic_checks = [c1, c2, c3]
        deterministic_passed = all(c.passed for c in deterministic_checks)

        if not deterministic_passed:
            sem_passed = False
            sem_reasoning = {"rationale": "Deterministic output validation failed."}
            replan: Optional[Dict[str, Any]] = {
                "directive_type": "RETRY_STAGE_OUTPUTS",
                "attempt": attempt,
            }
            verdict = L1ReviewVerdict.INSUFFICIENT_REPLAN
        else:
            sem_passed, sem_reasoning, replan = _evaluate_semantic_sufficiency(
                f1_text=f1_text,
                f2_text=f2_text,
                f3_dict=parsed_f3,
                metadata=metadata,
            )
            verdict = (
                L1ReviewVerdict.SUFFICIENT
                if sem_passed
                else L1ReviewVerdict.INSUFFICIENT_REPLAN
            )

        run_id = str(metadata.get("run_id") or "run_transcript_l1")
        query_id = str(metadata.get("meeting_name") or "meeting_unknown")
        plan_id = getattr(plan, "plan_id", "plan_transcript_l1")

        return L1PostL2ReviewContract.create(
            run_id=run_id,
            query_id=query_id,
            plan_id=plan_id,
            sealed_artifact_id=f"sealed_transcript_a{attempt}",
            verdict=verdict,
            deterministic_passed=deterministic_passed,
            deterministic_checks=deterministic_checks,
            semantic_passed=sem_passed,
            semantic_reasoning=sem_reasoning,
            replan_directive=replan,
        )

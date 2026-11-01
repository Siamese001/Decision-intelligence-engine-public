"""
dual_pipeline/remediation_core/diff_injector.py

Targeted Judge Diff and Remediation Prompt Injector.
Extracts actionable diffs, rubric rationales, protected paths, forbidden mutations,
and QA failure points, injecting targeted instructions into retry prompts.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _extract_critique_envelope_directives(eval_data: Dict[str, Any]) -> List[str]:
    """Extract structured critique envelope components if emitted by dialectical judge."""
    directives: List[str] = []
    critique_env = eval_data.get("critique_diff_envelope") or {}
    if not isinstance(critique_env, dict) or not critique_env:
        return directives

    target = critique_env.get("target_path")
    defect = critique_env.get("observed_defect")
    delta = critique_env.get("required_delta")
    if target and defect:
        directives.append(f"CRITIQUE TARGET [{target}]:\nDefect: {defect}\nRequired Delta: {delta or 'None'}")

    prot = critique_env.get("protected_paths", [])
    if prot:
        directives.append("PROTECTED SECTIONS (MUST PRESERVE VERBATIM - DO NOT REGENERATE OR ALTER):\n- " + "\n- ".join(prot))

    forb = critique_env.get("forbidden_mutations", [])
    if forb:
        directives.append("FORBIDDEN MUTATIONS (STRICTLY PROHIBITED):\n- " + "\n- ".join(forb))

    return directives


def _extract_qa_and_recon_directives(
    artifact_key: str,
    judge_report: Dict[str, Any],
    qa_report: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Extract QA failures and cross-artifact reconciliation directives."""
    directives: List[str] = []
    if qa_report:
        qa_map = {
            "output_1": "output_01_forensic_transcript",
            "output_2": "output_02_executive_recap",
            "output_3": "output_03_canonical_signal_projection",
        }
        q_key = qa_map.get(artifact_key)
        if q_key:
            failures = qa_report.get(q_key, {}).get("failures", [])
            if failures:
                directives.append(f"QA GATE FAILURES TO REMEDIATE FOR {artifact_key.upper()} (QA Failures):\n- " + "\n- ".join(failures))

    if artifact_key == "output_3":
        recon_fails = judge_report.get("cross_artifact_reconciliation", {}).get("failures", [])
        if recon_fails:
            directives.append("CROSS-ARTIFACT RECONCILIATION FAILURES:\n- " + "\n- ".join(recon_fails))

    return directives


class RemediationDiffInjector:
    """Extracts and formats targeted remediation directives for failed artifacts."""

    @staticmethod
    def extract_directive(
        artifact_key: str,  # "output_1", "output_2", "output_3"
        judge_report: Dict[str, Any],
        qa_report: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Extracts targeted feedback, protected paths, and forbidden mutations for an artifact."""
        directives: List[str] = []

        eval_key = f"{artifact_key}_evaluation"
        eval_data = judge_report.get(eval_key, {})

        diff = eval_data.get("actionable_remediation_diff")
        if diff:
            directives.append(f"TARGETED REMEDIATION REQUIRED FOR {artifact_key.upper()}:\n{diff}")

        directives.extend(_extract_critique_envelope_directives(eval_data))

        rationale = eval_data.get("forensic_rationale")
        if rationale:
            directives.append(f"JUDGE CRITIQUE: {rationale}")

        directives.extend(_extract_qa_and_recon_directives(artifact_key, judge_report, qa_report))

        res = "\n\n".join(directives).strip()
        logger.debug(f"Extracted {len(directives)} directives for {artifact_key}.")
        return res


def collect_o3_qa_failures(qa_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect failing checks from Output 3 QA report."""
    o3_qa = qa_report.get("output_03_canonical_signal_projection", {})
    if o3_qa.get("passed", True):
        return []
    return [
        {"stage": "output_3", "check_name": c.get("name", "unknown"), "details": c.get("details", "")}
        for c in o3_qa.get("checks", [])
        if not c.get("passed", True)
    ]


def collect_judge_failures(judge_report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect dimension failures across all output judge reports."""
    failures: List[Dict[str, Any]] = []
    stages = (
        ("output_1", "output_1_evaluation"),
        ("output_2", "output_2_evaluation"),
        ("output_3", "output_3_evaluation"),
    )
    for stage_key, report_key in stages:
        stage_eval = judge_report.get(report_key, {})
        if stage_eval.get("verdict") != "FAIL":
            continue
        for dim, score in stage_eval.get("dimension_scores", {}).items():
            if isinstance(score, (int, float)) and score < 75:
                failures.append({"stage": stage_key, "dimension": dim, "score": score})
    return failures


def compute_differential_patch_scope(
    qa_report: Dict[str, Any],
    judge_report: Dict[str, Any],
) -> Dict[str, Any]:
    """Determines whether remediation can be achieved via targeted micro-patching."""
    failures = collect_o3_qa_failures(qa_report) + collect_judge_failures(judge_report)
    is_patchable = 0 < len(failures) <= 2
    return {
        "is_differential_patchable": is_patchable,
        "failed_count": len(failures),
        "failures": failures,
        "patch_strategy": "TARGETED_MICRO_PATCH" if is_patchable else "GLOBAL_RESYNTHESIS",
        "rationale": (
            f"Localized failures ({len(failures)} <= 2) eligible for differential patching."
            if is_patchable
            else f"Failure count ({len(failures)}) exceeds micro-patch threshold (<=2); requiring global re-synthesis."
        ),
    }

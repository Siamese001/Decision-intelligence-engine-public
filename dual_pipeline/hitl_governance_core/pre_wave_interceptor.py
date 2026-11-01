"""
dual_pipeline/hitl_governance_core/pre_wave_interceptor.py

Pre-Wave Decision Interceptor Core Logic (Layer 4/HITL Governance).
Inspects wave plans, decision ledgers, and decision registries prior to wave execution.
Enforces the Human-in-the-Loop (HITL) Interceptor Invariant:
  - Clearance Granted: All decisions for the wave are ratified/resolved in persistent store.
  - Halt / Intercept: One or more decisions bound to the wave are OPEN/UNRATIFIED.
  - Zero Decisions: Fails closed with exit code 3 unless explicitly authorized or attested.

Refactored under Wave 4 to delegate parsing, domain models, and exact-scope
verification to decision_clearance_core.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dual_pipeline.hitl_governance_core.decision_clearance_core import (
    EXIT_CODE_CLEARED,
    EXIT_CODE_NO_DECISIONS,
    EXIT_CODE_OPERATIONAL_ERROR,
    EXIT_CODE_OPERATOR_INPUT_REQUIRED,
    NoDecisionAttestation,
    PreWaveDecision,
    PreWaveGateResult,
    parse_decisions_from_markdown,
    parse_decisions_from_table,
    parse_no_decision_attestations,
    validate_decision_collection,
    verify_decision_ratification,
)
from dual_pipeline.hitl_governance_core.layman_clarity import (
    contains_jargon,
    format_layman_display_label,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

__all__ = [
    "PreWaveDecision",
    "PreWaveGateResult",
    "evaluate_pre_wave_gate",
    "format_ask_question_payload_for_decision",
    "format_ask_user_question_payload_for_decision",
    "parse_decisions_from_markdown",
    "run_self_test",
    "verify_decision_ratification",
]


def format_ask_user_question_payload_for_decision(decision: PreWaveDecision) -> Dict[str, Any]:
    """Formats a PreWaveDecision into an interactive ask_user_question tool payload."""
    formatted_options: List[str] = []

    def _sort_key(opt: Dict[str, Any]) -> Tuple[int, float]:
        is_rec = 1 if opt.get("recommended") or opt.get("is_recommended") else 0
        score = float(opt.get("confidence_score") or opt.get("score") or 0.0)
        return (is_rec, score)

    sorted_opts = sorted(decision.options, key=_sort_key, reverse=True)

    for i, opt in enumerate(sorted_opts):
        rec_prefix = "(Recommended) " if (i == 0 or opt.get("recommended")) else ""
        if "plain_english_summary" in opt and "impact_statement" in opt:
            lbl = format_layman_display_label(opt)
            if not lbl.startswith("(Recommended) ") and (i == 0 or opt.get("recommended")):
                lbl = f"(Recommended) {lbl}"
        else:
            desc = str(opt.get("label") or opt.get("text") or "Option")
            pct = int(round(float(opt.get("confidence_score") or 0.5) * 100))
            tier = str(opt.get("confidence_level") or opt.get("tier") or "MEDIUM")
            lbl = f"{rec_prefix}{desc} (Confidence: {tier} ({pct}%))"
        formatted_options.append(lbl)

    clean_q = re.sub(r"`[^`]+`", "", decision.question).strip()
    if not clean_q:
        clean_q = "Which implementation option should be authorized?"

    return {
        "questions": [
            {
                "question": clean_q,
                "options": formatted_options,
                "is_multi_select": False,
            }
        ]
    }


format_ask_question_payload_for_decision = format_ask_user_question_payload_for_decision


def _collect_candidate_plans(plan_path: Optional[Path]) -> List[Path]:
    if plan_path:
        return [plan_path]
    plans: List[Path] = []
    docs_plans = REPO_ROOT / "docs" / "plans"
    if docs_plans.is_dir():
        for p in sorted(docs_plans.glob("WAVE_PLAN_*.md")):
            plans.append(p)
    root_plan = REPO_ROOT / "implementation_plan.md"
    if root_plan.is_file() and root_plan not in plans:
        plans.append(root_plan)
    return plans


def _parse_decisions_file(df: Path, default_wave: int) -> Tuple[List[PreWaveDecision], List[str]]:
    decs: List[PreWaveDecision] = []
    errors: List[str] = []
    try:
        content = df.read_text(encoding="utf-8").strip()
        if not content:
            return decs, errors
        data = json.loads(content)
        if not isinstance(data, list):
            errors.append(f"Malformed decisions file {df}: expected list of objects, got {type(data).__name__}")
            return decs, errors
        for item in data:
            if not isinstance(item, dict) or "decision_id" not in item:
                errors.append(f"Malformed item in {df}: missing decision_id")
                continue
            decs.append(PreWaveDecision(
                decision_id=str(item["decision_id"]).strip(),
                wave=int(item.get("wave", default_wave)),
                status=str(item.get("status", "OPEN")),
                question=str(item.get("question", "Authorize decision?")),
                options=list(item.get("options", [])),
                category=str(item.get("category", "ARCHITECTURAL")),
                plan_id=item.get("plan_id"),
                target_files=list(item.get("target_files", [])),
                action_description=item.get("action_description"),
                metadata=dict(item.get("metadata", {})),
            ))
    except Exception as exc:
        errors.append(f"Error parsing decisions file {df}: {exc}")
    return decs, errors


def _parse_single_plan(
    plan: Path,
    diagnostics: List[str],
    attestations: List[NoDecisionAttestation],
) -> Tuple[List[PreWaveDecision], List[str]]:
    decs: List[PreWaveDecision] = []
    if not plan.is_file():
        return decs, []
    try:
        content = plan.read_text(encoding="utf-8")
        md_decs, md_errs = parse_decisions_from_markdown(content, source_path=plan)
        if md_errs:
            return [], md_errs
        decs.extend(md_decs)
        table_decs = parse_decisions_from_table(content, source_file=str(plan.name))
        for td in table_decs:
            decs.append(PreWaveDecision(
                decision_id=td.decision_id,
                wave=td.wave if td.wave is not None else 1,
                status="OPEN",
                question=td.question,
                options=td.options,
                action_description=td.title,
            ))
        attestations.extend(parse_no_decision_attestations(content, plan_path=str(plan)))
    except Exception as e:
        diagnostics.append(f"Warning: Failed parsing plan {plan}: {e}")
    return decs, []


def _collect_all_plan_decisions(
    candidate_plans: List[Path],
    diagnostics: List[str],
    attestations: List[NoDecisionAttestation],
) -> Tuple[List[PreWaveDecision], List[str]]:
    all_decs: List[PreWaveDecision] = []
    for plan in candidate_plans:
        p_decs, p_errs = _parse_single_plan(plan, diagnostics, attestations)
        if p_errs:
            return [], p_errs
        all_decs.extend(p_decs)
    return all_decs, []


def _classify_wave_decisions(
    wave_decisions: List[PreWaveDecision],
    wave: int,
    ledger_path: Optional[Path],
    diagnostics: List[str],
) -> Tuple[List[PreWaveDecision], List[PreWaveDecision]]:
    open_decs: List[PreWaveDecision] = []
    resolved_decs: List[PreWaveDecision] = []
    for d in wave_decisions:
        if d.is_open():
            is_ratified, reason = verify_decision_ratification(d, ledger_path=ledger_path)
            if is_ratified:
                resolved_decs.append(d)
                diagnostics.append(f"Decision {d.decision_id} (Wave {wave}) verified in persistent store: {reason}")
            else:
                open_decs.append(d)
                diagnostics.append(f"Decision {d.decision_id} (Wave {wave}) is OPEN: {reason}")
        else:
            resolved_decs.append(d)
            diagnostics.append(f"Decision {d.decision_id} (Wave {wave}) marked {d.status}.")
    return open_decs, resolved_decs


def evaluate_pre_wave_gate(
    wave: int,
    plan_path: Optional[Path] = None,
    decisions_file: Optional[Path] = None,
    ledger_path: Optional[Path] = None,
    extra_decisions: Optional[Sequence[PreWaveDecision]] = None,
    allow_zero_decisions: bool = False,
    attestation: Optional[NoDecisionAttestation] = None,
) -> PreWaveGateResult:
    """Evaluates whether Wave N may proceed or must halt."""
    diagnostics: List[str] = []
    discovered_decisions: List[PreWaveDecision] = []
    attestations: List[NoDecisionAttestation] = [attestation] if attestation else []

    # 1. Parse decisions JSON file if present
    if decisions_file and decisions_file.is_file():
        file_decs, file_errs = _parse_decisions_file(decisions_file, wave)
        if file_errs:
            diagnostics.extend(file_errs)
            return PreWaveGateResult(
                wave=wave, clearance_granted=False, exit_code=EXIT_CODE_OPERATIONAL_ERROR, diagnostics=diagnostics
            )
        discovered_decisions.extend(file_decs)

    # 2. Discover plans
    should_discover = (decisions_file is None and extra_decisions is None) or bool(plan_path)
    candidate_plans = _collect_candidate_plans(plan_path) if should_discover else []
    plan_decs, plan_errs = _collect_all_plan_decisions(candidate_plans, diagnostics, attestations)
    if plan_errs:
        diagnostics.extend(plan_errs)
        return PreWaveGateResult(
            wave=wave, clearance_granted=False, exit_code=EXIT_CODE_OPERATIONAL_ERROR, diagnostics=diagnostics
        )
    discovered_decisions.extend(plan_decs)

    if extra_decisions:
        discovered_decisions.extend(extra_decisions)

    # 3. Decision D6: Zero decisions behavior across discovered scope
    if not discovered_decisions:
        has_wave_attestation = any(a.wave == wave for a in attestations)
        if allow_zero_decisions or has_wave_attestation:
            reason = "explicit authorization" if allow_zero_decisions else "no-decision attestation"
            diagnostics.append(f"Zero decisions found in scope; allowed by {reason}.")
            return PreWaveGateResult(wave=wave, clearance_granted=True, exit_code=EXIT_CODE_CLEARED, diagnostics=diagnostics)
        diagnostics.append(
            "FAIL_CLOSED_ZERO_DECISIONS: Automated checkpoint found 0 decision records in scope. "
            "Execution halted under Decision D6 policy."
        )
        return PreWaveGateResult(wave=wave, clearance_granted=False, exit_code=EXIT_CODE_NO_DECISIONS, diagnostics=diagnostics)

    wave_decisions = [d for d in discovered_decisions if d.wave == wave]
    valid_decs, val_errors = validate_decision_collection(wave_decisions)
    if not valid_decs:
        diagnostics.extend(val_errors)
        return PreWaveGateResult(wave=wave, clearance_granted=False, exit_code=EXIT_CODE_OPERATIONAL_ERROR, diagnostics=diagnostics)

    if not wave_decisions:
        diagnostics.append(f"Zero decisions found in scope for Wave {wave}.")
        return PreWaveGateResult(wave=wave, clearance_granted=True, exit_code=EXIT_CODE_CLEARED, diagnostics=diagnostics)

    # 4. Evaluate each decision
    open_decs, resolved_decs = _classify_wave_decisions(wave_decisions, wave, ledger_path, diagnostics)
    if not open_decs:
        return PreWaveGateResult(
            wave=wave, clearance_granted=True, exit_code=EXIT_CODE_CLEARED,
            resolved_decisions=resolved_decs, diagnostics=diagnostics
        )

    payload = format_ask_user_question_payload_for_decision(open_decs[0])
    return PreWaveGateResult(
        wave=wave, clearance_granted=False, exit_code=EXIT_CODE_OPERATOR_INPUT_REQUIRED,
        open_decisions=open_decs, resolved_decisions=resolved_decs,
        ask_user_question_payload=payload, diagnostics=diagnostics
    )


def run_self_test() -> Tuple[bool, List[str]]:
    """Deterministic internal self-test suite."""
    diagnostics: List[str] = []
    opt_a = {
        "label": "Option A", "plain_english_summary": "First option",
        "impact_statement": "Reduces operational latency.", "confidence_score": 0.88, "recommended": True
    }
    opt_b = {
        "label": "Option B", "plain_english_summary": "Second option",
        "impact_statement": "Increases throughput.", "confidence_score": 0.60, "recommended": False
    }
    open_dec = PreWaveDecision(
        decision_id="SYNTHETIC-DEC-001", wave=3, status="OPEN",
        question="Which verification architecture should be adopted for Wave 3?",
        options=[opt_a, opt_b]
    )

    res_open = evaluate_pre_wave_gate(wave=3, extra_decisions=[open_dec])
    if res_open.exit_code != 2 or res_open.clearance_granted:
        diagnostics.append(f"Self-test failed: Expected exit code 2 for open decision, got {res_open.exit_code}")

    res_zero = evaluate_pre_wave_gate(wave=99, extra_decisions=[], allow_zero_decisions=False)
    if res_zero.exit_code != 3:
        diagnostics.append(f"Self-test failed: Expected exit code 3 for zero decisions, got {res_zero.exit_code}")

    res_zero_allowed = evaluate_pre_wave_gate(wave=99, extra_decisions=[], allow_zero_decisions=True)
    if res_zero_allowed.exit_code != 0:
        diagnostics.append(f"Self-test failed: Expected exit code 0 for zero decisions allowed, got {res_zero_allowed.exit_code}")

    return len(diagnostics) == 0, diagnostics

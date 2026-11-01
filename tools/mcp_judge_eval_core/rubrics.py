"""Rubric-based deterministic judge evaluation for candidate artifacts."""
from __future__ import annotations

import json
from typing import List

from dual_pipeline.core import utc_now
from dual_pipeline.release_thresholds import ReleaseThresholdPolicy


def judge_evaluate_candidate(
    rubric_id: str,
    candidate_text: str,
    context_envelope_json: str = "{}",
    token_budget: int = 4000,
) -> str:
    """Evaluate candidate text against a governed rubric with smart token-budget truncation protection.

    Args:
        rubric_id: Rubric identifier ('forensic_fidelity', 'executive_recap', 'graph_projection', 'svp_leverage', 'governance_compliance').
        candidate_text: The candidate text or JSON to be evaluated.
        context_envelope_json: Supporting background context JSON.
        token_budget: Maximum tokens allowed for context (approx 4 chars/token). Truncates excess context safely.
    """
    try:
        context_data = json.loads(context_envelope_json) if context_envelope_json.strip() else {}
        context_str = json.dumps(context_data)

        # Smart truncation of context to avoid token-budget overflow (f1a9a89 fix)
        char_budget = token_budget * 4
        is_truncated = False
        if len(context_str) > char_budget:
            context_str = context_str[:char_budget] + "... [TRUNCATED_TO_BUDGET]"
            is_truncated = True

        # Perform rule-based deterministic evaluation based on rubric
        rubric = rubric_id.lower().strip()
        errors: List[str] = []
        score = 100.0

        # Enforce Rule C-19 and Credential Isolation fail-closed gates
        from dual_pipeline.live_meeting_recorder_core.c19_firewall import has_c19_marker
        from dual_pipeline.sanitizer import SECRET_PATTERNS

        if has_c19_marker(candidate_text):
            errors.append("Rule C-19 violation: Career leverage or personal advancement markers detected in candidate.")
            score = 0.0

        if any(p.search(candidate_text) for p in SECRET_PATTERNS):
            errors.append("Credential leak violation: Secret or API key pattern detected in candidate text.")
            score = 0.0

        if rubric == "forensic_fidelity":
            if "Ambiguity Lock Register" not in candidate_text:
                errors.append("Missing mandatory Ambiguity Lock Register.")
                score -= 30.0
            if len(candidate_text.strip()) < 50:
                errors.append("Candidate text is too brief to represent a forensic transcript.")
                score -= 40.0

        elif rubric == "executive_recap":
            if "Actionable Next Steps" not in candidate_text:
                errors.append("Missing mandatory Actionable Next Steps table.")
                score -= 35.0
            if "Meeting Objective" not in candidate_text:
                errors.append("Missing Meeting Objective.")
                score -= 25.0

        elif rubric == "graph_projection":
            try:
                proj = json.loads(candidate_text)
                if not isinstance(proj, dict):
                    errors.append("Graph projection must be a JSON object.")
                    score = 0.0
                else:
                    has_statements = isinstance(proj.get("statements"), list)
                    has_mutations = isinstance(proj.get("graph_mutations"), list)
                    has_nodes_edges = isinstance(proj.get("nodes"), list) and isinstance(proj.get("edges"), list)
                    if not (has_statements or has_mutations or has_nodes_edges):
                        errors.append(
                            "Invalid projection structure: missing 'graph_mutations', 'statements', or 'nodes'/'edges' lists."
                        )
                        score -= 50.0
            except Exception:
                errors.append("Graph projection candidate is not valid JSON.")
                score = 0.0

        elif rubric == "svp_leverage":
            lower_text = candidate_text.lower()
            if not any(k in lower_text for k in ["strategic", "decision", "governance", "executive", "impact", "milestone", "portfolio"]):
                errors.append("Candidate lacks strategic executive framing or portfolio decision alignment.")
                score -= 30.0
            if len(candidate_text.strip()) < 30:
                errors.append("Candidate text is too brief for an SVP-level strategic summary.")
                score -= 30.0

        elif rubric == "governance_compliance":
            for prohibited in ["TODO", "PLACEHOLDER", "FIXME"]:
                if prohibited in candidate_text:
                    errors.append(f"Found prohibited marker: {prohibited}")
                    score -= 20.0

        # Cross-check candidate claims against context if context provided
        if context_data and isinstance(context_data, dict):
            forbidden = context_data.get("forbidden_claims") or context_data.get("disallowed_claims") or []
            if isinstance(forbidden, list):
                for fc in forbidden:
                    if isinstance(fc, str) and fc.lower() in candidate_text.lower():
                        errors.append(f"Unsubstantiated or forbidden claim detected: {fc}")
                        score -= 25.0
            required = context_data.get("required_claims") or context_data.get("expected_claims") or []
            if isinstance(required, list):
                for rc in required:
                    if isinstance(rc, str) and rc.lower() not in candidate_text.lower():
                        errors.append(f"Missing required grounded claim from context: {rc}")
                        score -= 15.0

        advisory_floor = float(ReleaseThresholdPolicy().advisory_floor)
        verdict = (
            "PASS"
            if score >= advisory_floor and not errors
            else ("ESCALATE" if score >= 60.0 else "FAIL")
        )

        evidence_grade = (
            "A" if score >= 90.0 and not errors
            else ("B" if score >= 80.0
            else ("C" if score >= 70.0
            else ("D" if score >= 60.0 else "F")))
        )
        decision_chain = [
            f"Evaluated candidate against rubric '{rubric}'",
            f"Pre-egress Rule C-19 and credential checks: {'CLEARED' if score > 0 and not any('Rule C-19' in e or 'Credential' in e for e in errors) else 'VIOLATION'}",
            f"Structural rubric checks applied: {len(errors)} error(s) identified",
            f"Final score: {score:.1f}/100.0, Evidence Grade: {evidence_grade}, Verdict: {verdict}",
        ]

        return json.dumps(
            {
                "rubric_id": rubric,
                "verdict": verdict,
                "confidence_score": max(0.0, score) / 100.0,
                "evidence_grade": evidence_grade,
                "decision_chain": decision_chain,
                "token_budget_applied": token_budget,
                "context_truncated": is_truncated,
                "candidate_char_length": len(candidate_text),
                "errors": errors,
                "timestamp": utc_now(),
            },
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"error": f"Judge evaluation failed: {exc}"})

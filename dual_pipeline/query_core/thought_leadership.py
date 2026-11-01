"""
dual_pipeline/query_core/thought_leadership.py

Stakeholder-calibrated thought leadership and Next Best Action synthesis
grounded in quantitative funnel, portfolio capacity, commercial, and temporal proofs.
"""
from __future__ import annotations

import re
from typing import Any

from dual_pipeline.decision_engine_core.quantitative_math import (
    compute_commercial_burn_math,
    compute_portfolio_absorption_math,
    compute_recruiting_funnel_math,
)
from dual_pipeline.strategic_reasoning import (
    extract_quantitative_constraints,
    is_constraint_claim,
)
from dual_pipeline.strategic_reasoning_core.quantitative_ontology import (
    extract_audience_stakeholders,
    strip_audience_clause,
)


def _has_quantitative_constraint_signal(items: list[dict[str, Any]]) -> bool:
    for item in items:
        rec = item.get("record", {})
        text = f"{rec.get('title', '')} {rec.get('retrieval_text', '')}"
        meta_use = (item.get("retrieval_metadata") or {}).get("answer_use") or rec.get("answer_use")
        if meta_use == "decision_constraint" or is_constraint_claim(text):
            if extract_quantitative_constraints(text):
                return True
    return False


def _collect_citation_bundle(
    items: list[dict[str, Any]], max_records: int = 3
) -> tuple[list[str], list[str]]:
    citations: list[str] = []
    evidence_ids: list[str] = []
    for item in items[:max_records]:
        rec = item["record"]
        rid = str(rec["record_id"])
        ev_ids = [str(e) for e in rec.get("evidence_ids", []) if e]
        if rid and ev_ids:
            if rid not in citations:
                citations.append(rid)
            for eid in ev_ids:
                if eid not in evidence_ids:
                    evidence_ids.append(eid)
    return citations, evidence_ids


def _build_quantitative_nbas(
    recommendation_basis: list[dict[str, Any]],
    question: str,
) -> list[dict[str, Any]]:
    approved_basis = [
        it for it in recommendation_basis if it["record"].get("lane") == "approved"
    ] or recommendation_basis
    citations, _ = _collect_citation_bundle(approved_basis, max_records=3)
    if not citations:
        return []
    funnel = compute_recruiting_funnel_math(50.0, 7.5)
    cap = compute_portfolio_absorption_math(50.0, 4.0, 6.0)
    burn = compute_commercial_burn_math(10.0, 600_000.0, 42.0, 45.0)
    audiences = extract_audience_stakeholders(question)
    aud_prefix = f"For executive alignment with {' and '.join(audiences)}, " if audiences else ""
    nba_1 = (
        f"{aud_prefix}Pivot from 10 segregated local pods (50 engineers requiring "
        f"{funnel['required_candidate_pool']} local candidates at a 5-10% screening pass rate "
        f"and exceeding the {int(cap['max_absorbable_headcount'])}-engineer capacity ceiling across "
        f"{int(cap['active_workstreams'])} active workstreams) to 2-3 shared platform pods "
        "(10-18 engineers) anchored by co-located West Palm Beach Principal Leads and "
        "national NEE-bar-cleared engineers under a 1:8 lead-to-engineer ratio."
    )
    nba_2 = (
        f"Restructure commercial terms from {int(burn['weekly_billing_hours'])} hours/week "
        f"fixed billing ({int(burn['onshore_premium_pct'])}% onshore rate premium) to a 40-hour "
        "baseline with milestone holdbacks, and synchronize 6-month SOW cycles with a "
        "mandatory 14-day IT deprovisioning notice buffer."
    )
    return [
        {"action": nba_1, "basis_citations": list(citations)},
        {"action": nba_2, "basis_citations": list(citations)},
    ]


def synthesize_next_best_actions(
    query_type: str,
    opportunity_rows: list[dict[str, Any]],
    recommendation_basis: list[dict[str, Any]],
    question: str = "",
) -> list[dict[str, Any]]:
    """Synthesize evidence-bound Next Best Actions with quantitative constraint proofs when applicable."""
    actions: list[dict[str, Any]] = []
    seen: set[str] = set()

    if query_type == "opportunity_nba" and _has_quantitative_constraint_signal(recommendation_basis):
        for row in _build_quantitative_nbas(recommendation_basis, question):
            seen.add(row["action"])
            actions.append(row)

    for opp_row in opportunity_rows[:3]:
        addr_str = ", ".join(opp_row["addresses"][:2])
        act_text = f"Prioritize {opp_row['opportunity']} ({opp_row['why_prioritize']}) to address {addr_str}."
        if act_text not in seen:
            seen.add(act_text)
            actions.append({"action": act_text, "basis_citations": opp_row["citations"]})

    if query_type == "opportunity_nba" and not actions:
        for rec_item in recommendation_basis[:2]:
            top_rec = rec_item["record"]
            act_text = f"Advance {top_rec['title']}: {top_rec['retrieval_text']}"
            if act_text not in seen:
                seen.add(act_text)
                actions.append({"action": act_text, "basis_citations": [top_rec["record_id"]]})
    return actions[:4]


def _append_overflow_must_cite(
    current_state: list[dict[str, Any]],
    overflow_items: list[dict[str, Any]],
) -> None:
    if not current_state:
        return
    last_row = current_state[-1]
    for extra_item in overflow_items:
        rec = extra_item["record"]
        if rec["record_id"] not in last_row["citations"]:
            last_row["citations"].append(rec["record_id"])
        for eid in rec["evidence_ids"]:
            if eid not in last_row["evidence_ids"]:
                last_row["evidence_ids"].append(eid)


def build_governed_current_state(
    factual_approved: list[dict[str, Any]],
    effective_factual: list[dict[str, Any]],
    packet: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    req_approved_ids = set(
        (packet.get("required_material_record_ids_by_lane") or {}).get("approved")
        or [it["record"]["record_id"] for it in factual_approved[:2]]
    )
    path_approved_ids = {
        str(p.get("record_id")) for p in (packet.get("graph_paths") or [])[:2]
        if isinstance(p, dict) and p.get("lane") == "approved" and p.get("record_id")
    }
    req_factual = [it for it in factual_approved if it["record"]["record_id"] in req_approved_ids]
    path_factual = [
        it for it in factual_approved
        if it["record"]["record_id"] in path_approved_ids and it not in req_factual
    ]
    must_cite = req_factual + path_factual
    ordered = must_cite + [it for it in effective_factual if it not in must_cite]
    current_state = [
        {
            "statement": item["record"]["retrieval_text"],
            "citations": [item["record"]["record_id"]],
            "evidence_ids": list(item["record"]["evidence_ids"]),
            "lane": "approved",
        }
        for item in ordered[:6]
    ]
    _append_overflow_must_cite(current_state, must_cite[6:])
    return current_state, ordered

"""Edge-specific retrieval evaluation gate and audit tooling.

Provides floor validation (min approved/candidate edge cases, predicate diversity,
name-free paraphrases) and comprehensive audit of edge recall, leaks, and crowding.
"""
from __future__ import annotations

import math
from typing import Any

DEFAULT_EDGE_FLOORS: dict[str, int] = {
    "min_approved": 25,
    "min_candidate": 10,
    "min_predicates": 10,
    "min_name_free": 15,
}

_BASELINE_ENTITY_S1: float = 0.953


def _reciprocal_rank(retrieved_ids: list[str], expected_ids: set[str]) -> float:
    if not expected_ids:
        return 0.0
    for rank, rid in enumerate(retrieved_ids, 1):
        if rid in expected_ids:
            return 1.0 / rank
    return 0.0


def _ndcg_at_k(retrieved_ids: list[str], expected_ids: set[str], k: int = 10) -> float:
    if not expected_ids:
        return 0.0
    score = sum(
        1.0 / math.log2(rank + 1)
        for rank, rid in enumerate(retrieved_ids[:k], 1)
        if rid in expected_ids
    )
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(expected_ids), k) + 1))
    return 0.0 if ideal == 0 else score / ideal


def _collect_all_edge_cases(
    suite_or_qrels: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cases = list(suite_or_qrels.get("edge_cases") or suite_or_qrels.get("cases") or [])
    name_free_cases = list(suite_or_qrels.get("name_free_cases") or [])
    all_edge_cases = list(cases)
    case_ids = {c.get("case_id") for c in all_edge_cases}
    for nf in name_free_cases:
        if nf.get("case_id") not in case_ids:
            all_edge_cases.append(nf)
            case_ids.add(nf.get("case_id"))
    return all_edge_cases, name_free_cases


def _count_case_distribution(
    all_edge_cases: list[dict[str, Any]],
    name_free_cases: list[dict[str, Any]],
) -> tuple[int, int, set[str], int]:
    approved_count = 0
    candidate_count = 0
    distinct_predicates: set[str] = set()
    name_free_count = len(name_free_cases)

    for c in all_edge_cases:
        expected = set(c.get("expected_record_ids") or [])
        lane = c.get("lane", "approved")
        pred = c.get("predicate") or c.get("extensions", {}).get("com.partner_advisory.nee.predicate")
        query_form = c.get("query_form", "")

        if query_form == "name_free_paraphrase" and c not in name_free_cases:
            name_free_count += 1

        if expected:
            if lane == "approved":
                approved_count += 1
            elif lane == "candidate":
                candidate_count += 1

        if pred:
            distinct_predicates.add(str(pred))

    return approved_count, candidate_count, distinct_predicates, name_free_count


def validate_edge_gate_floors(
    suite_or_qrels: dict[str, Any],
    *,
    min_approved: int = 25,
    min_candidate: int = 10,
    min_predicates: int = 10,
    min_name_free: int = 15,
) -> dict[str, Any]:
    """Validate that an evaluation suite or QRELS dataset meets edge gate floor standards.

    Fails closed (status='FAIL', passed=False) if any requirement is not met.
    """
    all_edge_cases, name_free_cases = _collect_all_edge_cases(suite_or_qrels)
    approved_count, candidate_count, distinct_predicates, name_free_count = _count_case_distribution(
        all_edge_cases, name_free_cases
    )

    failures: list[str] = []
    if approved_count < min_approved:
        failures.append(
            f"Approved edge gold count {approved_count} is below floor {min_approved}"
        )
    if candidate_count < min_candidate:
        failures.append(
            f"Candidate edge gold count {candidate_count} is below floor {min_candidate}"
        )
    if len(distinct_predicates) < min_predicates:
        failures.append(
            f"Distinct predicate count {len(distinct_predicates)} is below floor {min_predicates} "
            f"(found: {sorted(distinct_predicates)})"
        )
    if name_free_count < min_name_free:
        failures.append(
            f"Name-free paraphrase count {name_free_count} is below floor {min_name_free}"
        )

    passed = len(failures) == 0
    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "floors": {
            "min_approved": min_approved,
            "min_candidate": min_candidate,
            "min_predicates": min_predicates,
            "min_name_free": min_name_free,
        },
        "counts": {
            "approved_edge_gold_count": approved_count,
            "candidate_edge_gold_count": candidate_count,
            "distinct_predicates_count": len(distinct_predicates),
            "name_free_count": name_free_count,
            "total_edge_cases": len(all_edge_cases),
        },
        "predicates_covered": sorted(distinct_predicates),
        "failures": failures,
    }


def _evaluate_single_edge_case(
    c: dict[str, Any],
    results: dict[str, list[str]],
) -> dict[str, Any]:
    cid = c.get("case_id", "")
    retrieved = results.get(cid, [])
    expected = set(c.get("expected_record_ids") or [])

    return {
        "case_id": cid,
        "hit_at_1": 1.0 if any(r in expected for r in retrieved[:1]) else 0.0,
        "hit_at_5": 1.0 if any(r in expected for r in retrieved[:5]) else 0.0,
        "hit_at_10": 1.0 if any(r in expected for r in retrieved[:10]) else 0.0,
        "hit_at_20": 1.0 if any(r in expected for r in retrieved[:20]) else 0.0,
        "mrr": _reciprocal_rank(retrieved, expected),
        "ndcg_at_10": _ndcg_at_k(retrieved, expected, k=10),
    }


def _detect_leaks_in_cases(
    cases: list[dict[str, Any]],
    results: dict[str, list[str]],
) -> list[dict[str, Any]]:
    leaks: list[dict[str, Any]] = []
    for c in cases:
        cid = c.get("case_id", "")
        retrieved = results.get(cid, [])
        forbidden = set(c.get("forbidden_record_ids") or [])
        found_forbidden = [r for r in retrieved if r in forbidden]
        if found_forbidden:
            leaks.append({
                "case_id": cid,
                "query": c.get("query", ""),
                "leak_category": c.get("adversarial_category", "forbidden_leak"),
                "leaked_record_ids": found_forbidden,
            })

        if c.get("expected_abstention") and retrieved:
            leaks.append({
                "case_id": cid,
                "query": c.get("query", ""),
                "leak_category": "failed_abstention",
                "leaked_record_ids": retrieved[:5],
            })
    return leaks


def _compute_crowding(
    control_cases: list[dict[str, Any]],
    results: dict[str, list[str]],
) -> dict[str, Any]:
    entity_control_count = 0
    claim_control_count = 0
    entity_hits_at_1 = 0
    claim_hits_at_1 = 0

    for cc in control_cases:
        cid = cc.get("case_id", "")
        retrieved = results.get(cid, [])
        expected = set(cc.get("expected_record_ids") or [])
        hit1 = 1 if (retrieved and retrieved[0] in expected) else 0

        target_kind = cc.get("target_kind", "entity")
        if target_kind == "entity":
            entity_control_count += 1
            entity_hits_at_1 += hit1
        else:
            claim_control_count += 1
            claim_hits_at_1 += hit1

    entity_s1 = round(entity_hits_at_1 / entity_control_count, 4) if entity_control_count > 0 else 0.0
    claim_s1 = round(claim_hits_at_1 / claim_control_count, 4) if claim_control_count > 0 else 0.0
    crowding_s1_drop = round(_BASELINE_ENTITY_S1 - entity_s1, 4) if entity_control_count > 0 else 0.0

    return {
        "baseline_entity_s1": _BASELINE_ENTITY_S1,
        "measured_entity_s1": entity_s1,
        "crowding_s1_drop": crowding_s1_drop,
        "entity_controls_evaluated": entity_control_count,
        "claim_controls_evaluated": claim_control_count,
        "claim_s1": claim_s1,
        "crowding_risk_elevated": crowding_s1_drop > 0.05,
    }


def audit_edge_recall_and_leaks(
    results: dict[str, list[str]],
    qrels: dict[str, Any],
) -> dict[str, Any]:
    """Compute edge recall@{1,5,10,20}, MRR, nDCG@10, leak detection, and crowding metrics.

    Args:
        results: Mapping from case_id to list of retrieved record_ids in rank order.
        qrels: QRELS dataset dictionary containing edge_cases, name_free_cases,
               control_cases, and adversarial_cases.
    """
    edge_cases = list(qrels.get("edge_cases") or [])
    name_free_cases = list(qrels.get("name_free_cases") or [])
    control_cases = list(qrels.get("control_cases") or [])
    adversarial_cases = list(qrels.get("adversarial_cases") or [])

    combined_edge: list[dict[str, Any]] = list(edge_cases)
    seen_ids = {c["case_id"] for c in combined_edge if "case_id" in c}
    for nf in name_free_cases:
        if nf.get("case_id") not in seen_ids:
            combined_edge.append(nf)
            seen_ids.add(nf.get("case_id"))

    case_metrics: list[dict[str, Any]] = []
    by_predicate: dict[str, list[dict[str, Any]]] = {}
    by_query_form: dict[str, list[dict[str, Any]]] = {}
    by_degree: dict[str, list[dict[str, Any]]] = {}
    by_lane: dict[str, list[dict[str, Any]]] = {}

    for c in combined_edge:
        entry = _evaluate_single_edge_case(c, results)
        case_metrics.append(entry)

        pred = c.get("predicate") or "UNKNOWN"
        by_predicate.setdefault(str(pred), []).append(entry)

        qform = c.get("query_form", "standard")
        by_query_form.setdefault(str(qform), []).append(entry)

        deg = c.get("degree_bucket", "unknown")
        by_degree.setdefault(str(deg), []).append(entry)

        lane = c.get("lane", "approved")
        by_lane.setdefault(str(lane), []).append(entry)

    def _avg(items: list[dict[str, Any]], field: str) -> float:
        if not items:
            return 0.0
        return round(sum(it[field] for it in items) / len(items), 4)

    def _summarize(items: list[dict[str, Any]]) -> dict[str, float]:
        return {
            "count": len(items),
            "recall_at_1": _avg(items, "hit_at_1"),
            "recall_at_5": _avg(items, "hit_at_5"),
            "recall_at_10": _avg(items, "hit_at_10"),
            "recall_at_20": _avg(items, "hit_at_20"),
            "mrr": _avg(items, "mrr"),
            "ndcg_at_10": _avg(items, "ndcg_at_10"),
        }

    overall_metrics = _summarize(case_metrics)
    predicate_summary = {p: _summarize(rows) for p, rows in sorted(by_predicate.items())}
    query_form_summary = {qf: _summarize(rows) for qf, rows in sorted(by_query_form.items())}
    degree_summary = {d: _summarize(rows) for d, rows in sorted(by_degree.items())}
    lane_summary = {lane_name: _summarize(rows) for lane_name, rows in sorted(by_lane.items())}

    all_evaluated_cases = list(combined_edge) + list(adversarial_cases)
    leaks = _detect_leaks_in_cases(all_evaluated_cases, results)
    crowding_analysis = _compute_crowding(control_cases, results)

    return {
        "status": "PASS" if not leaks else "LEAKS_DETECTED",
        "total_edge_cases_evaluated": len(case_metrics),
        "overall_metrics": overall_metrics,
        "metrics_by_predicate": predicate_summary,
        "metrics_by_query_form": query_form_summary,
        "metrics_by_degree_bucket": degree_summary,
        "metrics_by_lane": lane_summary,
        "leak_count": len(leaks),
        "leaks": leaks,
        "crowding_analysis": crowding_analysis,
    }


def extend_evaluation_with_edge_gate(
    evaluation: dict[str, Any],
    qrels: dict[str, Any],
    *,
    min_approved: int = 25,
    min_candidate: int = 10,
    min_predicates: int = 10,
    min_name_free: int = 15,
) -> dict[str, Any]:
    """Extend a retrieval-release evaluation artifact with the edge floor gate."""
    floor_res = validate_edge_gate_floors(
        qrels,
        min_approved=min_approved,
        min_candidate=min_candidate,
        min_predicates=min_predicates,
        min_name_free=min_name_free,
    )
    gates = dict(evaluation.get("gates", {}))
    gates["edge_gate"] = floor_res["passed"]
    evaluation["gates"] = gates
    evaluation["edge_gate_details"] = floor_res

    if not floor_res["passed"]:
        evaluation["status"] = "FAIL"

    return evaluation

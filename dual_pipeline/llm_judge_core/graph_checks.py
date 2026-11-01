"""
dual_pipeline/llm_judge_core/graph_checks.py

Deterministic preflight and scoring checks for GraphExtractionJudge (Output 03).
Enforces:
1. Quote locator grounding in source transcript & complete evidence_ids resolution (Dim 6).
2. Predicate specificity and canonical taxonomy from generic predicate counts (Dim 4).
3. Strict exclusion of tactical action items in action_updates (Dim 5).
4. Archetype conditional dimension omission (Dim 1 for TACTICAL_ALIGNMENT; Dim 2 never omitted).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from dual_pipeline.prompts.contracts import OUTPUT3_CONTRACT, MeetingArchetype
from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY


def is_document_pipeline_mode(mode_or_archetype: Optional[Any]) -> bool:
    if not mode_or_archetype:
        return False
    norm = str(mode_or_archetype).strip().upper()
    return norm in ("DOCUMENT", "DOCUMENT_MODE", "DOC") or norm.startswith("DOC_")

GENERIC_PREDICATES: Set[str] = {
    "RELATES_TO", "RELATED_TO", "ASSOCIATED_WITH", "CONNECTED_TO", "LINKED_TO",
    "RELATES", "ASSOCIATED", "CONNECTED", "INTERACTS_WITH",
}


def _extract_mutations(f3_parsed: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(f3_parsed.get("graph_mutations"), dict):
        return f3_parsed["graph_mutations"]
    return f3_parsed


def _is_exempt_evidence_entry(ev: Dict[str, Any]) -> bool:
    spk = str(ev.get("speaker") or "").strip().lower()
    src = str(ev.get("source_type") or ev.get("source") or "").strip().upper()
    loc = ev.get("locator")
    loc_kind = str(loc.get("kind") or "").strip().lower() if isinstance(loc, dict) else str(loc or "").strip().lower()
    eid = str(ev.get("id") or ev.get("statement_id") or ev.get("evidence_id") or "").lower()
    return (
        spk.startswith("attachment:")
        or spk in ("meeting roster", "operator", "hitl")
        or src in ("TRANSCRIPT_HITL", "ATTACHMENT", "COLLATERAL", "C0_RECEIPT", "ROSTER")
        or loc_kind in ("attachment", "hitl_decision_ledger", "c0_receipt", "collateral", "roster", "receipt")
        or loc_kind.startswith("attachment:")
        or "roster" in eid
    )


def _is_quote_grounded_in_text(quote: str, f1_clean: str, f1_unformatted: str) -> bool:
    """Verify quote presence with markdown and list prefix normalization."""
    if len(quote) < 8 or not f1_clean:
        return True
    quote_clean = re.sub(r"\s+", " ", quote.lower().strip("\"'"))
    quote_clean = re.sub(r"[*_`]", "", quote_clean)
    if quote_clean in f1_clean or (f1_unformatted and quote_clean in f1_unformatted):
        return True
    quote_stripped = re.sub(r"^\s*[\d.\-•*#]+\s*", "", quote_clean)
    if quote_stripped and len(quote_stripped) >= 8:
        if quote_stripped in f1_clean or (f1_unformatted and quote_stripped in f1_unformatted):
            return True
    snip = quote_clean[:30].strip()
    snip_stripped = quote_stripped[:30].strip() if quote_stripped else ""
    if snip and (snip in f1_clean or (f1_unformatted and snip in f1_unformatted)):
        return True
    if snip_stripped and (snip_stripped in f1_clean or (f1_unformatted and snip_stripped in f1_unformatted)):
        return True
    return False


def check_quote_and_evidence_resolution(
    f3_parsed: Dict[str, Any],
    f1_text: str,
    collateral_text: str = "",
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Verify that every quote appears at its locator in source text and all evidence_ids resolve."""
    mutations = _extract_mutations(f3_parsed)
    evidence_list = (
        mutations.get("evidence")
        or mutations.get("evidence_entries")
        or f3_parsed.get("evidence")
        or f3_parsed.get("evidence_entries")
        or mutations.get("claim_checks")
        or []
    )

    evidence_map: Dict[str, Dict[str, Any]] = {}
    if isinstance(evidence_list, list):
        for item in evidence_list:
            if isinstance(item, dict):
                eid = str(item.get("id") or item.get("evidence_id") or item.get("claim_id") or "")
                if eid:
                    evidence_map[eid] = item
    ev_idx = f3_parsed.get("evidence_index") or mutations.get("evidence_index")
    if isinstance(ev_idx, dict):
        for eid, item in ev_idx.items():
            if isinstance(item, dict) and str(eid) not in evidence_map:
                evidence_map[str(eid)] = item

    defects: List[str] = []
    unresolved_evidence_ids: List[str] = []
    broken_quotes: List[str] = []

    # Check that referenced evidence_ids resolve
    referenced_eids: Set[str] = set()
    for container_key in ("entity_upserts", "entities", "relationship_signals", "relations", "edges"):
        items = mutations.get(container_key) or []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    eids = item.get("evidence_ids") or item.get("claim_check_ids") or []
                    if isinstance(eids, list):
                        for eid in eids:
                            eid_str = str(eid)
                            referenced_eids.add(eid_str)
                            if eid_str not in evidence_map:
                                unresolved_evidence_ids.append(eid_str)

    if unresolved_evidence_ids:
        unique_unresolved = sorted(set(unresolved_evidence_ids))
        defects.append(
            f"Unresolved evidence_ids ({len(unique_unresolved)} unlinked references: {', '.join(unique_unresolved[:5])}). "
            "Every mutation must reference an existing evidence entry."
        )

    # Check that quotes exist in source transcript or uploaded collateral
    combined_source = f"{f1_text or ''}\n{collateral_text or ''}".strip()
    f1_clean = re.sub(r"\s+", " ", combined_source.lower()) if combined_source else ""
    f1_unformatted = re.sub(r"[*_`#]", "", f1_clean) if f1_clean else ""
    for eid, ev in evidence_map.items():
        if _is_exempt_evidence_entry(ev):
            continue
        quote = str(ev.get("quote") or ev.get("verbatim_quote") or ev.get("exact_text") or ev.get("text") or "").strip()
        loc = str(ev.get("locator") or ev.get("timestamp") or "")
        if not _is_quote_grounded_in_text(quote, f1_clean, f1_unformatted):
            broken_quotes.append(f"'{quote[:40]}...' at [{loc}]")

    if broken_quotes:
        defects.append(
            f"Quote grounding defect: {len(broken_quotes)} quote(s) not grounded in source transcript "
            f"({'; '.join(broken_quotes[:3])})."
        )

    passed = len(defects) == 0
    stats = {
        "total_referenced_eids": len(referenced_eids),
        "unresolved_eids_count": len(unresolved_evidence_ids),
        "broken_quotes_count": len(broken_quotes),
        "defects": defects,
    }
    return passed, defects, stats


def compute_predicate_specificity_score(
    f3_parsed: Dict[str, Any],
) -> Tuple[int, List[str], List[str]]:
    """Score Dimension 4 (Predicate Specificity & Canonical Taxonomy) from generic predicate counts.

    Max points: 15. Floor: 11.
    Each generic predicate deducts 4 points.
    """
    mutations = _extract_mutations(f3_parsed)
    relations = mutations.get("relationship_signals") or mutations.get("relations") or mutations.get("edges") or []
    if not isinstance(relations, list):
        relations = []

    generic_found: List[str] = []
    for rel in relations:
        if isinstance(rel, dict):
            pred = str(rel.get("relationship") or rel.get("predicate") or rel.get("relation") or rel.get("type") or rel.get("predicate_type") or "").strip().upper()
            if pred in GENERIC_PREDICATES:
                from_id = rel.get("from_id") or rel.get("source") or "?"
                to_id = rel.get("to_id") or rel.get("target") or "?"
                generic_found.append(f"{from_id} -[{pred}]-> {to_id}")

    defect_codes: List[str] = []
    deduction = len(generic_found) * 4
    score = max(0, 15 - deduction)

    if generic_found:
        defect_codes.append("ERR_GENERIC_PREDICATES")

    return score, generic_found, defect_codes


def compute_action_exclusion_score(
    f3_parsed: Dict[str, Any],
) -> Tuple[int, List[str]]:
    """Score Dimension 5 (Strict Exclusion of Action Items) from empty action_updates.

    Max points: 15. Floor: 11.
    If action_updates is empty: 15/15.
    If tactical action items leak: 0/15 (floor failure).
    """
    mutations = _extract_mutations(f3_parsed)
    action_updates = mutations.get("action_updates")
    if action_updates is None:
        action_updates = f3_parsed.get("action_updates")

    if not action_updates or (isinstance(action_updates, (list, dict)) and len(action_updates) == 0):
        return 15, []

    item_count = len(action_updates) if isinstance(action_updates, (list, dict)) else 1
    defects = [
        f"CONTAMINATION DEFECT: Output 03 contains {item_count} tactical action items in 'action_updates'. "
        "Action items and sprint tasks strictly partitioned out of knowledge graph entity mutations."
    ]
    return 0, defects


def _resolve_graph_scoring_mode(
    parsed_eval: Dict[str, Any], archetype: Optional[Any], pipeline_mode: Optional[str]
) -> Tuple[bool, MeetingArchetype]:
    eff_mode = (
        pipeline_mode
        or parsed_eval.get("pipeline_mode")
        or parsed_eval.get("modality")
        or parsed_eval.get("meeting_archetype")
        or (str(archetype) if archetype is not None else None)
    )
    is_doc_mode = is_document_pipeline_mode(eff_mode)
    if isinstance(archetype, MeetingArchetype):
        eff_arch = archetype
    elif isinstance(archetype, str) and not is_document_pipeline_mode(archetype):
        eff_arch = MeetingArchetype.from_string(archetype)
    else:
        eff_arch = MeetingArchetype.GENERAL
    return is_doc_mode, eff_arch


def _collect_critical_defects(
    parsed_eval: Dict[str, Any], defect_codes: List[str], quotes_passed: bool, floor_failures: List[str], score: float, is_doc_mode: bool
) -> List[str]:
    raw_verdict = str((parsed_eval.get("raw_judge_scorecard") or {}).get("verdict") or parsed_eval.get("verdict") or "").upper()
    ungrounded_is_crit = not quotes_passed or raw_verdict != "PASS" or bool(floor_failures) or score < CANONICAL_RELEASE_POLICY.output3_pass_threshold
    always = [
        c for c in defect_codes
        if c.startswith(("ERR_HALLUCINATED_", "ERR_VACUOUS_", "ERR_STUB_", "ERR_UNRESOLVED_", "ERR_ACTION_ITEM_"))
        or (c.startswith("ERR_UNGROUNDED_") and ungrounded_is_crit)
    ]
    cond = [c for c in defect_codes if c.startswith(("ERR_OMITTED_", "ERR_UNDERREPRESENTED_"))] if (is_doc_mode or floor_failures or score < CANONICAL_RELEASE_POLICY.output3_pass_threshold) else []
    return always + cond


def apply_deterministic_graph_scoring(
    parsed_eval: Dict[str, Any], f3_parsed: Dict[str, Any], f1_text: str,
    archetype: Optional[Any] = None, collateral_text: str = "", pipeline_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Score D4, D5, and D6 schema deterministically and enforce archetype omissions."""
    is_doc_mode, eff_arch = _resolve_graph_scoring_mode(parsed_eval, archetype, pipeline_mode)

    # 0. Vacuous Graph Extraction Check (applies to both meeting mode and DOCUMENT_MODE)
    mutations = _extract_mutations(f3_parsed)
    entities = mutations.get("entity_upserts") or mutations.get("entities") or f3_parsed.get("entity_upserts") or f3_parsed.get("entities") or f3_parsed.get("nodes") or []
    signals = (
        mutations.get("relationship_signals") or mutations.get("relations") or mutations.get("edges")
        or mutations.get("strategic_intent_signals") or f3_parsed.get("relationship_signals")
        or f3_parsed.get("relations") or f3_parsed.get("edges") or []
    )
    decisions = mutations.get("decision_updates") or f3_parsed.get("decisions") or f3_parsed.get("statements") or []
    is_vacuous = not (entities or signals or decisions)
    has_substantive_source = bool(f1_text and len(f1_text.strip()) >= 500)

    if is_vacuous and has_substantive_source:
        for k in ("dimension_1_strategic_edge", "dimension_2_knowledge_layer_density", "dimension_3_semantic_atomicity", "dimension_4_predicate_specificity", "dimension_6_evidence_grounding_and_schema"):
            parsed_eval[k] = 0
        vac_msg = "VACUOUS GRAPH EXTRACTION DEFECT: 0 entity upserts and 0 relationship signals extracted despite substantive source text (>= 500 chars)."
        parsed_eval["forensic_rationale"] = f"{vac_msg}\n{parsed_eval.get('forensic_rationale', '')}".strip()
        codes = parsed_eval.setdefault("defect_codes", [])
        if "ERR_VACUOUS_GRAPH_EXTRACTION" not in codes:
            codes.append("ERR_VACUOUS_GRAPH_EXTRACTION")

    # 1. Deterministic Dim 4 (Predicate Specificity)
    d4_score, generic_found, d4_codes = compute_predicate_specificity_score(f3_parsed)
    if is_vacuous and has_substantive_source:
        d4_score = 0
    llm_d4 = parsed_eval.get("dimension_4_predicate_specificity")
    if llm_d4 is None:
        parsed_eval["dimension_4_predicate_specificity"] = d4_score
    elif generic_found or (is_vacuous and has_substantive_source):
        parsed_eval["dimension_4_predicate_specificity"] = min(int(llm_d4), d4_score)
    if generic_found:
        gen_msg = f"GENERIC PREDICATES DEFECT: Found {len(generic_found)} generic predicate(s): {', '.join(generic_found[:3])}."
        parsed_eval["forensic_rationale"] = f"{gen_msg}\n{parsed_eval.get('forensic_rationale', '')}".strip()
        parsed_eval.setdefault("defect_codes", []).extend(d4_codes)

    # 2. Deterministic Dim 5 (Exclusion of Action Items)
    d5_score, d5_defects = compute_action_exclusion_score(f3_parsed)
    llm_d5 = parsed_eval.get("dimension_5_exclusion_of_action_items")
    if llm_d5 is None:
        parsed_eval["dimension_5_exclusion_of_action_items"] = d5_score
    elif d5_defects:
        parsed_eval["dimension_5_exclusion_of_action_items"] = min(int(llm_d5), d5_score)
    if d5_defects:
        parsed_eval["forensic_rationale"] = f"{'; '.join(d5_defects)}\n{parsed_eval.get('forensic_rationale', '')}".strip()
        parsed_eval.setdefault("defect_codes", []).append("ERR_ACTION_ITEM_LEAK")

    # 3. Deterministic Dim 6 Quote & Evidence Resolution
    quotes_passed, quote_defects, _ = check_quote_and_evidence_resolution(f3_parsed, f1_text, collateral_text=collateral_text)
    if not quotes_passed:
        parsed_eval["dimension_6_evidence_grounding_and_schema"] = min(int(parsed_eval.get("dimension_6_evidence_grounding_and_schema", 18) or 18), 10)
        parsed_eval["forensic_rationale"] = f"EVIDENCE GROUNDING DEFECT: {'; '.join(quote_defects)}\n{parsed_eval.get('forensic_rationale', '')}".strip()
        parsed_eval.setdefault("defect_codes", []).append("ERR_UNRESOLVED_EVIDENCE")

    # 4. Archetype-conditional dimensions
    all_dims = OUTPUT3_CONTRACT.rubric_dimensions
    applicable_dims = [dim for dim in all_dims if dim.is_applicable_for(eff_arch)]
    dim_keys = [dim.key for dim in applicable_dims]
    for dim in all_dims:
        if not dim.is_applicable_for(eff_arch):
            parsed_eval[dim.key] = None

    active_dims = {dim.key: (parsed_eval[dim.key], float(dim.max_points)) for dim in applicable_dims if parsed_eval.get(dim.key) is not None}
    if active_dims:
        active_sum, max_possible = sum(float(v) for v, _ in active_dims.values()), sum(max_p for _, max_p in active_dims.values())
        if max_possible > 0:
            parsed_eval["composite_score"] = (
                round((active_sum / max_possible) * 100.0, 2)
                if len(active_dims) < len(all_dims)
                else (round(active_sum, 2) if active_sum % 1 != 0 else int(active_sum))
            )

    contract_floors = OUTPUT3_CONTRACT.get_dimension_floors(pipeline_mode="DOCUMENT_MODE" if is_doc_mode else None)
    floors = {dim.key: int(contract_floors[dim.key]) for dim in applicable_dims if dim.key in contract_floors}
    floor_failures = [f"{k}={parsed_eval.get(k)} (floor={floors[k]})" for k in dim_keys if parsed_eval.get(k) is not None and k in floors and float(parsed_eval.get(k)) < floors[k]]

    score = float(parsed_eval.get("composite_score", 0) or 0)
    defect_codes = [str(c) for c in (parsed_eval.get("defect_codes") or []) if c]
    critical_defects = _collect_critical_defects(parsed_eval, defect_codes, quotes_passed, floor_failures, score, is_doc_mode)

    if "RULE C-19 VIOLATION" in str(parsed_eval.get("forensic_rationale", "")):
        parsed_eval["verdict"], parsed_eval["status"], parsed_eval["composite_score"] = "FAIL", "FAIL", min(30, int(score))
    elif critical_defects:
        parsed_eval["verdict"], parsed_eval["status"] = "FAIL", "FAIL"
    elif floor_failures:
        is_borderline = len(floor_failures) == 1 and all(int(parsed_eval.get(k, 0)) >= (floors.get(k, 0) - 1) for k in dim_keys if parsed_eval.get(k) is not None and k in floors and int(parsed_eval.get(k)) < floors.get(k, 0))
        if is_borderline and score >= 85:
            parsed_eval["verdict"] = "HITL_REQUIRED"
            parsed_eval["forensic_rationale"] = f"Borderline floor deduction: {', '.join(floor_failures)}.\n{parsed_eval.get('forensic_rationale', '')}".strip()
        else:
            parsed_eval["verdict"], parsed_eval["status"] = "FAIL", "FAIL"
            parsed_eval["forensic_rationale"] = f"PER-DIMENSION FLOOR FAILURE: {', '.join(floor_failures)}.\n{parsed_eval.get('forensic_rationale', '')}".strip()
    elif 85 <= score < CANONICAL_RELEASE_POLICY.output3_pass_threshold or str(parsed_eval.get("verdict")).upper() == "HITL_REQUIRED":
        parsed_eval["verdict"] = "HITL_REQUIRED"
    elif score < CANONICAL_RELEASE_POLICY.output3_pass_threshold:
        parsed_eval["verdict"] = "FAIL"
    else:
        parsed_eval["verdict"] = CANONICAL_RELEASE_POLICY.evaluate_output_verdict(3, int(round(score))).value
    return parsed_eval


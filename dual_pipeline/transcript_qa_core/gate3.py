"""
Deterministic QA Gate 3: Canonical Signal Projection QA Gates.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from dual_pipeline.prompts.config import CANONICAL_PROMPT_CONFIG
from dual_pipeline.temporal_ssot import TemporalSSOT
from dual_pipeline.transcript_models import CanonicalSignalProjectionV57

from .semantic_linters import (
    lint_citation_coverage, lint_compound_properties, lint_mutation_budget, lint_predicate_domain,
)
from .gate3_topology import _check_topology
from .gate3_evidence import _sync_evidence_offsets

logger = logging.getLogger(__name__)

ALLOWED_PREFIXES: Tuple[str, ...] = CANONICAL_PROMPT_CONFIG.output3.allowed_entity_prefixes

CONVERSATIONAL_MARKERS = re.compile(
    r"\b(yeah[,.]|and then[,.]|it'?s like|like,\s*wouldn'?t|gotcha|dropped in|i mean|so if you guys|you guys can feed me|well,\s*you know)\b",
    re.IGNORECASE,
)
TRAILING_FRAGMENT = re.compile(r"(?:,\s*and\s*then|\band\s*\.\.\.|\bbut\s*\.\.\.|\band\s+then\s*\.)$", re.IGNORECASE)


def _parse_payload(f3_text: Union[str, Dict[str, Any]]) -> Tuple[Dict[str, Any], bool, bool, bool, List[str]]:
    """Parse JSON and perform Pydantic schema validation."""
    failures: List[str] = []
    if isinstance(f3_text, dict):
        data = f3_text
        syntax_ok = True
    else:
        try:
            data = json.loads(f3_text) if isinstance(f3_text, str) else {}
            syntax_ok = True
        except Exception as exc:
            data = {}
            syntax_ok = False
            failures.append(f"Output 3 JSON parse error: {exc}")

    schema_version_ok = data.get("schema_version") == "5.7"
    if not schema_version_ok:
        failures.append(f"Output 3 schema_version is '{data.get('schema_version')}', expected '5.7'")

    try:
        CanonicalSignalProjectionV57.model_validate(data)
        pydantic_ok = True
    except Exception as exc:
        pydantic_ok = False
        failures.append(f"Output 3 Pydantic schema validation failed: {exc}")

    return data, syntax_ok, schema_version_ok, pydantic_ok, failures


def _check_prefixes(entity_upserts: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
    """Validate that all entity IDs adhere to allowed domain prefixes."""
    failures: List[str] = []
    invalid_entities = [
        e.get("id") for e in entity_upserts
        if not any((e.get("id") or "").startswith(p) for p in ALLOWED_PREFIXES)
    ]
    if invalid_entities:
        failures.append(f"Entity IDs without allowed prefix: {invalid_entities}")
    return len(failures) == 0, failures


def _normalize_grounding(s: str) -> str:
    return s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("\u00a0", " ")


def _grounding_tokens(s: str) -> set[str]:
    return set(re.findall(r"\b\w{3,}\b", s.lower().replace("’", "").replace("'", "")))


def _find_best_sentence_match(exact_text: str, f1_sentences: List[str]) -> Optional[str]:
    q_toks = _grounding_tokens(exact_text)
    if not q_toks:
        return None
    best_overlap, best_cand = 0.0, None
    for s in f1_sentences:
        s_toks = _grounding_tokens(s)
        if s_toks:
            overlap = len(q_toks & s_toks) / len(q_toks)
            if overlap > best_overlap:
                best_overlap, best_cand = overlap, s
    if best_cand and best_overlap >= 0.60:
        return re.sub(r"^\*{1,2}[^*]+(?:\*{1,2}|:)\s*", "", best_cand).strip()
    return None


def _ground_single_evidence(ev: Dict[str, Any], norm_f1: str, f1_sentences: List[str], f1_text: str) -> bool:
    locator = ev.get("locator", {})
    loc_kind = locator.get("kind") if isinstance(locator, dict) else ""
    if (
        loc_kind in ("attachment", "metadata", "header", "hitl_decision_ledger", "c0_receipt", "section", "session_roster")
        or ev.get("speaker") in ("meeting_metadata", "metadata", "system", "Operator", "COLLATERAL_REVIEWED", "Forensic Context", "meeting_participants", "Session Roster")
        or ev.get("source") in ("TRANSCRIPT_HITL", "HITL", "HUMAN_OPERATOR", "C0_RECEIPT", "COLLATERAL", "ROSTER_PRIOR")
        or ev.get("source_type") in ("TRANSCRIPT_HITL", "HITL", "C0_RECEIPT", "COLLATERAL", "ROSTER_PRIOR")
        or ev.get("attribution_mode") in ("COLLATERAL_REVIEWED", "BACKGROUND", "TRANSCRIPT_HITL")
    ):
        return True
    exact = ev.get("exact_text", "")
    if not exact:
        return True
    norm_exact = _normalize_grounding(exact)
    if norm_exact not in norm_f1:
        return False
    qp = ev.get("quote_pointer")
    start: Optional[int] = None
    end: Optional[int] = None
    if isinstance(qp, list) and len(qp) >= 2 and isinstance(qp[0], int) and isinstance(qp[1], int):
        start, end = qp[0], qp[1]
    elif isinstance(qp, dict) and isinstance(qp.get("start_offset"), int) and isinstance(qp.get("end_offset"), int):
        start, end = qp["start_offset"], qp["end_offset"]
    if start is not None and end is not None and f1_text:
        if not (0 <= start < end <= len(f1_text)):
            return False
        if _normalize_grounding(f1_text[start:end]) != norm_exact:
            return False
    return True


def _check_evidence_grounding(evidence_index: Dict[str, Any], f1_text: str) -> Tuple[bool, List[str]]:
    """Verify evidence statement exact_text is verbatim grounded in Output 01."""
    if not f1_text:
        return True, []
    failures: List[str] = []
    norm_f1 = _normalize_grounding(f1_text)
    f1_sentences = [s.strip() for s in re.split(r"(?<=[.!?\n])\s+", f1_text) if len(s.strip()) >= 15]

    for eid, ev in evidence_index.items():
        if isinstance(ev, dict) and not _ground_single_evidence(ev, norm_f1, f1_sentences, f1_text):
            failures.append(f"Evidence {eid} exact_text not grounded in Output 1: '{(ev.get('exact_text') or '')[:30]}...'")
    return len(failures) == 0, failures


CLOSED_EVIDENCE_KINDS: Set[str] = set(CANONICAL_PROMPT_CONFIG.output3.allowed_statement_kinds)


def _check_evidence_kinds(evidence_index: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate that all evidence items adhere to the 6 closed kind values."""
    failures: List[str] = []
    for eid, ev in evidence_index.items():
        if isinstance(ev, dict):
            k = str(ev.get("kind", "")).strip().lower()
            if k and k not in CLOSED_EVIDENCE_KINDS:
                failures.append(
                    f"Evidence '{eid}' has invalid kind '{k}'. "
                    f"Must be one of 6 closed kind values: {sorted(CLOSED_EVIDENCE_KINDS)}"
                )
    return len(failures) == 0, failures


def _check_temporal_hygiene(
    decision_updates: List[Dict[str, Any]],
    risk_updates: List[Dict[str, Any]],
) -> Tuple[bool, List[str]]:
    """Validate that observed_at and stale_after conform to Tier 2 MM/DD/YYYY format."""
    failures: List[str] = []
    for dec in decision_updates:
        obs = dec.get("observed_at", "")
        if obs and not TemporalSSOT.validate_human_date(obs):
            failures.append(f"Decision update observed_at '{obs}' must be MM/DD/YYYY.")
        stale = dec.get("stale_after", "")
        if stale and not TemporalSSOT.validate_human_date(stale):
            failures.append(f"Decision update stale_after '{stale}' must be MM/DD/YYYY.")
    for rsk in risk_updates:
        obs = rsk.get("observed_at", "")
        if obs and not TemporalSSOT.validate_human_date(obs):
            failures.append(f"Risk update observed_at '{obs}' must be MM/DD/YYYY.")
        stale = rsk.get("stale_after", "")
        if stale and not TemporalSSOT.validate_human_date(stale):
            failures.append(f"Risk update stale_after '{stale}' must be MM/DD/YYYY.")
    return len(failures) == 0, failures


def _extract_fragment_targets(data: Dict[str, Any], mutations: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Gather text fields across evidence and mutations for speech fragment checking."""
    targets: List[Tuple[str, str]] = []
    for eid, ev in data.get("evidence_index", {}).items():
        if isinstance(ev, dict):
            targets.append((eid, (ev.get("text") or ev.get("exact_text") or "").strip()))

    for dec in mutations.get("decision_updates", []):
        targets.append((dec.get("decision_id") or "decision", f"{dec.get('decision_question') or ''} {dec.get('rationale') or ''}".strip()))
    for rsk in mutations.get("risk_updates", []):
        targets.append((rsk.get("risk_id") or "risk", (rsk.get("description") or "").strip()))
    for prop in mutations.get("proposal_updates", []):
        targets.append((prop.get("proposal_id") or "proposal", f"{prop.get('proposal_text') or ''} {prop.get('rationale') or ''}".strip()))
    for ent in mutations.get("entity_upserts", []):
        targets.append((ent.get("id") or "entity", f"{ent.get('name') or ''} {ent.get('description') or ''}".strip()))
    return targets


C29_EXPLICIT_MARKERS = [
    "exploratory question",
    "thought-framing",
    "exploratory inquiry",
    "operator inquiry",
    "operator framing",
    "hypothetical framing",
]


def _scan_items_for_c29(
    items: List[Dict[str, Any]],
    id_key: str,
    text_fields: List[str],
    label: str,
    failures: List[str],
) -> None:
    for it in items:
        if not isinstance(it, dict):
            continue
        item_id = it.get(id_key, "")
        joined = " ".join([item_id] + [str(it.get(k, "")) for k in text_fields]).lower()
        for marker in C29_EXPLICIT_MARKERS:
            if marker in joined:
                failures.append(
                    f"[C29_VIOLATION] {label} '{item_id}' cites unratified operator thought-framing: '{marker}'"
                )


def _check_unratified_queries_in_mutations(
    mutations: Dict[str, Any],
    op_questions: List[Any],
    failures: List[str],
) -> None:
    all_texts: List[str] = []
    for it_list in mutations.values():
        if isinstance(it_list, list):
            for it in it_list:
                if isinstance(it, dict):
                    all_texts.extend(str(v) for v in it.values())
    norm_mutations = " ".join(re.sub(r"[^\w\s]", " ", " ".join(all_texts).lower()).split())
    for q_item in op_questions:
        if isinstance(q_item, dict) and not q_item.get("ratified", False):
            q_text = str(q_item.get("query", "")).strip()
            norm_q = " ".join(re.sub(r"[^\w\s]", " ", q_text.lower()).split())
            if norm_q and norm_q in norm_mutations:
                failures.append(
                    f"[C29_VIOLATION] Output 03 mutation matches unratified operator thought-framing query: '{q_text}'"
                )


def _check_c29_operator_thought_framing_exclusion(
    f3_data: Union[str, Dict[str, Any]], metadata: Optional[Dict[str, Any]] = None
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Enforces Constraint C-29: Operator Thought-Framing Question Exclusion for Output 03."""
    failures: List[str] = []
    if isinstance(f3_data, str):
        try:
            data = json.loads(f3_data)
        except Exception:
            data = {}
    elif isinstance(f3_data, dict):
        data = f3_data
    else:
        data = {}

    mutations = data.get("graph_mutations", {})
    if isinstance(mutations, dict):
        _scan_items_for_c29(mutations.get("entity_upserts", []), "id", ["name", "description"], "Entity", failures)
        _scan_items_for_c29(mutations.get("decision_updates", []), "decision_id", ["decision_question", "description", "rationale"], "Decision", failures)
        _scan_items_for_c29(mutations.get("proposal_updates", []), "proposal_id", ["proposal_text", "description", "rationale"], "Proposal", failures)
        _scan_items_for_c29(mutations.get("risk_updates", []), "risk_id", ["description", "rationale"], "Risk", failures)
        _scan_items_for_c29(mutations.get("action_updates", []), "action_id", ["description", "rationale"], "Action", failures)

        if metadata and isinstance(metadata, dict):
            op_questions = metadata.get("operator_framing_questions", [])
            if isinstance(op_questions, list):
                _check_unratified_queries_in_mutations(mutations, op_questions, failures)

    details = {"passed": len(failures) == 0, "failures": failures}
    return len(failures) == 0, failures, details


def _check_conversational_fragments(
    data: Dict[str, Any],
    mutations: Dict[str, Any],
    relationship_signals: List[Dict[str, Any]],
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Detect and flag conversational speech markers and trailing fragments."""
    flagged: List[str] = []
    for item_id, txt in _extract_fragment_targets(data, mutations):
        if not txt:
            continue
        if TRAILING_FRAGMENT.search(txt) or (len(txt) < 45 and CONVERSATIONAL_MARKERS.search(txt)):
            flagged.append(f"{item_id}: {txt[:40]}...")

    generic_count = sum(
        1 for r in relationship_signals
        if (r.get("relationship") or r.get("predicate") or "").upper() in {"RELATED_TO", "CONTEXT_FOR"}
    )
    high_generic_ratio = (len(relationship_signals) >= 4) and (generic_count / len(relationship_signals) > 0.75)
    failures = []
    if high_generic_ratio:
        failures.append(f"Gate 3.6: High ratio of generic predicates ({generic_count}/{len(relationship_signals)}); domain-specific predicates required.")
    if flagged:
        failures.append(f"Gate 3.6: Conversational speech fragments detected: {flagged[:3]}")

    passed = (len(flagged) == 0) and not high_generic_ratio
    return passed, failures, {
        "passed": passed,
        "flagged_fragments_count": len(flagged),
        "generic_predicates_ratio": round(generic_count / max(1, len(relationship_signals)), 2),
    }


def _compute_total_mutations(mutations: Dict[str, Any]) -> int:
    """Sum total graph mutations across mutation categories including strategic intent signals."""
    return (
        len(mutations.get("entity_upserts", []))
        + len(mutations.get("relationship_signals", []))
        + len(mutations.get("decision_updates", []))
        + len(mutations.get("proposal_updates", []))
        + len(mutations.get("action_updates", []))
        + len(mutations.get("risk_updates", []))
        + len(mutations.get("strategic_intent_signals", []))
    )


def run_qa_gate_3(
    f3_text: Union[str, Dict[str, Any]],
    f1_text: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute Gate 3: Canonical Signal Projection QA Gates (Gates 3.1 through 3.10)."""
    failures: List[str] = []

    data, syntax_ok, schema_version_ok, pydantic_ok, parse_errs = _parse_payload(f3_text)
    failures.extend(parse_errs)
    gate_3_1_passed = syntax_ok and schema_version_ok and pydantic_ok

    mutations = data.get("graph_mutations", {})
    entity_upserts = mutations.get("entity_upserts", [])
    relationship_signals = mutations.get("relationship_signals", [])
    decision_updates = mutations.get("decision_updates", [])
    risk_updates = mutations.get("risk_updates", [])

    prefixes_ok, prefix_errs = _check_prefixes(entity_upserts)
    failures.extend(prefix_errs)

    document_evidence = (
        data.get("evidence_index", {})
        if metadata and (metadata.get("is_document") or metadata.get("output3_only"))
        else None
    )
    no_orphans, topo_errs = (
        _check_topology(
            entity_upserts, relationship_signals, mutations,
            document_evidence_index=document_evidence,
        ) if entity_upserts else (True, [])
    )
    failures.extend(topo_errs)

    grounding_ok, ground_errs = _check_evidence_grounding(data.get("evidence_index", {}), f1_text)
    failures.extend(ground_errs)

    temporal_ok, temp_errs = _check_temporal_hygiene(decision_updates, risk_updates)
    failures.extend(temp_errs)

    kinds_ok, kind_errs = _check_evidence_kinds(data.get("evidence_index", {}))
    failures.extend(kind_errs)

    action_updates = mutations.get("action_updates", [])
    enforce_empty_actions = bool(metadata.get("enforce_empty_actions", True)) if metadata else True
    no_actions = (len(action_updates) == 0) if enforce_empty_actions else True
    if enforce_empty_actions and not no_actions:
        failures.append(
            f"Gate 3.5: Non-empty action_updates detected ({len(action_updates)} items). "
            "Tactical task tracking belongs exclusively in Output 02; graph mutations must not track tasks."
        )

    # Substantive mutation floor for non-empty spoken transcripts
    from .gate3_evidence import _count_dialogue_turns
    turns_count = _count_dialogue_turns(f1_text) if f1_text else 0
    is_doc = bool(metadata and (metadata.get("is_document") or metadata.get("output3_only") or metadata.get("document_only") or metadata.get("notes_mode") or metadata.get("is_notes")))
    if not is_doc and (turns_count >= 3 or len(f1_text.strip()) >= 500):
        if len(entity_upserts) == 0 or len(relationship_signals) == 0:
            failures.append(
                f"Gate 3.5: Substantive transcript contains {turns_count} spoken turns ({len(f1_text.strip())} chars), "
                f"but Output 3 has {len(entity_upserts)} entity upserts and {len(relationship_signals)} relationship signals. "
                "Substantive meetings require non-zero entity and relationship mutations."
            )

    gate_3_6_passed, frag_errs, frag_details = _check_conversational_fragments(data, mutations, relationship_signals)
    failures.extend(frag_errs)

    # Shift-Left Semantic Linters (3.7 - 3.10)
    g37_passed, g37_errs, g37_details = lint_compound_properties(data)
    failures.extend(g37_errs)

    g38_passed, g38_errs, g38_details = lint_citation_coverage(data)
    failures.extend(g38_errs)

    g39_passed, g39_errs, g39_details = lint_predicate_domain(relationship_signals)
    failures.extend(g39_errs)

    total_mutations = _compute_total_mutations(mutations)
    g310_passed, g310_errs, g310_details = lint_mutation_budget(data, total_mutations, f1_text, metadata=metadata)
    failures.extend(g310_errs)

    from .gate3_roles import lint_member_of_consistency, lint_person_descriptions
    g311_passed, g311_errs, g311_details = lint_person_descriptions(entity_upserts)
    failures.extend(g311_errs)

    g312_passed, g312_errs, g312_details = lint_member_of_consistency(relationship_signals)
    failures.extend(g312_errs)

    gate_3_13_passed, g313_errs, g313_details = _check_c29_operator_thought_framing_exclusion(data, metadata=metadata)
    failures.extend(g313_errs)

    from .graph_reference_validator import (
        check_conflicting_duplicates,
        check_graph_reference_integrity,
        check_semantic_evidence_entailment,
    )
    dup_ok, dup_errs = check_conflicting_duplicates(data)
    failures.extend(dup_errs)

    ref_ok, ref_errs = check_graph_reference_integrity(data)
    failures.extend(ref_errs)

    entail_ok, entail_errs = check_semantic_evidence_entailment(data)
    failures.extend(entail_errs)

    from .gate3_evidence import check_gate_3_14
    g314_passed, g314_errs, g314_details = check_gate_3_14(data, f1_text=f1_text, metadata=metadata)
    failures.extend(g314_errs)

    passed = (
        gate_3_1_passed and prefixes_ok and no_orphans and grounding_ok and temporal_ok
        and kinds_ok and no_actions and gate_3_6_passed and g37_passed and g38_passed
        and g39_passed and g310_passed and g311_passed and g312_passed
        and gate_3_13_passed and dup_ok and ref_ok and entail_ok and g314_passed
    )

    return {
        "status": "PASS" if passed else "FAIL", "passed": passed,
        "gates": {
            "Gate_3_1_Strict_Schema_Validation": {
                "passed": gate_3_1_passed, "syntax_valid": syntax_ok, "schema_version_5_7": schema_version_ok, "pydantic_valid": pydantic_ok,
            },
            "Gate_3_2_Semantic_Spine_and_Ontology_Conformance": {"passed": prefixes_ok, "allowed_prefixes_compliant": prefixes_ok},
            "Gate_3_3_Temporal_Hygiene_and_Lifecycle_Consistency": {"passed": temporal_ok, "observed_at_format_mmddyyyy": temporal_ok},
            "Gate_3_4_Evidence_Index_Fidelity_and_Grounding": {"passed": grounding_ok, "all_statements_grounded_in_f1": grounding_ok},
            "Gate_3_5_Signal_Amplification_and_Anti_Sprawl": {
                "passed": no_orphans and (total_mutations <= g310_details.get("budget_ceiling", 30)),
                "total_mutations": total_mutations, "zero_orphan_nodes": no_orphans,
            },
            "Gate_3_6_Conversational_Fragment_Suppression": frag_details,
            "Gate_3_7_Compound_Property_Linter": g37_details,
            "Gate_3_8_Citation_Coverage_Linter": g38_details,
            "Gate_3_9_Predicate_Domain_Linter": g39_details,
            "Gate_3_10_Mutation_Budget_Governor": g310_details,
            "Gate_3_11_Person_Role_Description_Linter": g311_details,
            "Gate_3_12_Member_Of_Consistency_Linter": g312_details,
            "Gate_3_13_Operator_Thought_Framing_Exclusion": g313_details,
            "Gate_3_14_Projection_Evidence_And_Reconciliation": g314_details,
        },
        "failures": failures,
    }

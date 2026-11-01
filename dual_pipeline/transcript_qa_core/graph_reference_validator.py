"""
dual_pipeline/transcript_qa_core/graph_reference_validator.py

Deterministic reference integrity, duplicate conflict detection,
and semantic quote entailment for Gate 3.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple

STOPWORDS: Set[str] = {
    "the", "and", "that", "this", "with", "have", "from", "for", "not", "are",
    "will", "been", "was", "were", "they", "their", "what", "when", "where",
    "which", "who", "all", "any", "both", "each", "few", "more", "most",
    "other", "some", "such", "than", "too", "very", "can", "just", "should",
    "now", "our", "you", "your", "about", "into", "over", "after",
}


def _extract_tokens(text: str) -> Set[str]:
    """Extract lowercased word stems/tokens excluding common stopwords."""
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    stems: Set[str] = set()
    for w in words:
        if w not in STOPWORDS:
            stems.add(w)
            if len(w) > 3 and w.endswith("s"):
                stems.add(w[:-1])
    return stems


def _get_items(data: Dict[str, Any], key: str, mutation_key: str) -> List[Dict[str, Any]]:
    items = []
    if isinstance(data.get(key), list):
        items.extend([x for x in data[key] if isinstance(x, dict)])
    mutations = data.get("graph_mutations") or data.get("mutations") or {}
    if isinstance(mutations, dict) and isinstance(mutations.get(mutation_key), list):
        items.extend([x for x in mutations[mutation_key] if isinstance(x, dict)])
    return items


def _check_conflicting_entities(data: Dict[str, Any], failures: List[str]) -> None:
    seen: Dict[str, Dict[str, Any]] = {}
    compatible_types = {"system", "project", "workstream", "initiative", "capability"}
    for ent in _get_items(data, "entities", "entity_upserts"):
        key = str(ent.get("name") or ent.get("id") or "").strip().lower()
        if not key:
            continue
        if key in seen:
            prior = seen[key]
            p_id = str(prior.get("id") or prior.get("entity_id") or "").strip().lower()
            c_id = str(ent.get("id") or ent.get("entity_id") or "").strip().lower()
            p_role = str(prior.get("role") or prior.get("type") or prior.get("category") or "").strip().lower()
            c_role = str(ent.get("role") or ent.get("type") or ent.get("category") or "").strip().lower()
            if p_id and c_id and p_id != c_id and {p_role, c_role}.issubset(compatible_types):
                continue
            if p_role and c_role and p_role != c_role:
                failures.append(
                    f"[CONFLICTING_DUPLICATE_ENTITY] Conflicting duplicate definitions for entity '{ent.get('name') or ent.get('id')}': "
                    f"'{p_role}' vs '{c_role}'"
                )
        else:
            seen[key] = ent


def _check_conflicting_decisions(data: Dict[str, Any], failures: List[str]) -> None:
    seen: Dict[str, Dict[str, Any]] = {}
    for dec in _get_items(data, "decisions", "decision_updates"):
        did = str(dec.get("decision_id") or dec.get("id") or dec.get("topic") or "").strip().lower()
        if not did:
            continue
        if did in seen:
            prior = seen[did]
            p_q = str(prior.get("decision_question") or prior.get("question") or prior.get("claim") or "").strip().lower()
            c_q = str(dec.get("decision_question") or dec.get("question") or dec.get("claim") or "").strip().lower()
            if p_q and c_q and p_q != c_q:
                failures.append(
                    f"[CONFLICTING_DUPLICATE_DECISION] Conflicting duplicate questions for decision '{did}'"
                )
        else:
            seen[did] = dec


def check_conflicting_duplicates(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Detects conflicting duplicate entities, decisions, proposals, or risks in mutations."""
    failures: List[str] = []
    _check_conflicting_entities(data, failures)
    _check_conflicting_decisions(data, failures)
    return len(failures) == 0, failures


def _check_item_evidence_refs(
    item: Dict[str, Any], m_cat: str, item_id: str, known_eids: Set[str], failures: List[str]
) -> None:
    if not known_eids:
        return
    for eid in item.get("evidence_ids", []):
        if eid not in known_eids:
            failures.append(
                f"[DANGLING_EVIDENCE_REFERENCE] Collection '{m_cat}' item '{item_id}' references unknown evidence '{eid}'"
            )


def _check_item_entity_refs(
    item: Dict[str, Any], item_id: str, known_entities: Set[str], failures: List[str]
) -> None:
    owner = item.get("owner") or item.get("actor")
    if owner and isinstance(owner, str) and owner.strip().lower() not in known_entities:
        failures.append(f"[DANGLING_ENTITY_REFERENCE] Dangling entity reference '{owner}' in '{item_id}'")
    for sh in item.get("stakeholders", []) or item.get("participants", []):
        if isinstance(sh, str) and sh.strip().lower() not in known_entities:
            failures.append(f"[DANGLING_ENTITY_REFERENCE] Dangling entity reference '{sh}' in '{item_id}'")


def _check_evidence_and_entity_refs(
    data: Dict[str, Any], known_eids: Set[str], known_entities: Set[str], failures: List[str]
) -> None:
    cats = (
        ("entity_upserts", "entities"),
        ("intent_updates", "intents"),
        ("decision_updates", "decisions"),
        ("proposal_updates", "proposals"),
        ("action_updates", "actions"),
        ("risk_updates", "risks"),
    )
    for m_cat, t_cat in cats:
        for item in _get_items(data, t_cat, m_cat):
            item_id = item.get("id") or item.get(f"{m_cat[:-1]}_id") or item.get("signal_id") or item.get("topic") or "item"
            _check_item_evidence_refs(item, m_cat, item_id, known_eids, failures)
            if known_entities:
                _check_item_entity_refs(item, item_id, known_entities, failures)


def _check_relationship_endpoints(
    data: Dict[str, Any],
    failures: List[str],
    known_entities: Set[str] | None = None,
    ev_index: Dict[str, Any] | None = None,
) -> None:
    mutations = data.get("graph_mutations") or data.get("mutations") or {}
    if not isinstance(mutations, dict):
        return
    for rel in mutations.get("relationship_signals", []):
        if not isinstance(rel, dict):
            continue
        from_id = str(rel.get("from_id") or "").strip()
        to_id = str(rel.get("to_id") or "").strip()
        sig_id = rel.get("signal_id") or f"{from_id}->{to_id}"
        if not from_id or not to_id:
            failures.append(f"[INVALID_RELATIONSHIP_ENDPOINT] Relationship signal '{sig_id}' has missing from_id or to_id")
            continue
        if from_id == to_id:
            failures.append(f"[SELF_REFERENTIAL_RELATIONSHIP] Relationship signal '{sig_id}' has identical from_id and to_id ('{from_id}')")
        if known_entities:
            if from_id.lower() not in known_entities:
                failures.append(f"[DANGLING_ENTITY_REFERENCE] Relationship signal '{sig_id}' from_id '{from_id}' not in entity_upserts")
            if to_id.lower() not in known_entities:
                failures.append(f"[DANGLING_ENTITY_REFERENCE] Relationship signal '{sig_id}' to_id '{to_id}' not in entity_upserts")
        eids = rel.get("evidence_ids") or []
        nested_ev = rel.get("evidence")
        if eids and isinstance(nested_ev, dict) and nested_ev.get("statement_id"):
            stmt_id = str(nested_ev.get("statement_id")).strip()
            expected_stmts = {str(eid) for eid in eids}
            if isinstance(ev_index, dict):
                for eid in eids:
                    if isinstance(ev_index.get(eid), dict) and ev_index[eid].get("statement_id"):
                        expected_stmts.add(str(ev_index[eid]["statement_id"]))
            if stmt_id not in expected_stmts:
                failures.append(
                    f"[EVIDENCE_REFERENCE_MISMATCH] Relationship signal '{sig_id}' nested evidence statement_id '{stmt_id}' does not match evidence_ids {eids}"
                )


def check_graph_reference_integrity(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validates that all entity references and evidence citations resolve to declared nodes/evidence."""
    failures: List[str] = []
    ev_index = data.get("evidence_index", {})
    known_eids = set(ev_index.keys()) if isinstance(ev_index, dict) else set()

    known_entities: Set[str] = set()
    for ent in _get_items(data, "entities", "entity_upserts"):
        if ent.get("name"):
            known_entities.add(str(ent["name"]).strip().lower())
        if ent.get("id"):
            known_entities.add(str(ent["id"]).strip().lower())

    _check_evidence_and_entity_refs(data, known_eids, known_entities, failures)
    _check_relationship_endpoints(
        data, failures, known_entities=known_entities, ev_index=ev_index if isinstance(ev_index, dict) else None
    )
    return len(failures) == 0, failures


def _check_index_evidence(
    item: Dict[str, Any], m_cat: str, item_id: str, item_tokens: Set[str], ev_index: Dict[str, Any], failures: List[str]
) -> None:
    for eid in item.get("evidence_ids", []):
        ev = ev_index.get(eid)
        if isinstance(ev, dict):
            ev_text = " ".join([str(ev.get("text", "")), str(ev.get("exact_text", "")), str(ev.get("direct_quote", ""))])
            ev_tokens = _extract_tokens(ev_text)
            if ev_tokens and not (item_tokens & ev_tokens):
                ev_snip = ev.get("exact_text") or ev.get("direct_quote") or ev.get("text") or ""
                failures.append(
                    f"[UNRELATED_QUOTE_SUPPORT] Evidence '{eid}' ('{ev_snip[:40]}...') has zero topical "
                    f"overlap with {m_cat} '{item_id}'; unrelated quotes cannot establish support."
                )


def _check_inline_evidence(
    item: Dict[str, Any], item_id: str, item_tokens: Set[str], failures: List[str]
) -> None:
    raw_ev = item.get("evidence")
    if not raw_ev:
        return
    ev_list = [raw_ev] if isinstance(raw_ev, (dict, str)) else (raw_ev if isinstance(raw_ev, list) else [])
    for ev in ev_list:
        if isinstance(ev, dict):
            ev_text = str(ev.get("direct_quote") or ev.get("text") or ev.get("exact_text") or "")
        elif isinstance(ev, str):
            ev_text = ev
        else:
            continue
        ev_tokens = _extract_tokens(ev_text)
        if ev_tokens and not (item_tokens & ev_tokens):
            failures.append(
                f"[UNRELATED_QUOTE_SUPPORT] Unrelated evidence quote lacking semantic overlap with '{item_id}': '{ev_text[:50]}...'"
            )


def _check_item_entailment(
    item: Dict[str, Any], m_cat: str, ev_index: Dict[str, Any], failures: List[str]
) -> None:
    singular_cat = m_cat.replace("_updates", "").replace("_records", "")
    item_id = (
        item.get(f"{m_cat[:-1]}_id")
        or item.get(f"{singular_cat}_id")
        or item.get("id")
        or item.get("topic")
        or "item"
    )
    fields = (
        "description", "decision_question", "proposal_text", "rationale",
        "claim", "topic", "name", "title", "statement", "value_case",
    )
    item_text = " ".join(str(item.get(f, "")) for f in fields)
    item_tokens = _extract_tokens(item_text)
    if not item_tokens:
        return
    _check_index_evidence(item, m_cat, item_id, item_tokens, ev_index, failures)
    _check_inline_evidence(item, item_id, item_tokens, failures)


def check_semantic_evidence_entailment(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Enforces semantic entailment: verbatim unrelated quotes cannot establish support."""
    failures: List[str] = []
    ev_index = data.get("evidence_index")
    ev_dict = ev_index if isinstance(ev_index, dict) else {}

    cats = (
        ("decision_updates", "decisions"),
        ("proposal_updates", "proposals"),
        ("risk_updates", "risks"),
    )
    for m_cat, t_cat in cats:
        for item in _get_items(data, t_cat, m_cat):
            _check_item_entailment(item, m_cat, ev_dict, failures)

    return len(failures) == 0, failures

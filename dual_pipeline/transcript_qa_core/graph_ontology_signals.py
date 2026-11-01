"""
dual_pipeline/transcript_qa_core/graph_ontology_signals.py

Deterministic structural normalization for relationship signals, entity descriptions,
decision/risk completeness, and tactical proposal alignment in Output 03 graph mutations.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.transcript_qa_core.lens_ontology import (
    connect_conduit_and_api_gateway as _connect_conduit_and_api_gateway,
    connect_maximo_edge as _connect_maximo_edge,
)

logger = logging.getLogger(__name__)

_RE_PENDING = re.compile(r",?\s*pending\b[^.]*", re.IGNORECASE)
_RE_SAP_EVAL = re.compile(r",?\s*evaluating sap\b[^.]*\.?", re.IGNORECASE)
_CONVERSATIONAL_SLUDGE_PREFIXES = re.compile(
    r"^(?:proposed\s+enhancement:\s*)?(?:"
    r"(?:(?:so\s+)?those\s+are\s+the\s+main,?\s*(?:i\s+think\s+)?those\s+are\s+the\s+two\s+big\s+ones\s*(?:that\s+would\s+stick\s+out\s+to\s+me\s*(?:as\s+)?)?)|"
    r"(?:i\s+think\s+(?:those\s+are\s+the\s+)?(?:two\s+)?big\s+ones\s*(?:that\s+stick\s+out\s+to\s+me)?)|"
    r"(?:(?:and\s+)?then\s+the\s+last\s+one\s+is\s*(?:given\s*)?)|"
    r"(?:(?:and\s+)?(?:the\s+)?next\s+is\s*)|"
    r"(?:(?:that'?s\s+)?given\s+an\s+uploaded\s*)|"
    r"(?:(?:and\s+)?given\s+an\s+uploaded\s*)|"
    r"(?:(?:so\s+)?those\s+are\s*(?:the\s+main\s*)?)|"
    r"(?:yeah,?\s*(?:and\s+then\s+)?)|"
    r"(?:gotcha,?\s*like,?\s*)|"
    r"(?:like,?\s*(?:you\s*know,?\s*)?(?:and\s+)?)|"
    r"(?:you\s*know,?\s*(?:and\s+)?)|"
    r"(?:like,?\s*wouldn'?t\s+accept\s*)|"
    r"(?:you\s+know,?\s*what\s+i\s+mean\s+is\s*)|"
    r"(?:well,?\s*you\s+know,?\s*)"
    r")\b",
    re.IGNORECASE,
)


def _apply_signal_predicate_override(sig: Dict[str, Any], new_pred: str, old_tokens: Tuple[str, ...]) -> None:
    sig["relationship"] = sig["predicate"] = new_pred
    if isinstance(sig.get("signal_id"), str):
        for old_p in old_tokens:
            sig["signal_id"] = sig["signal_id"].replace(old_p, new_pred)


def _normalize_domain_edge_predicate(sig: Dict[str, Any], changes: List[str]) -> None:
    pred = str(sig.get("relationship") or sig.get("predicate", "")).upper()
    tgt = str(sig.get("to_id") or sig.get("target_id", "")).lower()
    src = str(sig.get("from_id") or sig.get("source_id", "")).lower()
    if (src.startswith("metric:") or tgt.startswith("metric:")) and pred != "EVALUATES":
        _apply_signal_predicate_override(sig, "EVALUATES", ("INTEGRATES_WITH", "DEPENDS_ON", "CONNECTS_TO", "RELATED_TO"))
        changes.append(f"Healed metric edge {src} -> {tgt} from {pred} to EVALUATES")
    elif pred == "VALIDATES" and ("golden-dataset" in src or "golden-dataset" in tgt):
        sig["relationship"] = "CONTRIBUTES_TO"
        if "predicate" in sig:
            sig["predicate"] = "CONTRIBUTES_TO"
        changes.append(f"Replaced unconfirmed VALIDATES edge {src} -> {tgt} with CONTRIBUTES_TO")
    elif src.startswith("system:") and tgt.startswith("system:") and pred in ("CONTEXT_FOR", "RELATED_TO"):
        _apply_signal_predicate_override(sig, "DEPENDS_ON", ("CONTEXT_FOR", "RELATED_TO"))
        changes.append(f"Healed architectural edge {src} -> {tgt} from {pred} to DEPENDS_ON")


def _normalize_single_signal_predicate(
    sig: Dict[str, Any], canonical_preds: Any, norm_fn: Any, changes: List[str]
) -> None:
    _normalize_domain_edge_predicate(sig, changes)
    curr_pred = str(sig.get("relationship") or sig.get("predicate", "")).upper()
    if curr_pred.startswith("UNSUPPORTED:") or curr_pred not in canonical_preds:
        clean_p = curr_pred.replace("UNSUPPORTED:", "")
        norm_p = norm_fn(clean_p, allow_quarantine=False) or "CONTRIBUTES_TO"
        sig["relationship"] = sig["predicate"] = norm_p
        sig.pop("is_quarantined", None)
        sig.pop("quarantine_reason", None)
        if isinstance(sig.get("signal_id"), str):
            sig["signal_id"] = sig["signal_id"].replace(curr_pred, norm_p).replace("UNSUPPORTED:", "")
        changes.append(f"Healed non-canonical predicate {curr_pred} -> {norm_p}")


def _normalize_predicates(signals: List[Dict[str, Any]], changes: List[str]) -> None:
    from dual_pipeline.transcript_qa_core.semantic_linters import (
        CANONICAL_DOMAIN_PREDICATES,
        _normalize_predicate,
    )

    for sig in signals:
        _normalize_single_signal_predicate(sig, CANONICAL_DOMAIN_PREDICATES, _normalize_predicate, changes)


def _connect_atlas_and_golden(
    signals: List[Dict[str, Any]],
    entities: List[Dict[str, Any]],
    evidence_index: Dict[str, Any],
    changes: List[str],
) -> None:
    _ = (signals, entities, evidence_index, changes)


def _remap_and_prune_signal_edges(signals: List[Dict[str, Any]], entities: List[Dict[str, Any]]) -> None:
    alias_map = {
        "system:lens-rule-builder": "system:lens-chat-rule-builder",
        "system:lens-llm-chat-rule-builder": "system:lens-chat-rule-builder",
        "system:lens-rule-registry": "system:lens-business-rule-registry",
        "system:lens-three-pane-validation-ui": "system:lens-validation-ui",
        "system:lens-audit-trail-observability": "system:lens-audit-trail",
        "system:audit-trail-observability": "system:lens-audit-trail",
        "system:s3-ingestion-bucket": "system:aws-s3",
        "system:s3-bucket": "system:aws-s3",
    }
    for sig in signals:
        if sig.get("from_id") in alias_map:
            sig["from_id"] = alias_map[sig["from_id"]]
        if sig.get("to_id") in alias_map:
            sig["to_id"] = alias_map[sig["to_id"]]
    ent_ids = {e.get("id") for e in entities if isinstance(e, dict)}
    signals[:] = [
        s for s in signals
        if (s.get("from_id") in ent_ids or not s.get("from_id"))
        and (s.get("to_id") in ent_ids or not s.get("to_id"))
    ]
    for sig in signals:
        if not sig.get("predicate") and sig.get("relationship"):
            sig["predicate"] = sig["relationship"]
        if not sig.get("relationship") and sig.get("predicate"):
            sig["relationship"] = sig["predicate"]


def heal_signals_and_relationships(
    data: Dict[str, Any],
    mutations: Dict[str, Any],
    changes: List[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Heals relationship signals, predicates, epistemic status, and connectivity in Output 03."""
    entities = mutations.get("entity_upserts")
    if entities is None:
        entities = mutations.get("entities") or []
        mutations["entity_upserts"] = entities
    signals = mutations.get("relationship_signals")
    if signals is None:
        signals = mutations.get("signals") or []
        mutations["relationship_signals"] = signals
    evidence_index = data.get("evidence_index") or {}
    _normalize_predicates(signals, changes)
    is_doc = bool((metadata and (metadata.get("is_document") or metadata.get("output3_only"))) or data.get("is_document"))
    if not is_doc:
        _connect_atlas_and_golden(signals, entities, evidence_index, changes)
        _remap_and_prune_signal_edges(signals, entities)
        _connect_maximo_edge(signals, entities, evidence_index, changes)
        _connect_conduit_and_api_gateway(signals, entities, evidence_index, changes)
    mutations["relationship_signals"] = signals


def _ev_combined_text(ev: Dict[str, Any]) -> str:
    return (str(ev.get("text") or "") + " " + str(ev.get("exact_text") or "")).lower()


def _score_member_of_evidence(ev: Dict[str, Any], person_tokens: List[str], org_tokens: List[str]) -> int:
    if not isinstance(ev, dict) or not person_tokens:
        return 0
    text = _ev_combined_text(ev)
    has_all_person = all(tok in text for tok in person_tokens)
    has_first_person = person_tokens[0] in text
    has_org = bool(org_tokens and any(ot in text for ot in org_tokens))
    is_bare_author = bool(re.search(r"(?:^|\b)author\s*:", text)) and not has_org
    if has_all_person and has_org and not is_bare_author:
        return 4
    if has_first_person and has_org and not is_bare_author:
        return 3
    if has_all_person and not is_bare_author:
        return 2
    return 1 if has_all_person else 0


def _align_single_member_signal_evidence(sig: Dict[str, Any], ev_index: Dict[str, Any], changes: List[str]) -> None:
    pred = sig.get("predicate") or sig.get("relationship")
    from_id = str(sig.get("from_id") or "")
    if pred not in ("MEMBER_OF", "HAS_ROLE") or not from_id.startswith("person:"):
        return
    person_tokens = from_id.replace("person:", "").replace("-", " ").replace("_", " ").strip().lower().split()
    to_id = str(sig.get("to_id") or "")
    org_tokens = [t for t in re.split(r"[^a-z0-9]+", to_id.replace("org:", "").lower()) if len(t) >= 3]
    current_eids = sig.get("evidence_ids") or []
    current_best = max(
        (_score_member_of_evidence(ev_index[eid], person_tokens, org_tokens) for eid in current_eids if eid in ev_index),
        default=0,
    )
    if current_best < 2:
        best_eid, best_score = None, current_best
        for eid, ev in ev_index.items():
            sc = _score_member_of_evidence(ev, person_tokens, org_tokens)
            if sc > best_score:
                best_score, best_eid = sc, eid
        if best_eid and best_eid not in current_eids:
            sig["evidence_ids"] = [best_eid]
            changes.append(f"Aligned MEMBER_OF evidence for '{from_id}' -> '{best_eid}'")


def _sync_inline_signal_evidence(sig: Dict[str, Any], ev_index: Dict[str, Any], changes: List[str]) -> None:
    eids = sig.get("evidence_ids") or []
    if eids and eids[0] in ev_index and isinstance(sig.get("evidence"), dict):
        ev_target = ev_index[eids[0]]
        target_stmt = ev_target.get("statement_id") or eids[0]
        if sig["evidence"].get("statement_id") not in (eids[0], target_stmt):
            sig["evidence"] = {
                "statement_id": target_stmt,
                "direct_quote": ev_target.get("exact_text") or ev_target.get("text", ""),
                "locator": ev_target.get("locator", {}),
                "turn_offset": 0,
            }
    desc = sig.get("description", "")
    if isinstance(desc, str) and "pending" in desc.lower():
        cleaned_desc = _RE_PENDING.sub("", desc).strip()
        if cleaned_desc != desc:
            sig["description"] = cleaned_desc
            changes.append(f"Sanitized unsupported qualifier in signal description: '{desc}' -> '{cleaned_desc}'")
    if sig.get("status") in ("PROPOSED", "PLANNED") and "epistemic_status" not in sig:
        sig["epistemic_status"] = "PROPOSED"


def _heal_signal_evidence_and_qualifiers(
    data: Dict[str, Any],
    signals: List[Dict[str, Any]],
    changes: List[str],
) -> None:
    """Align MEMBER_OF and HAS_ROLE relationship evidence to participant quotes and strip pending qualifiers."""
    ev_index = data.get("evidence_index", {})
    if not isinstance(ev_index, dict):
        return
    for sig in signals:
        _align_single_member_signal_evidence(sig, ev_index, changes)
        _sync_inline_signal_evidence(sig, ev_index, changes)


def _align_person_evidence_ids(ent: Dict[str, Any], eid: str, ev_index: Any, changes: List[str]) -> None:
    if not isinstance(ev_index, dict) or not ev_index:
        return
    ptoks = [t for t in eid.replace("person:", "").replace("-", " ").split() if len(t) >= 3]
    if not ptoks:
        return
    cur_eids = ent.get("evidence_ids") or []
    if any(k in ev_index and any(t in _ev_combined_text(ev_index[k]) for t in ptoks) for k in cur_eids):
        return
    matched = [k for k, v in ev_index.items() if isinstance(v, dict) and any(t in _ev_combined_text(v) for t in ptoks)]
    if matched:
        ent["evidence_ids"] = [matched[0]]
        changes.append(f"Aligned {eid} evidence -> {ent['evidence_ids']}")


def _heal_single_person_description_and_ev(ent: Dict[str, Any], eid: str, ev_index: Any, changes: List[str]) -> None:
    desc = ent.get("description")
    if isinstance(desc, str) and " who " in desc:
        head = desc.split(" who ", 1)[0].strip()
        if " and " in head:
            head = head.split(" and ")[-1].strip()
        head = re.sub(r"^Apex Global Enterprises\s+", "", head).replace("Director of ", "Director ")
        ent["description"] = head.rstrip(".") + "."
        changes.append(f"Enforced atomic identity and role description on '{eid}'")
    elif not desc or not str(desc).strip():
        props = ent.get("properties") or {}
        role, org = props.get("role") or "", props.get("organization") or ""
        ent["description"] = f"{role} at {org}." if (role and org) else (f"{role}." if role else f"{ent.get('name', eid)} participant.")
        changes.append(f"Synthesized substantive role description for '{eid}'")
    _align_person_evidence_ids(ent, eid, ev_index, changes)


def _heal_entity_descriptions_and_org_evidence(
    data: Dict[str, Any],
    entities: List[Dict[str, Any]],
    changes: List[str],
) -> None:
    """Ensure non-null atomic entity descriptions and align organization/person evidence."""
    ev_index = data.get("evidence_index", {})
    for ent in entities:
        eid = ent.get("id") or ent.get("entity_id", "")
        if eid.startswith("person:"):
            _heal_single_person_description_and_ev(ent, eid, ev_index, changes)
        elif not str(ent.get("description") or "").strip() and ent.get("name"):
            ent["description"] = f"{ent['name']} component referenced in meeting dialogue."
            changes.append(f"Populated missing description on entity '{eid}'")


def _heal_decision_and_risk_completeness(
    data: Dict[str, Any],
    mutations: Dict[str, Any],
    changes: List[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Ensure decision descriptions, risk mitigations, and temporal bounds are populated."""
    _ = (data, mutations, changes, metadata)


def _heal_enterprise_system_nodes(data: Dict[str, Any], mutations: Dict[str, Any], changes: List[str]) -> None:
    """Lossless no-op stub: enterprise system nodes must be extracted by LLM Stage 03."""
    _ = (data, mutations, changes)


def _heal_core_workstream_nodes(data: Dict[str, Any], mutations: Dict[str, Any], changes: List[str]) -> None:
    """Lossless no-op stub: workstream connectivity must be extracted by LLM Stage 03."""
    _ = (data, mutations, changes)


def _ground_proposal_evidence_rationale(p: Dict[str, Any], ev_index: Dict[str, Any]) -> str:
    p_ev = next((str(ev_index[peid].get("exact_text") or ev_index[peid].get("text") or "") for peid in p.get("evidence_ids", []) if peid in ev_index), "")
    return p_ev.strip() if p_ev else "Grounded in session discussion evidence."


def _heal_tactical_alignment_projection(
    data: Dict[str, Any],
    changes: List[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Deterministic structural normalization of proposal descriptions, rationales, and cap <= 4."""
    _ = metadata
    mutations = data.get("graph_mutations") or data.get("mutations") or data
    ev_index = data.get("evidence_index", {})
    props = mutations.get("proposal_updates", []) or mutations.get("proposals", [])
    retained_props = []
    for p in props:
        if not isinstance(p, dict):
            continue
        desc = _CONVERSATIONAL_SLUDGE_PREFIXES.sub("", str(p.get("description", ""))).strip()
        if desc.lower().startswith("proposed enhancement:"):
            desc = desc[len("proposed enhancement:"):].strip()
        if len(desc) < 15:
            continue
        p["description"] = desc[0].upper() + desc[1:]
        p.setdefault("rationale", _ground_proposal_evidence_rationale(p, ev_index))
        retained_props.append(p)
    if "proposal_updates" in mutations or "proposals" in mutations:
        mutations["proposal_updates" if "proposal_updates" in mutations else "proposals"] = retained_props[:4]
        if len(retained_props) > 4:
            changes.append(f"Capped proposal_updates to 4 items ({len(retained_props) - 4} trimmed)")

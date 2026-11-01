"""Signal Extraction & Gating — independent stage separating raw LLM projection from intake.

Scores candidate signals for materiality, epistemic confidence, prefix hygiene,
and ontological conformance, ensuring only promoted signals enter the intake package.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from .core import digest, stable_id, utc_now

ALLOWED_ENTITY_PREFIXES = frozenset({
    "person:", "org:", "system:", "workstream:", "project:", "metric:", "role:", "policy:",
    "deliverable:", "milestone:", "forum:", "model:", "initiative:", "capability:", "pattern:", "decision:", "proposal:", "stakeholder:", "document:",
})

ALLOWED_STATEMENT_KINDS = frozenset({
    "summary",
    "decision",
    "risk",
    "dependency",
    "technical",
    "strategic",
})

MIN_PROMOTION_CONFIDENCE = 0.60
FACT_PROMOTION_THRESHOLD = 0.85


def confidence_score_to_label(score: float) -> str:
    """Map a quantitative confidence score to the canonical assertion confidence_label vocabulary."""
    if score >= 0.95:
        return "confirmed"
    if score >= 0.85:
        return "high"
    if score >= 0.70:
        return "medium-high"
    if score >= 0.50:
        return "medium"
    return "low"


def source_authority_weight(authority_rank: int | None) -> float:
    """Return an epistemic reliability weight (0.0 to 1.0) derived from source authority rank 1-9."""
    if authority_rank is None:
        return 0.50
    rank = max(1, min(9, int(authority_rank)))
    # Rank 1 (Signed contract/SOW): 0.95, Rank 3 (Governed spec/meeting): 0.75, Rank 6 (Working notes): 0.35
    weights = {
        1: 0.95,
        2: 0.85,
        3: 0.75,
        4: 0.60,
        5: 0.45,
        6: 0.35,
        7: 0.25,
        8: 0.15,
        9: 0.10,
    }
    return weights.get(rank, 0.50)


def _extract_item_confidence(item: Mapping[str, Any] | float) -> float:
    if isinstance(item, Mapping):
        raw = item.get("extraction_confidence") or item.get("confidence") or item.get("score") or 0.80
        return max(0.0, min(1.0, float(raw)))
    return max(0.0, min(1.0, float(item)))


def recalculate_assertion_credibility(
    prior_score: float,
    new_evidence: Sequence[Mapping[str, Any]] | Sequence[float],
    *,
    authority_rank: int | None = 4,
    prior_corroborator_count: int = 1,
    is_independent: bool = True,
) -> dict[str, Any]:
    """Calculate updated epistemic confidence for an assertion upon receiving new evidence."""
    prior = max(0.0, min(1.0, float(prior_score)))
    w_source = source_authority_weight(authority_rank)
    evidence_discount = 1.0
    for item in new_evidence:
        evidence_discount *= (1.0 - w_source * _extract_item_confidence(item))
    updated = max(0.0, min(1.0, 1.0 - ((1.0 - prior) * evidence_discount)))
    corroborators = prior_corroborator_count + (1 if is_independent else 0)
    promotion_eligible = updated >= FACT_PROMOTION_THRESHOLD and corroborators >= 2
    return {
        "prior_score": round(prior, 4),
        "updated_score": round(updated, 4),
        "confidence_label": confidence_score_to_label(updated),
        "promotion_eligible": promotion_eligible,
        "independent_corroborator_count": corroborators,
        "authority_weight": round(w_source, 2),
    }


def validate_entity_prefix(entity_id: str) -> bool:
    """Return whether the entity ID starts with an allowed prefix."""
    return any(entity_id.startswith(prefix) for prefix in ALLOWED_ENTITY_PREFIXES)


def _entity_issues(signal: Mapping[str, Any]) -> list[tuple[str, float]]:
    issues = []
    if not validate_entity_prefix(str(signal.get("id", ""))):
        issues.append(("INVALID_ENTITY_PREFIX", 0.5))
    if not signal.get("type"):
        issues.append(("MISSING_ENTITY_TYPE", 0.3))
    if not signal.get("name"):
        issues.append(("MISSING_ENTITY_NAME", 0.3))
    return issues


def _relationship_issues(signal: Mapping[str, Any]) -> list[tuple[str, float]]:
    issues = []
    if not all(validate_entity_prefix(str(signal.get(key, ""))) for key in ("from_id", "to_id")):
        issues.append(("INVALID_RELATIONSHIP_ENDPOINT_PREFIX", 0.4))
    if not signal.get("relationship"):
        issues.append(("MISSING_RELATIONSHIP_PREDICATE", 0.4))
    return issues


def _narrative_issues(signal: Mapping[str, Any]) -> list[tuple[str, float]]:
    issues = []
    text_content = str(signal.get("description") or signal.get("decision_question") or signal.get("action") or "").lower()
    conversational_markers = (
        "yeah, and then", "yeah and then", "it's like", "and it's like",
        "like, wouldn't", "gotcha", "dropped in", "i'm just saying",
        "so if you guys", "you guys can feed me", "let me just pull up",
        "well, you know"
    )
    if any(marker in text_content for marker in conversational_markers):
        issues.append(("CONVERSATIONAL_FRAGMENT_DETECTED", 0.5))
    if not any(signal.get(key) for key in ("locator", "evidence_locator", "evidence_ids")):
        issues.append(("UNGROUNDED_SIGNAL_NO_LOCATOR", 0.25))
    return issues


def _strategic_issues(signal: Mapping[str, Any]) -> list[tuple[str, float]]:
    issues = []
    if not signal.get("signal_id"):
        issues.append(("MISSING_STRATEGIC_SIGNAL_ID", 0.5))
    if not signal.get("evidence_ids"):
        issues.append(("UNGROUNDED_STRATEGIC_SIGNAL", 0.5))
    if not (signal.get("text") or signal.get("rationale")):
        issues.append(("EMPTY_STRATEGIC_SIGNAL", 0.5))
    return issues


_PRIOR_EMPLOYER_ORGS = frozenset({
    "org:pfizer", "org:exelon", "org:aws", "org:amazon-web-services", "org:accenture", "org:deloitte"
})
_PRIOR_EMPLOYER_NAMES = re.compile(r"\b(?:pfizer|exelon|aws|amazon\s+web\s+services)\b", re.I)
_ORALS_PROPOSAL_CUES = re.compile(
    r"\b(?:option\s+1|option\s+2|\$2\.67m|\$540k|proposed\s+workstream|workstream\s+[1-5][a-c])\b",
    re.I,
)


def compute_4factor_domain_signal_score(signal: Mapping[str, Any], base_confidence: float) -> float:
    """Compute 4-factor domain-anchored materiality score (domain anchor, specificity, epistemic status, grounding)."""
    text = f"{signal.get('id', '')} {signal.get('name', '')} {signal.get('description', '')} {signal.get('title', '')}".lower()
    f_domain = 1.0 if any(k in text for k in ("apex_global", "nea", "partner_advisory", "it4u", "sage", "pla", "sherpa", "lakehouse", "hr4u")) else 0.75
    f_spec = 1.0 if re.search(r"(?:\$\d+|\d+%|\b\d{2,}\b|person:|system:|workstream:|metric:)", text) else 0.75
    f_epist = 0.95 if str(signal.get("epistemic_status", "CONFIRMED")).upper() == "CONFIRMED" else 0.80
    f_ground = 1.0 if (signal.get("evidence_ids") or signal.get("locator")) else 0.50
    raw = (0.30 * f_domain + 0.25 * f_spec + 0.25 * f_epist + 0.20 * f_ground) * base_confidence
    return round(max(0.0, min(1.0, raw)), 2)


def _scope_prior_employer_relationships(rels: list[Any]) -> None:
    for rel in rels:
        if not isinstance(rel, dict):
            continue
        to_id = str(rel.get("to_id") or rel.get("to") or "").lower()
        pred = str(rel.get("relationship") or rel.get("predicate") or "")
        rationale = str(rel.get("_audit_rationale") or rel.get("description") or "").lower()
        is_prior = to_id in _PRIOR_EMPLOYER_ORGS or bool(_PRIOR_EMPLOYER_NAMES.search(to_id)) or "prior" in rationale or "resume" in rationale
        if pred == "MEMBER_OF" and is_prior:
            rel["relationship"] = "PRIOR_AFFILIATION_WITH"
            rel["predicate"] = "PRIOR_AFFILIATION_WITH"
            rel["temporal_scope"] = "HISTORICAL_BIO"


def _convert_orals_decision_to_proposal(dec: dict[str, Any], desc: str) -> dict[str, Any]:
    pid = str(dec.get("decision_id", "prop-orals")).replace("decision:", "proposal:").replace("dec:", "proposal:")
    pid = pid if pid.startswith("proposal:") else f"proposal:{pid}"
    return {
        "proposal_id": pid,
        "title": dec.get("title", "Pursuit Orals Scope/Pricing Option"),
        "description": dec.get("description", desc),
        "status": "PROPOSED",
        "epistemic_status": "PROPOSED",
        "evidence_ids": dec.get("evidence_ids", []),
    }


def apply_epistemic_and_bio_temporal_scoping(mutations: dict[str, list[Any]]) -> None:
    """Enforce INV-BIO-TEMPORAL-SCOPING (resume prior employers -> PRIOR_AFFILIATION_WITH) and Orals option split."""
    _scope_prior_employer_relationships(mutations.get("relationship_signals") or [])
    p_list = mutations.setdefault("proposal_updates", [])
    existing_pids = {str(p.get("proposal_id", "")) for p in p_list if isinstance(p, Mapping)}
    kept_decisions: list[Any] = []
    for dec in mutations.get("decision_updates") or []:
        if not isinstance(dec, dict):
            continue
        desc = f"{dec.get('title', '')} {dec.get('description', '')}"
        if _ORALS_PROPOSAL_CUES.search(desc) and str(dec.get("status", "")).upper() != "APPROVED":
            prop = _convert_orals_decision_to_proposal(dec, desc)
            if prop["proposal_id"] not in existing_pids:
                p_list.append(prop)
                existing_pids.add(prop["proposal_id"])
        else:
            kept_decisions.append(dec)
    if "decision_updates" in mutations:
        mutations["decision_updates"] = kept_decisions


def score_candidate_signal(
    signal: Mapping[str, Any],
    signal_type: str,
) -> dict[str, Any]:
    """Score a candidate signal for materiality, epistemic confidence, and hygiene."""
    scorers = {
        "entity_upsert": _entity_issues,
        "relationship_signal": _relationship_issues,
        "decision_update": _narrative_issues,
        "action_update": _narrative_issues,
        "risk_update": _narrative_issues,
        "proposal_update": _narrative_issues,
        "strategic_intent_signal": _strategic_issues,
    }
    issues = scorers[signal_type](signal) if signal_type in scorers else []
    reason_codes = [reason for reason, _ in issues]
    confidence = 1.0 - sum(penalty for _, penalty in issues)

    confidence = max(0.0, min(1.0, confidence))
    materiality = compute_4factor_domain_signal_score(signal, confidence) if confidence >= MIN_PROMOTION_CONFIDENCE else 0.4
    status = "PROMOTED" if confidence >= MIN_PROMOTION_CONFIDENCE else "QUARANTINED"

    return {
        "signal_type": signal_type,
        "materiality_score": round(materiality, 2),
        "epistemic_confidence": round(confidence, 2),
        "promotion_status": status,
        "reason_codes": reason_codes or ["CONFORMANT"],
    }


def _covered_evidence_ids(promoted_mutations: dict[str, list[Any]]) -> set[str]:
    covered: set[str] = set()
    for grp in ("decision_updates", "proposal_updates", "risk_updates"):
        for item in promoted_mutations.get(grp, []):
            if isinstance(item, Mapping):
                covered.update(item.get("evidence_ids", []))
    return covered


_CONDITIONAL_DIALOGUE_MARKERS = (
    "if we agree", "pending sow", "tentative", "potential direction", "subject to",
    "we are exploring", "exploring", "under consideration", "we propose",
    "suggest that we", "i propose",
)


def _is_dialogue_proposal_ev(ev: Mapping[str, Any]) -> tuple[bool, str]:
    txt = str(ev.get("text", "") or ev.get("exact_text", "")).strip()
    kind = str(ev.get("kind", "") or ev.get("source_classification", "")).lower()
    is_prop = "prop" in kind or any(marker in txt.lower() for marker in _CONDITIONAL_DIALOGUE_MARKERS)
    return is_prop, txt


def _promote_dialogue_proposals(
    evidence_index: Mapping[str, Any], promoted_mutations: dict[str, list[Any]],
) -> None:
    p_list = promoted_mutations.setdefault("proposal_updates", [])
    existing_pids = {str(p.get("proposal_id", "")) for p in p_list if isinstance(p, Mapping)}
    covered_eids = _covered_evidence_ids(promoted_mutations)
    for eid, ev in evidence_index.items():
        if not isinstance(ev, Mapping) or eid in covered_eids:
            continue
        is_prop, txt = _is_dialogue_proposal_ev(ev)
        pid = f"proposal:{eid.replace('stmt-', '').replace('ev-', '')}"
        if is_prop and pid not in existing_pids:
            p_list.append({
                "proposal_id": pid, "status": "PROPOSED", "description": txt[:200],
                "rationale": f"Conditional dialogue proposal ({txt[:100]})", "evidence_ids": [eid],
            })
            existing_pids.add(pid)
            covered_eids.add(eid)


def _clean_found_text(found: str) -> str:
    s = re.sub(r"^(\[.*?\]|\*\*.*?\*\*)\s*[:*]*\s*", "", found)
    s = re.sub(r"^\*\*[^*]+\*\*:\s*", "", s).strip()
    return re.sub(r"(\.\.\.|\u2026|\s+$)", "", s).strip()


def _find_prefix_match(clean_et: str, text: str) -> Tuple[int, str]:
    needle = clean_et[:25].strip()
    if not (needle and needle in text):
        return -1, clean_et
    idx = text.find(needle)
    norm_et = re.sub(r"\s+", " ", clean_et)
    tokens = [re.escape(tok) for tok in norm_et.split(" ") if tok]
    if tokens:
        gap = r"(?:\s+|\s*#*\s*Page\s+\d+\s*(?:[^\n]*\(\d+\.\d{4}\)\s*)?)+"
        try:
            m = re.search(gap.join(tokens), text[idx : idx + len(clean_et) * 2 + 250], re.I)
            if m:
                clean_found = _clean_found_text(m.group(0).strip())
                return (text.find(clean_found, idx) if text.find(clean_found, idx) != -1 else idx + m.start()), clean_found
        except re.error:
            pass
    end_idx = text.find("\n", idx + len(needle))
    end_idx = (idx + len(clean_et)) if end_idx == -1 else end_idx
    clean_found = _clean_found_text(text[idx:end_idx].strip())
    f_idx = text.find(clean_found)
    return (f_idx if f_idx != -1 else idx), clean_found


def _clean_matched_line(line: str) -> str:
    line_clean = line.strip().lstrip("#* -")
    colon_idx = line_clean.find("]: ")
    if colon_idx != -1:
        line_clean = line_clean[colon_idx + 3:].strip()
    return _clean_found_text(line_clean)


def _find_fuzzy_words_match(clean_et: str, text: str) -> Tuple[int, str]:
    words = [w.lower() for w in clean_et.split() if len(w) > 4][:4]
    if not words:
        return -1, clean_et
    for line in text.splitlines():
        low_line = line.lower()
        if all(w in low_line for w in words):
            clean_final = _clean_matched_line(line)
            f_idx = text.find(clean_final)
            return (f_idx if f_idx != -1 else text.find(line.strip())), clean_final
    return -1, clean_et


def _extract_last_speaker_header(prefix: str) -> tuple[str | None, str | None]:
    hdrs = list(re.finditer(r"\*\*\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s*([A-Za-z0-9\s\.\-_]+?)(?:\s*[—–-]\s*([A-Za-z0-9\s\.\-_]+))?\*\*", prefix))
    if not hdrs:
        hdrs = list(re.finditer(r"\*\*([A-Za-z0-9\s\.\-_]+?):\*\*", prefix))
    if not hdrs:
        return None, None
    last_h = hdrs[-1]
    groups = last_h.groups()
    spk_val = (groups[1] if len(groups) >= 2 and groups[1] else groups[0]).strip()
    org_val = groups[2].strip() if len(groups) >= 3 and groups[2] else None
    return spk_val, org_val


def _resolve_speaker_attribution(ev_copy: dict[str, Any], text: str, idx: int) -> None:
    spk = ev_copy.get("speaker")
    if spk and spk.lower() not in ("unattributed", "unknown", "system", "speaker"):
        return
    spk_val, org_val = _extract_last_speaker_header(text[:idx])
    if spk_val:
        ev_copy["speaker"] = spk_val
    if org_val:
        ev_copy["speaker_org"] = org_val


def _is_direct_grounded_locator(ev_copy: dict[str, Any]) -> bool:
    loc = ev_copy.get("locator") or {}
    loc_kind = str(loc.get("kind") or "").lower()
    return (
        loc_kind in ("roster", "hitl", "receipt")
        or "roster" in str(ev_copy.get("statement_id", "")).lower()
        or ev_copy.get("source") in ("TRANSCRIPT_HITL", "HITL")
    )


def _ground_evidence_statement(ev_copy: dict[str, Any], clean_et: str, forensic_transcript: str | None) -> bool:
    """Resolve exact source offsets and speaker for an evidence statement. Returns True if grounded."""
    ev_copy["exact_text"] = ev_copy["text"] = clean_et
    if _is_direct_grounded_locator(ev_copy):
        ev_copy["grounding_status"] = "GROUNDED"
        return True
    if not forensic_transcript:
        ev_copy["end_offset"] = ev_copy.get("start_offset", 0) + len(clean_et)
        return True
    loc = ev_copy.get("source_locator") or ev_copy.get("locator") or {}
    loc_val = str(loc.get("value") if isinstance(loc, dict) else (loc or "")).strip()
    sec_pos = forensic_transcript.find(loc_val) if len(loc_val) >= 4 else -1
    idx = forensic_transcript.find(clean_et, sec_pos) if sec_pos != -1 else -1
    idx = forensic_transcript.find(clean_et) if idx == -1 else idx
    matched_text = clean_et
    if idx == -1:
        idx, matched_text = _find_prefix_match(clean_et, forensic_transcript)
    if idx == -1:
        idx, matched_text = _find_fuzzy_words_match(clean_et, forensic_transcript)
    if idx != -1:
        ev_copy.update({"exact_text": matched_text, "text": matched_text, "start_offset": idx, "end_offset": idx + len(matched_text), "grounding_status": "GROUNDED"})
        _resolve_speaker_attribution(ev_copy, forensic_transcript, idx)
        return True
    ev_copy["grounding_status"] = "UNGROUNDED"
    return False


def _resolve_item_eids(item: dict[str, Any]) -> list[str]:
    eids = item.get("evidence_ids")
    if eids:
        return list(eids)
    cur_ev = item.get("evidence")
    if isinstance(cur_ev, dict) and cur_ev.get("statement_id"):
        return [str(cur_ev["statement_id"])]
    return []


def _is_ev_grounded(ev: dict[str, Any] | None) -> bool:
    if not ev:
        return False
    loc_kind = str((ev.get("locator") or {}).get("kind", "")).lower()
    return ev.get("grounding_status") == "GROUNDED" or loc_kind in ("roster", "hitl", "receipt")


def _is_item_grounded(item: dict[str, Any], evidence_index: dict[str, Any]) -> bool:
    return any(_is_ev_grounded(evidence_index.get(eid)) for eid in _resolve_item_eids(item))


def _filter_ungrounded_mutations(
    promoted_mutations: dict[str, list[Any]],
    evidence_index: dict[str, Any],
    quarantined_signals: list[dict[str, Any]],
    source_present: bool,
) -> int:
    """Drop and quarantine assertions that lack grounded source evidence spans."""
    if not source_present:
        return 0
    dropped = 0
    for cat in list(promoted_mutations.keys()):
        kept: list[Any] = []
        for item in promoted_mutations[cat]:
            if _is_item_grounded(item, evidence_index):
                kept.append(item)
            else:
                dropped += 1
                quarantined_signals.append({
                    "signal": item,
                    "score_info": {
                        "promotion_status": "QUARANTINED",
                        "reason": "UNGROUNDED_ASSERTION_NO_SOURCE_SPAN",
                    },
                })
        promoted_mutations[cat] = kept
    return dropped


_PREFIX_NORMALIZERS: dict[str, tuple[str, str, str]] = {
    "decision_updates": ("decision_id", "decision:", r"^(dec-|dec:)"),
    "proposal_updates": ("proposal_id", "proposal:", r"^(prop-|prop:)"),
    "risk_updates": ("risk_id", "risk:", r"^(rsk-|rsk:)"),
}


def _normalize_mutation_id(category: str, item_dict: dict[str, Any]) -> None:
    spec = _PREFIX_NORMALIZERS.get(category)
    if not spec:
        return
    key, prefix, pattern = spec
    val = str(item_dict.get(key, ""))
    if val and not val.startswith(prefix):
        item_dict[key] = f"{prefix}{re.sub(pattern, '', val)}"


def _partition_single_item(
    category: str,
    signal_type: str,
    item_dict: dict[str, Any],
    promoted_mutations: dict[str, list[Any]],
    quarantined_signals: list[dict[str, Any]],
    scoring_receipts: list[dict[str, Any]],
) -> None:
    _normalize_mutation_id(category, item_dict)
    score_info = score_candidate_signal(item_dict, signal_type)
    scoring_receipts.append(score_info)
    if category == "action_updates":
        quarantined_signals.append({
            "signal": item_dict,
            "score_info": {**score_info, "reason": "TACTICAL_ACTION_ITEM_EXCLUDED_FROM_GRAPH"},
        })
    elif score_info["promotion_status"] == "PROMOTED" and category in promoted_mutations:
        promoted_mutations[category].append(item_dict)
    else:
        quarantined_signals.append({"signal": item_dict, "score_info": score_info})


def _score_and_partition_mutations(
    mutations: Mapping[str, Any],
    promoted_mutations: dict[str, list[Any]],
    quarantined_signals: list[dict[str, Any]],
    scoring_receipts: list[dict[str, Any]],
) -> None:
    for category, items in mutations.items():
        if not isinstance(items, list):
            continue
        signal_type = category[:-1] if category.endswith("s") else category
        for item in items:
            if isinstance(item, Mapping):
                _partition_single_item(
                    category, signal_type, dict(item), promoted_mutations, quarantined_signals, scoring_receipts
                )


def _clean_and_ground_evidence_index(
    raw_evidence_index: Any,
    forensic_transcript: str | None,
) -> dict[str, Any]:
    if not isinstance(raw_evidence_index, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for eid, ev in raw_evidence_index.items():
        if not isinstance(ev, dict):
            continue
        ev_copy = dict(ev)
        et = ev_copy.get("exact_text", "") or ev_copy.get("text", "")
        clean_et = re.sub(r"(\.\.\.|\u2026|\s+$)", "", et).strip().strip("\"'")
        clean_et = re.sub(r"^(\[.*?\]|\*\*.*?\*\*)\s*[:*]*\s*", "", clean_et).strip()
        clean_et = re.sub(r"^\*\*[^*]+\*\*:\s*", "", clean_et).strip()
        _ground_evidence_statement(ev_copy, clean_et, forensic_transcript)
        cleaned[eid] = ev_copy
    return cleaned if cleaned else raw_evidence_index


def _parse_raw_projection(raw_projection: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(raw_projection, str):
        try:
            return json.loads(raw_projection)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid raw projection JSON: {exc}") from exc
    return dict(raw_projection)


def _derive_deterministic_meeting_id(data: dict[str, Any], meta: dict[str, Any]) -> str:
    """Resolve deterministic meeting_id without falling back to shared 'meeting-unknown'."""
    degen = {"meeting-unknown", "meeting-unspecified", "meeting-default", "unknown", "unspecified"}
    for cand in (data.get("meeting_id"), meta.get("meeting_id")):
        if isinstance(cand, str) and cand.strip() and cand.strip().lower() not in degen and not re.fullmatch(r"document-[0-9a-f]{12}", cand.strip(), re.I):
            return cand.strip()
    raw_title = next((data.get(k) or meta.get(k) for k in ("title", "meeting_title", "meeting_name", "source_location", "input_path") if data.get(k) or meta.get(k)), "")
    raw_date = next((data.get(k) or meta.get(k) for k in ("date", "meeting_date", "started_at") if data.get(k) or meta.get(k)), "")
    slug = re.sub(r"[^a-z0-9]+", "-", str(raw_title).lower()).strip("-")
    if slug in {"", "unknown", "unspecified", "default", "source-document", "transcript"}:
        raise ValueError("Signal extraction rejected degenerate or missing meeting_id/title ('meeting-unknown' prohibited)")
    date_digits = re.sub(r"\D+", "", str(raw_date)[:10])
    prefix = "document" if (meta.get("is_document") or meta.get("output3_only")) else "meeting"
    return f"{prefix}-{date_digits}-{slug[:48]}" if len(date_digits) == 8 else f"{prefix}-{slug[:48]}"


def _build_extracted_projection(
    data: dict[str, Any],
    meta: dict[str, Any],
    effective_ev_index: dict[str, Any],
    promoted_mutations: dict[str, list[Any]],
    promoted_evidence_ids: list[Any],
) -> dict[str, Any]:
    proj: dict[str, Any] = {
        "schema_version": data.get("schema_version", "5.7"),
        "meeting_id": _derive_deterministic_meeting_id(data, meta),
        "classification": data.get("classification") or meta.get("classification", "internal"),
        "audiences": data.get("audiences") or meta.get("audiences", ["nee-engineering"]),
        "evidence_index": effective_ev_index,
        "graph_mutations": promoted_mutations,
        "evidence_only_statement_ids": promoted_evidence_ids,
    }
    for opt_key in ("ingestion_context", "ingestion_context_hash", "_strategic_analysis"):
        if opt_key in data:
            proj[opt_key] = data[opt_key]
    return proj


class SignalExtractor:
    """Independent signal extraction and gating processor."""

    def __init__(self, min_confidence: float = MIN_PROMOTION_CONFIDENCE) -> None:
        self.min_confidence = min_confidence

    def extract_and_gate(
        self,
        raw_projection: dict[str, Any] | str,
        *,
        metadata: dict[str, Any] | None = None,
        forensic_transcript: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Gate raw signal projection into promoted mutations and extraction receipt."""
        data = _parse_raw_projection(raw_projection)
        meta = metadata or {}
        promoted_mutations: dict[str, list[Any]] = {
            "entity_upserts": [], "relationship_signals": [], "decision_updates": [],
            "proposal_updates": [], "action_updates": [], "risk_updates": [], "strategic_intent_signals": [],
        }
        quarantined_signals: list[dict[str, Any]] = []
        scoring_receipts: list[dict[str, Any]] = []
        _score_and_partition_mutations(
            data.get("graph_mutations", {}), promoted_mutations, quarantined_signals, scoring_receipts
        )
        effective_ev_index = _clean_and_ground_evidence_index(data.get("evidence_index", {}), forensic_transcript)
        if not (meta.get("is_document") or meta.get("output3_only")):
            _promote_dialogue_proposals(effective_ev_index, promoted_mutations)
        apply_epistemic_and_bio_temporal_scoping(promoted_mutations)

        source_present = bool(forensic_transcript and len(forensic_transcript.strip()) > 30)
        dropped_ungrounded = _filter_ungrounded_mutations(
            promoted_mutations, effective_ev_index, quarantined_signals, source_present=source_present,
        )
        promoted_count = sum(len(items) for items in promoted_mutations.values())
        evidence_ceiling = max(3, 2 * promoted_count)
        raw_eids = data.get("evidence_only_statement_ids", [])
        promoted_evidence_ids = raw_eids[:evidence_ceiling] if isinstance(raw_eids, list) else []

        promoted_projection = _build_extracted_projection(
            data, meta, effective_ev_index, promoted_mutations, promoted_evidence_ids
        )
        created_at = utc_now()
        total_eval = promoted_count + dropped_ungrounded
        receipt = {
            "receipt_id": stable_id("signal-extraction-receipt", created_at),
            "total_candidates_evaluated": len(scoring_receipts),
            "promoted_signal_count": promoted_count,
            "quarantined_signal_count": len(quarantined_signals),
            "dropped_ungrounded_assertions": dropped_ungrounded,
            "grounded_assertions_promoted": promoted_count,
            "grounding_ratio": 1.0 if total_eval == 0 else promoted_count / total_eval,
            "evidence_only_count": len(promoted_evidence_ids),
            "evidence_ceiling_enforced": evidence_ceiling,
            "quarantined_signals": quarantined_signals,
            "created_at": created_at,
            "projection_digest": digest(promoted_projection),
        }
        return promoted_projection, receipt


def package_evidence(evidence: dict) -> dict:
    """Wave 8: Package evidence preserving lineage and dispositions."""
    from dual_pipeline.contracts import verify_authority_contract
    verify_authority_contract(evidence)
    return evidence

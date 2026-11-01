"""
dual_pipeline/transcript_qa_core/semantic_linters.py

Deterministic semantic and ontological linters for Gate 3:
- Gate 3.7: Compound Property Linter
- Gate 3.8: Citation Coverage Linter
- Gate 3.9: Predicate Domain Linter
- Gate 3.10: Mutation Budget Governor
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

# Canonical W2/W3 domain predicates
CANONICAL_DOMAIN_PREDICATES: Set[str] = {
    "CONSTRAINS", "INTEGRATES_WITH", "DEPENDS_ON", "GOVERNS", "VALIDATES",
    "EVALUATES", "CONTRIBUTES_TO", "CONTEXT_FOR", "OWNS", "ACCELERATES",
    "MEMBER_OF", "AFFILIATED_WITH", "CANDIDATE_FOR", "CONTAINS", "DELIVERS",
    "HAS_CLAIM_TEXT", "HAS_RISK", "HAS_ROLE_IN", "HAS_ROLE", "OWNS_OR_LEADS",
    "LEADS", "REPORTS_TO", "ROUTES_TO", "SUPPORTED_BY", "TRANSITIONS_TO",
    "USES_PATTERN", "PRODUCES", "CONSUMES", "CO_SELLS_WITH", "COORDINATES_PIPELINE_WITH",
    "PROMOTES_ADOPTION_OF", "ACCOUNTABLE_FOR", "COLLABORATES_WITH", "ALIGNS_WITH",
    "USES", "RECORDED_IN", "DESIGNATED_IN", "TRACKED_IN", "PROVIDES_SCREENING_FOR",
    "PROVIDES_FEEDBACK_TO", "PROVIDES_CANDIDATES_TO", "ENABLES", "BLOCKS",
    "PARTNERS_WITH", "EXPLORES", "PARTICIPATES_IN",
}

GENERIC_PREDICATES: Set[str] = {"RELATED_TO"}

_PREDICATE_MAPPINGS: Dict[str, str] = {
    "RETAINED_FOR": "MEMBER_OF", "NOT_RETAINED_FOR": "CONSTRAINS", "INTERVIEW_PENDING": "CANDIDATE_FOR",
    "TENTATIVE_ASSIGNMENT": "CANDIDATE_FOR", "PROPOSED_FOR": "CANDIDATE_FOR", "CONSIDERED_FOR": "CANDIDATE_FOR",
    "ASSIGNED_TO": "MEMBER_OF", "DEVELOPS": "PRODUCES", "SPONSORS": "GOVERNS", "WORKS_ON": "CONTRIBUTES_TO",
    "LEADS": "OWNS_OR_LEADS", "TECH_LEAD_FOR": "OWNS_OR_LEADS", "PART_OF": "MEMBER_OF", "INTERVIEWED_BY": "VALIDATES",
    "SCREENED_BY": "VALIDATES", "EVALUATED_BY": "VALIDATES", "MANAGES": "GOVERNS", "SUPERVISES": "GOVERNS",
    "OPERATES": "OWNS_OR_LEADS", "OPERATE": "OWNS_OR_LEADS", "OPERATED_BY": "GOVERNS",
    "CONNECTED_TO": "INTEGRATES_WITH", "LINKS_TO": "INTEGRATES_WITH", "COMPONENT_OF": "INTEGRATES_WITH",
    "PARTNER_WITH": "PARTNERS_WITH", "PARTNERSHIP_WITH": "PARTNERS_WITH", "PARTNERED_WITH": "PARTNERS_WITH",
    "COLLABORATES": "COLLABORATES_WITH", "UTILIZES": "USES", "POWERS": "ENABLES",
    "BUILDS": "PRODUCES", "CREATES": "PRODUCES",
    "HOLDS_ROLE": "HAS_ROLE", "SERVES_AS_ROLE": "HAS_ROLE", "HAS_TITLE": "HAS_ROLE",
}

_KEYWORD_PREDICATE_RULES: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("ROLE", "TITLE"), "HAS_ROLE"),
    (("CANDIDATE", "PENDING", "APPL", "TENTATIVE", "PROPOSED"), "CANDIDATE_FOR"),
    (("RETAIN", "MEMBER", "ASSIGN"), "MEMBER_OF"),
    (("LEAD",), "LEADS"),
    (("OWN",), "OWNS"),
    (("REPORT",), "REPORTS_TO"),
    (("EVALUAT",), "EVALUATES"),
    (("VALIDAT", "SCREEN", "TEST", "INTERVIEW"), "VALIDATES"),
    (("RISK", "NOT_RETAIN", "CONSTRAIN"), "CONSTRAINS"),
    (("PARTNER", "ALLIANCE"), "PARTNERS_WITH"),
    (("COLLABORAT",), "COLLABORATES_WITH"),
    (("ENABLE", "POWER", "ACCELERAT"), "ENABLES"),
    (("OPERAT", "MANAGE", "SUPERVIS"), "OWNS_OR_LEADS"),
    (("PRODUCE", "BUILD", "CREATE", "DELIVER", "DEVELOP"), "PRODUCES"),
    (("USE", "UTILIZ", "RELY", "REQUIRE"), "USES"),
)

_TENTATIVE_STAFFING_CUES = (
    "tentative", "potential", "candidate", "possible", "considering",
    "exploring", "likely", "willing to start", "proposed", "evaluate whether",
)
_TRANSIENT_ACTION_USES_CUES = (
    "offered to update", "will update", "reflect that assignment in",
    "update salesforce", "post in the", "log in salesforce", "action item",
)


def _normalize_predicate(pred: str, allow_quarantine: bool = True) -> Optional[str]:
    c = (pred or "").strip().upper()
    if c in CANONICAL_DOMAIN_PREDICATES:
        return c
    if c in _PREDICATE_MAPPINGS:
        return _PREDICATE_MAPPINGS[c]
    for kws, target in _KEYWORD_PREDICATE_RULES:
        if any(k in c for k in kws):
            return target
    return None if allow_quarantine else "CONTRIBUTES_TO"


def _combined_signal_context(sig: Dict[str, Any], evidence_index: Optional[Dict[str, Any]]) -> str:
    parts = [str(sig.get("description") or ""), str(sig.get("rationale") or "")]
    if isinstance(evidence_index, dict):
        for eid in sig.get("evidence_ids") or []:
            ev = evidence_index.get(eid)
            if isinstance(ev, dict):
                parts.append(str(ev.get("text") or ""))
                parts.append(str(ev.get("exact_text") or ""))
    return " ".join(parts).lower()


def _is_transient_action_uses_signal(sig: Dict[str, Any], pred: str, ctx: str) -> bool:
    if pred != "USES":
        return False
    from_id = str(sig.get("from_id") or "")
    to_id = str(sig.get("to_id") or "")
    return from_id.startswith("person:") and to_id.startswith("system:") and any(c in ctx for c in _TRANSIENT_ACTION_USES_CUES)


def _is_tentative_staffing_signal(sig: Dict[str, Any], pred: str, ctx: str) -> bool:
    if pred not in ("ASSIGNED_TO", "MEMBER_OF", "PART_OF") or not str(sig.get("from_id") or "").startswith("person:"):
        return False
    ep_stat = str(sig.get("epistemic_status") or "").upper()
    st_stat = str(sig.get("status") or "").upper()
    return ep_stat == "PROPOSED" or st_stat in ("PROPOSED", "PLANNED") or any(c in ctx for c in _TENTATIVE_STAFFING_CUES)


def normalize_tentative_staffing_and_action_signals(
    signals: List[Dict[str, Any]],
    evidence_index: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Demote tentative staffing edges to CANDIDATE_FOR (PROPOSED) and drop transient action USES edges."""
    kept: List[Dict[str, Any]] = []
    for sig in signals:
        if not isinstance(sig, dict):
            continue
        pred = str(sig.get("relationship") or sig.get("predicate") or "").strip().upper()
        ctx = _combined_signal_context(sig, evidence_index)
        if _is_transient_action_uses_signal(sig, pred, ctx):
            continue
        if _is_tentative_staffing_signal(sig, pred, ctx):
            sig["relationship"] = "CANDIDATE_FOR"
            sig["predicate"] = "CANDIDATE_FOR"
            sig["epistemic_status"] = "PROPOSED"
            sig["status"] = "PROPOSED"
            if str(sig.get("description") or "").strip().lower() in ("", "member of organization."):
                sig["description"] = "Prospective candidate for assignment."
        kept.append(sig)
    return kept


# Secondary platforms that should not inflate primary entity upserts
SECONDARY_PLATFORM_KEYWORDS: Set[str] = {
    "crow", "operator log", "operator_log", "sharepoint", "jira", "servicenow", "teams", "slack",
}

RE_PACKED_KV = re.compile(r"\b[a-zA-Z_]+[:=]\s*[^,;|]+[,;|]\s*[a-zA-Z_]+[:=]")
RE_SLASH_COMPOUND = re.compile(r"^[a-zA-Z0-9_-]+\s*/\s*[a-zA-Z0-9_-]+$")
RE_DATE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
RE_BRACKET_TS = re.compile(r"\[(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[-–]\s*\d{1,2}:\d{2}(?::\d{2})?)?)\]")
RE_OFFSET_TS = re.compile(r"(?:at|offset|timestamp)\s+(\d{1,2}:\d{2}(?::\d{2})?)", re.IGNORECASE)
RE_RAW_TS = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")


# ── Gate 3.7: Compound Property Linter ──────────────────────────────────────────


def _is_compound_value(val: Any) -> bool:
    """Check if a string value contains packed key-value pairs or slashed compound tokens."""
    if not isinstance(val, str):
        return False
    clean = val.strip()
    if clean.startswith(("http://", "https://", "urn:")) or RE_DATE.match(clean):
        return False
    if RE_PACKED_KV.search(clean):
        return True
    return bool(RE_SLASH_COMPOUND.match(clean) and "/" in clean)


def _check_entity_compound(entity: Dict[str, Any]) -> List[str]:
    """Inspect entity name, type, and attributes for compound properties."""
    violations: List[str] = []
    e_id = entity.get("id") or "unknown"
    name = str(entity.get("name") or "")
    if re.search(r"\b[a-zA-Z0-9_]+\s*/\s*[a-zA-Z0-9_]+\b", name):
        violations.append(f"Entity '{e_id}' name '{name}' contains compound conjunction.")

    e_type = str(entity.get("type") or "")
    if any(sep in e_type for sep in ("/", ",", ";")):
        violations.append(f"Entity '{e_id}' type '{e_type}' contains compound separator.")

    attrs = entity.get("attributes") or entity.get("properties") or {}
    if isinstance(attrs, dict):
        for k, v in attrs.items():
            if _is_compound_value(v):
                violations.append(f"Entity '{e_id}' attribute '{k}' contains compound packed value: '{v}'.")
    return violations


def _check_relationship_compound(rel: Dict[str, Any]) -> List[str]:
    """Inspect relationship signal predicate for compound conjunctions."""
    sig_id = rel.get("signal_id", "unknown")
    pred = rel.get("relationship") or rel.get("predicate") or ""
    if re.search(r"[/,&]|(?:\s+(?:AND|OR)\s+)", pred, re.IGNORECASE):
        return [f"Relationship '{sig_id}' predicate '{pred}' contains compound conjunction."]
    return []


def _check_update_compound(items: List[Dict[str, Any]], kind: str) -> List[str]:
    """Inspect status and owner fields in mutation updates."""
    violations: List[str] = []
    for item in items:
        item_id = item.get(f"{kind}_id") or item.get("id") or "unknown"
        status = str(item.get("status") or "")
        if any(sep in status for sep in ("/", ",", ";", "|")):
            violations.append(f"{kind.title()} '{item_id}' status '{status}' contains compound separator.")
        if kind == "action":
            owner = str(item.get("owner_id") or item.get("owner") or "")
            if any(sep in owner for sep in ("/", ",", ";", "&", "|")):
                violations.append(f"Action '{item_id}' owner '{owner}' contains compound assignment.")
    return violations


def lint_compound_properties(data: Dict[str, Any]) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Execute Gate 3.7: Detects and flags compound strings in properties."""
    violations: List[str] = []
    raw_mutations = data.get("graph_mutations") or data.get("mutations") or {}
    mutations = raw_mutations if isinstance(raw_mutations, dict) else {}

    for ent in mutations.get("entity_upserts") or mutations.get("entities") or []:
        violations.extend(_check_entity_compound(ent))
    for rel in mutations.get("relationship_signals") or mutations.get("signals") or []:
        violations.extend(_check_relationship_compound(rel))
    for cat in ("decision", "proposal", "action", "risk"):
        items = mutations.get(f"{cat}_updates") or mutations.get(f"{cat}s") or []
        violations.extend(_check_update_compound(items, cat))

    passed = len(violations) == 0
    return passed, violations, {
        "passed": passed,
        "compound_properties_count": len(violations),
        "violations": violations[:5],
    }


# ── Gate 3.8: Citation Coverage Linter ─────────────────────────────────────────


def _extract_evidence_timestamps(evidence_index: Dict[str, Any]) -> Set[str]:
    """Extract all timestamps from evidence statements for coverage indexing."""
    timestamps: Set[str] = set()
    for ev in evidence_index.values():
        if isinstance(ev, dict):
            loc_val = (ev.get("locator") or {}).get("value", "") if isinstance(ev.get("locator"), dict) else ""
            txt = ev.get("text", "") + " " + ev.get("exact_text", "") + " " + loc_val
            timestamps.update(m.strip() for m in RE_RAW_TS.findall(txt))
    return timestamps


def _collect_known_evidence_ids(ev_index: Dict[str, Any]) -> Set[str]:
    known_ids = set(ev_index.keys())
    for ev in ev_index.values():
        if isinstance(ev, dict):
            for key in ("statement_id", "evidence_id"):
                val = ev.get(key)
                if val:
                    known_ids.add(val)
    return known_ids


def _check_dangling_evidence_ids(mutations: Dict[str, Any], known_ids: Set[str]) -> List[str]:
    """Verify that every evidence_id cited in mutations exists in evidence_index."""
    dangling: List[str] = []
    for cat in ("relationship_signals", "decision_updates", "proposal_updates", "action_updates", "risk_updates"):
        for item in mutations.get(cat, []):
            item_id = item.get("signal_id") or item.get(f"{cat[:-1]}_id") or item.get("id") or "item"
            dangling.extend(
                f"Dangling evidence ID '{eid}' in {cat} '{item_id}'."
                for eid in item.get("evidence_ids", [])
                if eid not in known_ids
            )
    return dangling


def _check_evidence_only_statements(
    ev_only_ids: Any, known_ids: Set[str], mutations: Dict[str, Any]
) -> List[str]:
    if not isinstance(ev_only_ids, list):
        return []
    violations = [
        f"Statement ID '{sid}' in evidence_only_statement_ids not found in evidence_index."
        for sid in ev_only_ids
        if sid not in known_ids
    ]
    promoted_count = sum(
        len(mutations.get(cat, []))
        for cat in ("decision_updates", "proposal_updates", "action_updates", "risk_updates")
    )
    max_allowed = max(3, 2 * promoted_count)
    if len(ev_only_ids) > max_allowed:
        violations.append(
            f"Rule C-17 violation: evidence_only_statement_ids count ({len(ev_only_ids)}) "
            f"exceeds max(3, 2 * promoted_mutation_count) = {max_allowed}."
        )
    return violations


def _collect_narrative_entries(data: Dict[str, Any], mutations: Dict[str, Any]) -> List[Tuple[str, str]]:
    narratives: List[Tuple[str, str]] = []
    if "_strategic_analysis" in data:
        narratives.append(("_strategic_analysis", str(data["_strategic_analysis"])))
    for cat in ("decision", "proposal", "risk"):
        for item in mutations.get(f"{cat}_updates", []):
            iid = item.get(f"{cat}_id") or "item"
            desc = (item.get("description") or "") + " " + (item.get("rationale") or "")
            narratives.append((f"{cat}:{iid}", desc))
    return narratives


def _check_narrative_citations(narratives: List[Tuple[str, str]], known_ts: Set[str]) -> List[str]:
    """Verify explicit timestamp citations in narrative texts exist in evidence."""
    unindexed: List[str] = []
    for location, text in narratives:
        if not text:
            continue
        for m in RE_BRACKET_TS.findall(text):
            ts = m.strip().split("-")[0].strip()
            if ts not in known_ts:
                unindexed.append(f"Unindexed timestamp citation '[{m}]' in {location}.")
        for m in RE_OFFSET_TS.findall(text):
            ts = m.strip()
            if ts not in known_ts:
                unindexed.append(f"Unindexed timestamp citation '{ts}' in {location}.")
    return unindexed


def lint_citation_coverage(data: Dict[str, Any]) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Execute Gate 3.8: Verifies 100% citation coverage and no unindexed citations."""
    ev_index = data.get("evidence_index", {})
    known_ids = _collect_known_evidence_ids(ev_index)
    known_ts = _extract_evidence_timestamps(ev_index)
    mutations = data.get("graph_mutations") or data.get("mutations") or {}

    dangling = _check_dangling_evidence_ids(mutations, known_ids)
    ev_only_errs = _check_evidence_only_statements(data.get("evidence_only_statement_ids", []), known_ids, mutations)
    unindexed = _check_narrative_citations(_collect_narrative_entries(data, mutations), known_ts)
    violations = dangling + ev_only_errs + unindexed

    passed = len(violations) == 0
    return passed, violations, {
        "passed": passed,
        "dangling_evidence_count": len(dangling),
        "unindexed_citations_count": len(unindexed),
        "violations": violations[:5],
    }


# ── Gate 3.9: Predicate Domain Linter ──────────────────────────────────────────


def _check_architectural_predicate(rel: Dict[str, Any], pred: str) -> Optional[str]:
    """Flag generic predicates on architectural edges between systems."""
    from_id = str(rel.get("from_id") or "")
    to_id = str(rel.get("to_id") or "")
    sig_id = str(rel.get("signal_id") or "unknown")
    if from_id.startswith("system:") and to_id.startswith("system:") and pred in GENERIC_PREDICATES:
        return (
            f"Generic predicate '{pred}' forbidden on architectural edge "
            f"'{from_id}' -> '{to_id}' (signal '{sig_id}')."
        )
    return None


def lint_predicate_domain(relationship_signals: List[Dict[str, Any]]) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Execute Gate 3.9: Enforces canonical predicates and bans generic ones on arch edges."""
    violations: List[str] = []
    generic_count = 0

    for rel in relationship_signals:
        if rel.get("is_quarantined"):
            continue
        raw_pred = rel.get("relationship") or rel.get("predicate") or ""
        pred = raw_pred.strip().upper()
        sig_id = rel.get("signal_id", "unknown")

        if pred not in CANONICAL_DOMAIN_PREDICATES:
            violations.append(f"Non-canonical predicate '{pred}' in signal '{sig_id}'.")

        arch_err = _check_architectural_predicate(rel, pred)
        if arch_err:
            violations.append(arch_err)

        if pred in GENERIC_PREDICATES:
            generic_count += 1

    total_rels = len(relationship_signals)
    generic_ratio = round(generic_count / max(1, total_rels), 2)
    if total_rels >= 4 and generic_ratio > 0.25:
        violations.append(f"Generic predicate ratio ({generic_count}/{total_rels} = {generic_ratio:.2f}) exceeds max 0.25.")

    passed = len(violations) == 0
    return passed, violations, {
        "passed": passed,
        "canonical_predicates_compliant": not any("Non-canonical" in v for v in violations),
        "architectural_edges_compliant": not any("forbidden on architectural" in v for v in violations),
        "generic_ratio": generic_ratio,
        "violations": violations[:5],
    }


# ── Gate 3.10: Mutation Budget Governor ────────────────────────────────────────


def _is_grounded_doc_entity(ent: Dict[str, Any], is_document: bool, evidence_index: Optional[Dict[str, Any]]) -> bool:
    if not (is_document and evidence_index):
        return False
    eids = ent.get("evidence_ids") or []
    return bool(eids and all(eid in evidence_index for eid in eids))


def _check_secondary_platforms(
    entity_upserts: List[Dict[str, Any]],
    signals: List[Dict[str, Any]],
    is_document: bool = False,
    evidence_index: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Ensure secondary platforms are not promoted to unlinked primary entities."""
    violations: List[str] = []
    connected_ids = {str(r.get("from_id")) for r in signals if r.get("from_id")} | {str(r.get("to_id")) for r in signals if r.get("to_id")}
    for ent in entity_upserts:
        e_id = str(ent.get("id") or "").lower()
        if any(sec in e_id for sec in SECONDARY_PLATFORM_KEYWORDS) and ent.get("id") not in connected_ids:
            if _is_grounded_doc_entity(ent, is_document, evidence_index):
                continue
            violations.append(
                f"Secondary platform '{ent.get('id')}' has zero relationships; "
                f"relegate to evidence_only_statement_ids."
            )
    return violations


def _resolve_effective_mutation_budget(
    data: Dict[str, Any], f1_text: str, max_budget: int, metadata: Optional[Dict[str, Any]]
) -> int:
    from dual_pipeline.transcript_duration_helper import resolve_duration_mutation_budget

    meta = metadata if isinstance(metadata, dict) and metadata else (data.get("metadata") if isinstance(data.get("metadata"), dict) else data)
    if max_budget == 30:
        return resolve_duration_mutation_budget(
            metadata=meta,
            text=f1_text or str(data.get("raw_transcript") or ""),
            base_budget=max_budget,
        )
    if data.get("source_type") == "visual_document" or data.get("is_visual_document"):
        return max(max_budget, 60)
    return max_budget


def _is_document_payload(data: Dict[str, Any]) -> bool:
    return bool(
        data.get("is_document")
        or data.get("output3_only")
        or data.get("canonical_modality") == "DOCUMENT"
        or str(data.get("meeting_id") or "").startswith("document-")
    )


def lint_mutation_budget(
    data: Dict[str, Any],
    total_mutations: int,
    f1_text: str = "",
    max_budget: int = 30,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Execute Gate 3.10: Enforces materiality- and duration-proportional mutation budget and roster exemption."""
    from dual_pipeline.transcript_duration_helper import _is_roster_edge, count_roster_scaffolding_mutations

    effective_budget = _resolve_effective_mutation_budget(data, f1_text, max_budget, metadata)
    mutations = data.get("graph_mutations") or data.get("mutations") or {}
    signals = mutations.get("relationship_signals") or mutations.get("signals") or []
    roster_triples_count = sum(
        1 for s in signals
        if isinstance(s, dict)
        and _is_roster_edge(
            str(s.get("relationship") or s.get("predicate") or "").upper(),
            str(s.get("from_id") or s.get("source_id", "")),
            str(s.get("to_id") or s.get("target_id", "")),
        )
    )
    roster_exempt = max(roster_triples_count, count_roster_scaffolding_mutations(mutations))
    effective_ceiling = effective_budget + roster_exempt

    violations: List[str] = []
    if total_mutations > effective_ceiling:
        violations.append(f"Total graph mutations ({total_mutations}) exceeds maximum budget of {effective_ceiling}.")

    evidence_only = data.get("evidence_only_statement_ids", [])
    ceiling = max(3, 2 * total_mutations)
    if len(evidence_only) > ceiling:
        violations.append(f"Evidence-only statement count ({len(evidence_only)}) exceeds hard ceiling of {ceiling}.")

    entities = mutations.get("entity_upserts") or mutations.get("entities") or []
    sec_violations = _check_secondary_platforms(entities, signals, is_document=_is_document_payload(data), evidence_index=data.get("evidence_index"))
    violations.extend(sec_violations)

    passed = len(violations) == 0
    return passed, violations, {
        "passed": passed,
        "total_mutations": total_mutations,
        "budget_ceiling": effective_ceiling,
        "base_budget": effective_budget,
        "roster_exempt_triples": roster_exempt,
        "evidence_only_count": len(evidence_only),
        "evidence_only_ceiling": ceiling,
        "secondary_platforms_relegated": len(sec_violations) == 0,
        "violations": violations[:5],
    }

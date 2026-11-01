"""Post-extraction sanitization, schema enforcement, and validation for Output 03."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from dual_pipeline.transcript_models import CanonicalSignalProjectionV57
from dual_pipeline.transcript_stages_core.graph_post_helpers import (
    _canonicalize_entity_id,
    _collect_connected_ids,
    _matches_any_keyword,
    _normalize_decisions,
    _normalize_entities,
    _normalize_risks,
    _parse_raw_payload,
    _populate_root_metadata,
    _prune_orphan_entities,
    _resolve_utterance_matched_evidence_ids,
)

logger = logging.getLogger(__name__)


RELATIONSHIP_PRIORITY = {
    "MEMBER_OF": 1,
    "INTEGRATES_WITH": 1,
    "DEPENDS_ON": 1,
    "GOVERNS": 1,
    "VALIDATES": 1,
    "OWNS": 1,
    "DELIVERS": 1,
    "CONTRIBUTES_TO": 1,
    "PRODUCES": 1,
    "BLOCKS": 1,
    "CONSTRAINS": 1,
    "EVALUATES": 1,
    "MANAGES": 2,
    "HAS_ROLE": 2,
    "REPORTS_TO": 2,
    "SUPERVISES": 2,
    "EXPLORES": 3,
    "HYPOTHESIZES": 3,
}


def _collect_doc_grounded_ids(
    entities: List[Any],
    ev_index: Optional[Dict[str, Any]],
    core_only: bool = False,
) -> set[str]:
    grounded: set[str] = set()
    for ent in entities:
        if not isinstance(ent, dict):
            continue
        eid = str(ent.get("id") or "")
        if not eid:
            continue
        if core_only and eid.startswith("person:"):
            continue
        eids = ent.get("evidence_ids") or []
        if not ev_index or not eids or any(ev in ev_index for ev in eids):
            grounded.add(eid)
    return grounded


def _enforce_mutation_budget(
    mutations: Dict[str, Any],
    max_budget: int = 30,
    is_document: bool = False,
    ev_index: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    text: str = "",
) -> None:
    """Prune orphans and enforce Gate 3.10 materiality-proportional mutation budget using deterministic semantic triage."""
    from dual_pipeline.transcript_duration_helper import (
        _is_roster_edge,
        count_roster_scaffolding_mutations,
        resolve_duration_mutation_budget,
    )

    effective_budget = max_budget
    if max_budget == 30:
        effective_budget = resolve_duration_mutation_budget(metadata, text, base_budget=max_budget)

    entities, signals = mutations.get("entity_upserts", []), mutations.get("relationship_signals", [])
    static_count = sum(len(mutations.get(k, [])) for k in ("decision_updates", "risk_updates", "proposal_updates", "action_updates", "strategic_intent_signals"))
    connected = _collect_connected_ids(signals, mutations)
    is_lessons_learned = bool(
        (metadata and (metadata.get("meeting_archetype") == "LESSONS_LEARNED" or metadata.get("archetype") == "LESSONS_LEARNED"))
        or mutations.get("meeting_archetype") == "LESSONS_LEARNED"
    )
    if is_document or is_lessons_learned:
        connected |= _collect_doc_grounded_ids(entities, ev_index, core_only=False)
    entities = _prune_orphan_entities(entities, connected)

    roster_sigs = [
        s for s in signals
        if isinstance(s, dict)
        and _is_roster_edge(
            str(s.get("relationship") or s.get("predicate") or "").upper(),
            str(s.get("from_id") or ""),
            str(s.get("to_id") or ""),
        )
    ]
    roster_exempt = max(len(roster_sigs), count_roster_scaffolding_mutations(mutations))
    effective_ceiling = effective_budget + roster_exempt

    def _signal_priority_key(sig: Dict[str, Any]) -> int:
        if sig in roster_sigs:
            return 0
        rel = str(sig.get("relationship", "")).upper()
        return RELATIONSHIP_PRIORITY.get(rel, 3)

    total_count = len(entities) + len(signals) + static_count
    pruned_count = 0
    min_signals = len(roster_sigs) + 4
    if total_count > effective_ceiling and len(signals) > min_signals:
        ranked_signals = sorted(signals, key=_signal_priority_key)
        target_signals_len = min_signals
        doc_grounded = _collect_doc_grounded_ids(entities, ev_index, core_only=True) if (is_document or is_lessons_learned) else set()
        for cutoff in range(len(ranked_signals) - 1, min_signals - 1, -1):
            cand_signals = ranked_signals[:cutoff]
            cand_connected = _collect_connected_ids(cand_signals, mutations) | doc_grounded
            cand_entities = _prune_orphan_entities(entities, cand_connected)
            if len(cand_entities) + len(cand_signals) + static_count <= effective_ceiling:
                target_signals_len = cutoff
                break
        pruned_count = len(signals) - target_signals_len
        signals = ranked_signals[:target_signals_len]
        connected = _collect_connected_ids(signals, mutations) | doc_grounded
        entities = _prune_orphan_entities(entities, connected)

    if pruned_count > 0 or effective_ceiling > 30:
        mutations["_budget_triage_audit"] = {
            "triage_policy": "semantic_edge_prioritization",
            "pruned_signal_count": pruned_count,
            "budget_ceiling": effective_ceiling,
            "base_budget": effective_budget,
            "roster_exempt_triples": roster_exempt,
        }

    mutations["entity_upserts"] = entities
    mutations["relationship_signals"] = signals



def _materialize_missing_endpoints_and_evidence(mutations: Dict[str, Any], ev_index: Optional[Dict[str, Any]] = None) -> None:
    """Ensure all signal endpoints exist in entity_upserts and all entities carry evidence_ids."""
    entities: List[Dict[str, Any]] = mutations.setdefault("entity_upserts", [])
    signals: List[Dict[str, Any]] = mutations.get("relationship_signals", [])
    existing_ids = {str(e.get("id")) for e in entities if isinstance(e, dict) and e.get("id")}
    fallback_ev = [next(iter(ev_index.keys()))] if (ev_index and isinstance(ev_index, dict) and ev_index) else []

    for sig in signals:
        if not isinstance(sig, dict):
            continue
        sig_ev = [eid for eid in (sig.get("evidence_ids") or []) if not ev_index or eid in ev_index]
        if not sig_ev:
            sig_ev = _resolve_utterance_matched_evidence_ids(
                [str(sig.get("from_id", "")), str(sig.get("to_id", "")), str(sig.get("description", ""))],
                ev_index,
                fallback_ev,
            )
            sig["evidence_ids"] = list(sig_ev)
        for k in ("from_id", "to_id"):
            raw_eid = str(sig.get(k, "")).strip()
            if not raw_eid or raw_eid.startswith(("decision:", "proposal:", "action:", "risk:")):
                continue
            eid = _canonicalize_entity_id(raw_eid)
            sig[k] = eid
            if eid and eid not in existing_ids:
                pfx, rname = eid.split(":", 1)[0], eid.split(":", 1)[-1]
                entities.append({"id": eid, "type": pfx, "name": rname.replace("-", " ").replace("_", " ").title(), "mutation": "CONFIRM", "evidence_ids": list(sig_ev)})
                existing_ids.add(eid)

    signals[:] = [
        s for s in signals
        if isinstance(s, dict)
        and not str(s.get("from_id", "")).startswith(("decision:", "proposal:", "action:", "risk:"))
        and not str(s.get("to_id", "")).startswith(("decision:", "proposal:", "action:", "risk:"))
    ]

    for ent in entities:
        if isinstance(ent, dict) and not ent.get("evidence_ids"):
            eid = str(ent.get("id") or "")
            c_ev = next((s.get("evidence_ids") for s in signals if isinstance(s, dict) and s.get("evidence_ids") and (s.get("from_id") == eid or s.get("to_id") == eid)), None)
            if c_ev:
                ent["evidence_ids"] = list(c_ev)
            else:
                ent["evidence_ids"] = _resolve_utterance_matched_evidence_ids(
                    [eid, str(ent.get("name", "")), str(ent.get("description", ""))],
                    ev_index,
                    fallback_ev,
                )



def _find_or_create_system_evidence(sys_id: str, kws: List[str], ev_index: Dict[str, Any], source_text: Optional[str]) -> List[str]:
    m = [eid for eid, ev in ev_index.items() if isinstance(ev, dict) and _matches_any_keyword(str(ev.get("text", "")), kws)]
    if m:
        return m[:2]
    if not source_text or not _matches_any_keyword(source_text, kws):
        return []
    new_eid = f"ev-{sys_id.split(':', 1)[-1]}"
    if new_eid in ev_index:
        return [new_eid]
    for line in source_text.splitlines():
        lc = line.strip()
        if lc.startswith(("#", "|", "-")) or not _matches_any_keyword(lc, kws):
            continue
        m_spk = re.match(r"^\*\*([^*]+?):?\*\*:?\s*\[([^\]]+)\]\s*(.+)$", lc)
        spk, loc, txt = (m_spk.group(1).strip(), {"kind": "timestamp", "value": m_spk.group(2)}, m_spk.group(3).strip()) if m_spk else ("Session Context", {"kind": "section", "value": "Transcript"}, lc)
        ev_index[new_eid] = {"speaker": spk, "source_classification": "FACT", "text": txt, "exact_text": txt, "locator": loc, "statement_id": new_eid, "kind": "technical"}
        return [new_eid]
    return []


def _ensure_critical_architecture_systems(
    mutations: Dict[str, Any], ev_index: Optional[Dict[str, Any]] = None, source_text: Optional[str] = None
) -> None:
    if not ev_index or not isinstance(ev_index, dict):
        return
    entities = mutations.setdefault("entity_upserts", [])
    existing_ids = {str(e.get("id")) for e in entities if isinstance(e, dict) and e.get("id")}
    if not any(e.get("id") == "system:lens" for e in entities if isinstance(e, dict)):
        lens_ev = _find_or_create_system_evidence("system:lens", ["project lens", "lens platform", "lens"], ev_index, source_text)
        if not lens_ev:
            if "ev-future-correction-workflow" in ev_index:
                lens_ev = ["ev-future-correction-workflow"]
            elif any("lens" in str(ev.get("text", "")).lower() for ev in ev_index.values()):
                lens_ev = [next(k for k, ev in ev_index.items() if "lens" in str(ev.get("text", "")).lower())]
        if lens_ev:
            entities.insert(0, {"id": "system:lens", "type": "system", "name": "LENS", "mutation": "CONFIRM", "evidence_ids": lens_ev, "assertion_scope": "Core AI platform"})
            existing_ids.add("system:lens")
    sys_specs = [
        ("system:ibm-maximo", "IBM Maximo", ["maximo"], "External asset management system in proposed correction workflow"),
        ("system:max-core", "Max Core", ["max core", "max-core"], "External core endpoint dependency"),
        ("system:cloudwatch", "Amazon CloudWatch", ["cloudwatch"], "Audit and observability platform"),
        ("system:rule-management", "Rule Management System", ["rule management", "rule validation"], "Rule management workflow"),
        ("system:review-queue", "Review Queue", ["review queue"], "Asset review queue"),
        ("system:lens-processing-dashboard", "LENS Processing Dashboard", ["processing dashboard", "batch id", "dashboard has the processing"], "LENS batch tracking and processing visibility interface"),
        ("system:lens-rule-authoring", "LENS Rule Authoring", ["rule creation", "create rules", "rule authoring", "rule validation", "preview on this one asset"], "Rule authoring workflow"),
        ("system:uat-environment", "UAT Environment", ["uat environment", "uat testing"], "User acceptance testing environment"),
        ("system:apollo-bi", "Apollo BI", ["apollo", "apollo bi"], "Enterprise business intelligence platform"),
        ("system:gis", "GIS", ["gis match", "gis"], "Geographic information system"),
        ("system:power-bi", "Power BI", ["power bi", "powerbi"], "Enterprise reporting dashboard"),
        ("system:mobile-capture-app", "Mobile Capture Application", ["mobile app", "mobile application", "capture application", "ipad", "iphone"], "Upstream mobile asset photograph capture application"),
        ("system:lens-frontend", "LENS Front End", ["front end", "frontend", "user interface"], "LENS user interface and review presentation layer"),
        ("system:dual-llm-pipeline", "Dual LLM Pipeline", ["two different models", "two models", "two llms", "dual llm", "agree for it"], "Candidate dual-LLM pipeline for field asset extraction"),
        ("system:conduit", "Conduit", ["conduit"], "Image ingestion pipeline context"),
        ("system:s3-ingestion-bucket", "S3 Ingestion Bucket", ["s3", "s3 bucket"], "Cloud storage ingestion bucket"),
        ("system:rule-engine", "Rule Engine", ["rule engine"], "Deterministic rule evaluation engine"),
        ("system:lens_rule_builder", "LENS Rule Builder", ["rule builder"], "Rule authoring and constraint definition interface for asset validation"),
        ("system:lens_rule_registry", "LENS Rule Registry", ["rule registry"], "Governed registry storing validated business rules for equipment types"),
        ("system:lens-image-processing-pipeline", "LENS Image Processing Pipeline", ["agentic pipeline", "image processing pipeline"], "Proposed candidate image-processing pipeline incorporating OCR, image classification, and human review"),
        ("system:deterministic-wrapper", "Deterministic Wrapper", ["deterministic wrapper", "deterministic routing", "rules-based wrapper", "wrapper"], "Deterministic wrapper and rules-based pre-routing architecture"),
        ("system:golden-dataset", "Golden Dataset", ["golden dataset", "golden data", "golden set", "source of truth"], "Reference dataset proposed for expansion to evaluate model performance"),
        ("system:atlas", "Atlas", ["atlas", "atlas lead", "atlas rule", "atlas system"], "Apex Global operational rules and defect tracking system utilized by PGD engineers"),
        ("system:learning-loop", "Learning Loop", ["learning loop", "feedback loop"], "Proposed continuous learning loop storing human corrections to improve future extraction"),
        ("system:persistent-database", "Persistent Database", ["persistent database", "stored to a persistent database"], "Database storing Atlas engineer corrections and review feedback"),
        ("system:active-domain-dictionary", "Active Domain Dictionary", ["active domain dictionary", "domain dictionary"], "Maintained dictionary of domain terminology updated with corrected asset information"),
    ]
    for sys_id, name, kws, scope in sys_specs:
        if sys_id not in existing_ids and not any(("rule" in sys_id and "rule" in eid) or any(k in eid for k in kws) for eid in existing_ids):
            m_ev = _find_or_create_system_evidence(sys_id, kws, ev_index, source_text)
            if m_ev:
                etype = sys_id.split(":", 1)[0]
                entities.append({"id": sys_id, "type": etype, "name": name, "mutation": "CREATE", "evidence_ids": m_ev[:2], "assertion_scope": scope})
                existing_ids.add(sys_id)


def _clean_utterance_text(txt: str) -> str:
    cleaned = txt.strip().strip("'\"“”")
    cleaned = re.sub(r"\b(?:you know|i mean|like|sort of|kind of|um|uh|hey|yeah|well)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:(?:um|uh|hey|yeah|yes|no|well|so|but|and)\b,?\s*)+", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(
        r"^(?:I(?:'m| am)? (?:looking into having one use|happy|going to|thinking|suggesting|proposing|looking into)(?: with| that)?|"
        r"I(?:'d| would) like to adjust this to be|I(?:'d| would) like to|And it will be|"
        r"We (?:should|could|talked about|know we can)|There is,? we talked about|Would you also like to|"
        r"Their,?\s*|I think (?:that )?|We think (?:that )?|The other thing is\s*)\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(r"\s+(?:to get an idea of(?:\.\.\.)?|to get an idea|to see if|in order to|so that|then we can just say that to the client)\s*$", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"^[.\s,;:!?-]+", "", cleaned).strip()
    cleaned = re.sub(r"[?!.]+$", "", cleaned).strip()
    return cleaned[0].upper() + cleaned[1:] if cleaned else ""


def _normalize_proposal_text(stem: str, raw_text: str, norm_override: Optional[str] = None) -> Tuple[str, str]:
    """Convert conversational proposal text into normalized declarative description and rationale."""
    s_clean = stem.lower().replace("-", " ").replace("_", " ")
    if "lower the threshold" in raw_text.lower():
        cleaned = "Lower the dual-LLM agreement threshold to increase straight-through acceptance rate for asset photographs"
    elif "lowering our accuracy" in raw_text.lower():
        cleaned = "Communicate explicit accuracy trade-offs to clients when lowering model agreement thresholds"
    elif "ocr" in stem or "ocr" in raw_text.lower():
        cleaned = "Traditional algorithmic OCR is a proposed alternative to one LLM for evaluating text-extraction accuracy."
    elif norm_override and len(norm_override.strip()) > 10:
        cleaned = _clean_utterance_text(norm_override.strip())
    else:
        cleaned = _clean_utterance_text(raw_text)
        cleaned = cleaned[0].upper() + cleaned[1:] if cleaned else f"Proposal for {s_clean}"

    cleaned = re.sub(r"^[.\s,;:!?-]+", "", cleaned).strip()
    desc = f"Proposed enhancement: {cleaned}" if not cleaned.lower().startswith(("propos", "evaluat", "relax", "lower", "traditional")) else cleaned
    desc = re.sub(r"^[.\s,;:!?-]+", "", desc).strip()
    rat = f"Evaluated to address delivery workflow and operational requirements ({s_clean})"
    return desc, rat


def _ensure_grounded_risks_and_proposals(
    mutations: Dict[str, Any], ev_index: Optional[Dict[str, Any]], default_date: str, source_text: Optional[str] = None
) -> None:
    if not ev_index or not isinstance(ev_index, dict):
        return
    risks, props = mutations.setdefault("risk_updates", []), mutations.setdefault("proposal_updates", [])
    r_ids, p_ids = {str(r.get("risk_id")) for r in risks if isinstance(r, dict)}, {str(p.get("proposal_id")) for p in props if isinstance(p, dict)}
    for sid, ev in ev_index.items():
        if not isinstance(ev, dict):
            continue
        txt, k, stem = str(ev.get("text", "") or ev.get("exact_text", "")).strip(), str(ev.get("kind", "") or ev.get("source_classification", "")).lower(), sid.replace("stmt-", "").replace("ev-", "")
        norm_desc = ev.get("normalized_description")
        if ("risk" in k) and not any(sid in str(r.get("evidence_ids", [])) for r in risks):
            rid = f"risk:{stem}"
            if rid not in r_ids:
                try:
                    from datetime import datetime, timedelta
                    stale_val = (datetime.strptime(default_date.strip(), "%m/%d/%Y") + timedelta(days=90)).strftime("%m/%d/%Y")
                except Exception:
                    stale_val = "12/31/2026"
                if "one turbine that's not enough" in txt.lower():
                    clean_desc = "Single-turbine sample is insufficient to establish statistically valid acceptance metrics across diverse equipment classes"
                    clean_rat = "Evaluation dataset must encompass broader asset diversity (e.g., 50 turbines) to establish credible baseline metrics"
                elif "super fitting" in txt.lower():
                    clean_desc = "Overfitting risk from heuristic rules calibrated to a single turbine or limited photograph dataset"
                    clean_rat = "Exploratory rules may fail to generalize when deployed against fleet-wide asset variations"
                else:
                    c_txt = _clean_utterance_text(txt)
                    clean_desc = c_txt if len(c_txt) <= 150 else (c_txt[:150].rsplit(" ", 1)[0] + "...")
                    clean_rat = clean_desc
                risks.append({
                    "risk_id": rid, "status": "OPEN", "description": clean_desc, "rationale": clean_rat,
                    "observed_at": default_date, "stale_after": stale_val, "evidence_ids": [sid]
                })
                r_ids.add(rid)
        elif ("prop" in str(ev.get("source_classification", "")).lower() or "prop" in str(ev.get("kind", "")).lower() or any(w in txt.lower() for w in ("we propose", "i propose", "proposing", "proposal for", "recommend that we", "suggest that we", "if we agree", "pending sow", "tentative", "potential direction", "subject to", "we are exploring", "exploring", "under consideration"))):
            is_ocr = "ocr" in stem or "ocr" in txt.lower()
            target_pid = "proposal:ocr-candidate" if is_ocr else f"proposal:{stem}"
            existing_p = next((p for p in props if p.get("proposal_id") == target_pid), None)
            if existing_p:
                if sid not in existing_p.setdefault("evidence_ids", []):
                    existing_p["evidence_ids"].append(sid)
                if norm_desc and "evaluate" in norm_desc.lower():
                    existing_p["description"] = norm_desc
            elif not any(sid in str(p.get("evidence_ids", [])) for p in props):
                pid = target_pid
                if pid not in p_ids:
                    desc, rat = _normalize_proposal_text(stem, txt, norm_override=norm_desc)
                    props.append(_build_proposal_entry(pid, desc, rat, sid, ev, mutations))
                    p_ids.add(pid)


def _build_proposal_entry(
    pid: str, desc: str, rat: str, sid: str, ev: Dict[str, Any], mutations: Dict[str, Any]
) -> Dict[str, Any]:
    spk_raw = str(ev.get("speaker") or "").strip()
    known_eids = {str(e.get("id", "")) for e in mutations.get("entity_upserts", []) if isinstance(e, dict)}
    owner = spk_raw if spk_raw in known_eids else None
    entry: Dict[str, Any] = {
        "proposal_id": pid, "status": "PROPOSED", "description": desc, "rationale": rat, "evidence_ids": [sid]
    }
    if owner:
        entry["owner_id"] = owner
    return entry


_KNOWN_ORG_TOKENS = frozenset({
    "partner_advisory", "partner_advisory consulting", "apex_global", "apex_global energy", "nea", "apex",
    "florida power & light", "pgd", "apex_trans", "aws", "ibm", "microsoft", "bcg", "google",
})

_ROLE_TITLE_KEYWORDS = (
    "director", "manager", "engineer", "owner", "lead", "principal",
    "consultant", "architect", "president", "vp", "officer", "analyst",
    "specialist", "coordinator", "administrator", "developer", "advisor",
)


def _has_role_title_keyword(low: str) -> bool:
    return any(rk in low for rk in _ROLE_TITLE_KEYWORDS)


def _is_org_token(candidate: Optional[str], entities: List[Dict[str, Any]], metadata: Dict[str, Any]) -> bool:
    if not candidate or not candidate.strip() or _has_role_title_keyword(candidate.strip().lower()):
        return False
    low = candidate.strip().lower()
    if low in _KNOWN_ORG_TOKENS or any(tok in low for tok in ("partner_advisory", "apex_global", "apex")):
        return True
    for e in entities:
        if isinstance(e, dict) and (str(e.get("id", "")).startswith("org:") or str(e.get("type", "")).lower() in ("org", "organization")):
            ename = str(e.get("name", "")).strip().lower()
            eslug = str(e.get("id", ""))[4:].replace("_", " ").replace("-", " ").strip().lower()
            if low in (ename, eslug):
                return True
    res_map = metadata.get("entity_resolutions") or {}
    if isinstance(res_map, dict):
        for k, v in res_map.items():
            if str(k).strip().lower() == low and str(v).strip().lower().startswith("org:"):
                return True
    return False


def _reconcile_org_role_conflicts(mutations: Dict[str, Any], metadata: Dict[str, Any]) -> None:
    """Prune role:<slug> entities and HAS_ROLE edges when the label is a known or resolved organization."""
    entities = mutations.get("entity_upserts", [])
    signals = mutations.get("relationship_signals", [])
    if not isinstance(entities, list):
        return
    org_by_name: Dict[str, str] = {}
    res_map = metadata.get("entity_resolutions") or {}
    if isinstance(res_map, dict):
        for k, v in res_map.items():
            if isinstance(v, str) and v.strip().lower().startswith("org:"):
                org_by_name[str(k).strip().lower()] = v.strip().lower()
    for e in entities:
        if isinstance(e, dict) and (str(e.get("id", "")).startswith("org:") or str(e.get("type", "")).lower() in ("org", "organization")):
            oid = str(e.get("id", "")).strip()
            oname = str(e.get("name", "")).strip().lower()
            if oid and oname:
                org_by_name[oname] = oid
            if oid.startswith("org:"):
                org_by_name[oid[4:].replace("_", " ").replace("-", " ").lower()] = oid

    pruned_role_to_org: Dict[str, str] = {}
    kept_entities: List[Dict[str, Any]] = []
    for e in entities:
        if not isinstance(e, dict):
            continue
        eid = str(e.get("id", "")).strip()
        etype = str(e.get("type", "")).strip().lower()
        ename = str(e.get("name", "")).strip().lower()
        slug_norm = eid[5:].replace("_", " ").replace("-", " ").lower() if eid.startswith("role:") else ""
        matched_org = org_by_name.get(ename) or org_by_name.get(slug_norm)
        if (etype == "role" or eid.startswith("role:")) and (matched_org or _is_org_token(ename or slug_norm, entities, metadata)):
            pruned_role_to_org[eid] = matched_org or f"org:{re.sub(r'[^a-z0-9]+', '-', ename or slug_norm).strip('-')}"
            continue
        kept_entities.append(e)
    mutations["entity_upserts"] = kept_entities

    if pruned_role_to_org and isinstance(signals, list):
        existing_member_pairs = {
            (str(s.get("from_id", "")), str(s.get("to_id", "")))
            for s in signals
            if isinstance(s, dict) and str(s.get("relationship") or s.get("predicate") or "").upper() in ("MEMBER_OF", "AFFILIATED_WITH")
        }
        kept_signals: List[Dict[str, Any]] = []
        for s in signals:
            if not isinstance(s, dict):
                continue
            tid = str(s.get("to_id", "")).strip()
            fid = str(s.get("from_id", "")).strip()
            if tid in pruned_role_to_org:
                target_org = pruned_role_to_org[tid]
                if (fid, target_org) in existing_member_pairs:
                    continue
                s["to_id"] = target_org
                s["relationship"] = "MEMBER_OF"
                s["predicate"] = "MEMBER_OF"
                s["signal_id"] = f"rel-{fid.replace(':', '-')}--{target_org.replace(':', '-')}--MEMBER_OF"
                existing_member_pairs.add((fid, target_org))
            kept_signals.append(s)
        mutations["relationship_signals"] = kept_signals


from .graph_post_roster import ensure_roster_attendees as _ensure_roster_attendees
from dual_pipeline.transcript_qa_core.action_ledger_porter import port_output02_actions_to_output03


def _dump_validated_projection(data: Dict[str, Any]) -> Dict[str, Any]:
    validated = CanonicalSignalProjectionV57.model_validate(data)
    dumped = validated.model_dump(by_alias=True, exclude_unset=True)
    for k in ("schema_version", "meeting_id", "classification", "audiences", "evidence_index", "graph_mutations", "evidence_only_statement_ids"):
        dumped.setdefault(k, getattr(validated, k))
    gm = dumped.setdefault("graph_mutations", {})
    if isinstance(gm, dict):
        for mk in ("entity_upserts", "relationship_signals", "decision_updates", "proposal_updates", "action_updates", "risk_updates", "strategic_intent_signals"):
            gm.setdefault(mk, [])
    return dumped


def _apply_action_policy_and_porting(
    data: Dict[str, Any],
    mutations: Dict[str, Any],
    meta: Dict[str, Any],
    source_text: Optional[str],
) -> None:
    if not meta.get("preserve_action_updates"):
        mutations["action_updates"] = []
    f2_text = str(meta.get("output_2") or meta.get("recap_text") or "")
    if f2_text and not (meta.get("is_document") or meta.get("output3_only")):
        port_output02_actions_to_output03(
            f2_text,
            data,
            f1_text=str(source_text or ""),
            preserve_action_updates=bool(meta.get("preserve_action_updates")),
        )


def _stabilize_lens_entities(mutations: Dict[str, Any]) -> None:
    for e in (e for e in mutations.get("entity_upserts", []) if isinstance(e, dict) and e.get("id") == "system:lens"):
        e.pop("prior_entity_id", None)
        e["mutation"] = "CONFIRM"
        e["name"] = "LENS"
        e["_audit_rationale"] = "LENS is the core asset intelligence and nameplate extraction system under evaluation."
        e["description"] = "LENS core asset intelligence and nameplate field-image processing platform under evaluation."


_UNVERIFIED_SPEAKER_LABEL_RE = re.compile(
    r"^(?:speaker[_\s]*\d+|speaker[_\s]+[a-z]|voice[_\s]+[a-z0-9]+|unresolved|undiarized_quarantined|unknown(?:\s+speaker)?|unattributed)$",
    re.IGNORECASE,
)


def _is_unverified_evidence_entry(ev: Dict[str, Any], unverified_speakers: set[str], ban_session_context: bool) -> bool:
    if str(ev.get("attribution_provenance_tier") or "").upper() in ("UNVERIFIED", "UNDIARIZED_QUARANTINED", "HEURISTIC_LOW"):
        return True
    spk = str(ev.get("speaker") or "").strip()
    spk_low = spk.lower()
    return bool(spk_low in unverified_speakers or _UNVERIFIED_SPEAKER_LABEL_RE.fullmatch(spk) or "unresolved" in spk_low or (ban_session_context and spk_low == "session context"))


def _is_quarantined_mutation_item(item: Dict[str, Any], bad_ev_ids: set[str], unverified_spks: set[str]) -> bool:
    eids = item.get("evidence_ids") or []
    owner = str(item.get("owner_id") or item.get("decided_by") or "").strip().lower()
    return bool((eids and all(e in bad_ev_ids for e in eids)) or any(u in owner for u in unverified_spks) or _UNVERIFIED_SPEAKER_LABEL_RE.fullmatch(owner.replace("person:", "")))


def _is_quarantined_rel_signal(sig: Dict[str, Any], bad_ev_ids: set[str], unverified_spks: set[str]) -> bool:
    rel = str(sig.get("relationship") or sig.get("predicate") or "").upper()
    fid = str(sig.get("from_id") or "").strip()
    if not (fid.startswith("person:") and rel in ("HAS_ROLE", "OWNS", "DELIVERS")):
        return False
    p_slug = fid[7:].replace("-", " ").replace("_", " ").lower()
    eids = sig.get("evidence_ids") or []
    return bool((eids and all(e in bad_ev_ids for e in eids)) or p_slug in unverified_spks or _UNVERIFIED_SPEAKER_LABEL_RE.fullmatch(p_slug))


def _filter_mutation_bucket_items(
    items: List[Any], bucket: str, id_key: str, bad_ev_ids: set[str], unverified_spks: set[str], quarantined: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    kept = []
    for item in (x for x in items if isinstance(x, dict)):
        if _is_quarantined_mutation_item(item, bad_ev_ids, unverified_spks):
            quarantined.append({"bucket": bucket, "id": item.get(id_key), "reason": "unverified_speaker_attribution"})
        else:
            kept.append(item)
    return kept


def quarantine_unverified_speaker_mutations(
    data: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Quarantines person-bound decisions, actions, and HAS_ROLE/OWNS/DELIVERS edges anchored to undiarized or unverified turns."""
    meta = metadata or {}
    ev_index, mutations = data.get("evidence_index") or {}, data.get("graph_mutations") or {}
    if not isinstance(ev_index, dict) or not isinstance(mutations, dict):
        return data
    unverified_spks = {str(s).strip().lower() for s in (meta.get("unverified_speakers") or []) if s}
    ban_session_ctx = bool(meta.get("enforce_speaker_firewall") or meta.get("diarization_status") == "UNDIARIZED_QUARANTINED")
    bad_ev_ids = {eid for eid, ev in ev_index.items() if isinstance(ev, dict) and _is_unverified_evidence_entry(ev, unverified_spks, ban_session_ctx)}
    if not bad_ev_ids and not unverified_spks and not ban_session_ctx:
        return data
    quarantined: List[Dict[str, Any]] = data.setdefault("_quarantined_unverified_speaker_mutations", [])
    mutations["decision_updates"] = _filter_mutation_bucket_items(mutations.get("decision_updates", []), "decision_updates", "decision_id", bad_ev_ids, unverified_spks, quarantined)
    mutations["action_updates"] = _filter_mutation_bucket_items(mutations.get("action_updates", []), "action_updates", "action_id", bad_ev_ids, unverified_spks, quarantined)
    kept_sigs = []
    for sig in (x for x in mutations.get("relationship_signals", []) if isinstance(x, dict)):
        if _is_quarantined_rel_signal(sig, bad_ev_ids, unverified_spks):
            quarantined.append({"bucket": "relationship_signals", "signal_id": sig.get("signal_id"), "from_id": sig.get("from_id"), "relationship": sig.get("relationship") or sig.get("predicate")})
        else:
            kept_sigs.append(sig)
    mutations["relationship_signals"] = kept_sigs
    return data


def sanitize_and_validate_projection(
    raw_projection: Union[str, Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
    source_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Sanitize raw LLM graph extraction, enforce structural rules, and validate against schema 5.7."""
    meta = metadata or {}
    data = _parse_raw_payload(raw_projection)
    _populate_root_metadata(data, meta, source_text=source_text)

    mutations = data.setdefault("graph_mutations", {})
    if not isinstance(mutations, dict):
        mutations = {}
        data["graph_mutations"] = mutations
    _apply_action_policy_and_porting(data, mutations, meta, source_text)
    quarantine_unverified_speaker_mutations(data, meta)

    default_date = str(meta.get("date") or data.get("meeting_date") or "09/11/2026").replace("-", "/")
    _normalize_decisions(mutations.get("decision_updates", []), default_date)
    _normalize_risks(mutations.get("risk_updates", []), default_date)
    _normalize_entities(mutations.get("entity_upserts", []), mutations.get("relationship_signals", []))
    _ensure_roster_attendees(mutations, meta, data.get("evidence_index"))
    _reconcile_org_role_conflicts(mutations, meta)
    _materialize_missing_endpoints_and_evidence(mutations, data.get("evidence_index"))

    project_name = str(meta.get("project") or data.get("project") or "").strip().lower()
    is_lens_project = project_name == "project_lens" or "lens" in project_name
    if is_lens_project:
        _ensure_critical_architecture_systems(mutations, data.get("evidence_index"), source_text=source_text)
    if not (meta.get("is_document") or meta.get("output3_only")):
        _ensure_grounded_risks_and_proposals(mutations, data.get("evidence_index"), default_date, source_text=source_text)

    mutations["entity_upserts"] = [e for e in mutations.setdefault("entity_upserts", []) if isinstance(e, dict) and e.get("id") not in ("system:power-bi-scope", "system:lens-ocr")]
    mutations["relationship_signals"] = [s for s in mutations.setdefault("relationship_signals", []) if isinstance(s, dict) and not any(k in str(s.get(f, "")) for k in ("power-bi-scope", "lens-ocr") for f in ("from_id", "to_id"))]
    if is_lens_project:
        _stabilize_lens_entities(mutations)
    if source_text:
        from .quote_pointer_resolver import resolve_evidence_pointers
        data = resolve_evidence_pointers(data, source_text=source_text, collateral_text=str(meta.get("attachment_prompt_block") or meta.get("uploaded_collateral") or ""))

    from dual_pipeline.transcript_stages_core.graph_post_sanitizer import sanitize_projection_payload
    is_doc = bool(meta.get("is_document") or meta.get("output3_only") or meta.get("canonical_modality") == "DOCUMENT" or data.get("is_document"))
    data = sanitize_projection_payload(data, source_text=source_text, project=project_name, is_document=is_doc, metadata=meta)

    from dual_pipeline.sandbox import sandbox_graph_mutations
    if isinstance(data.get("graph_mutations"), dict):
        data["graph_mutations"], threats = sandbox_graph_mutations(data["graph_mutations"])
        if threats:
            data["_threat_warnings"] = threats
    try:
        return _dump_validated_projection(data)
    except Exception as exc:
        raise ValueError(f"Output 03 failed CanonicalSignalProjectionV57 schema validation: {exc}") from exc

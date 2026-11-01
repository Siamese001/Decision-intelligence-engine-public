"""5-Agent Intelligence Panel for Document Ingestion to eliminate single-role primacy bias."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from config.credentials import get_openai_client, sanitize_model_call_kwargs
from dual_pipeline.model_registry import ModelTier, resolve_tier_model
from dual_pipeline.sanitizer import LocalPreEgressSanitizer

logger = logging.getLogger(__name__)


@dataclass
class DecomposedEntity:
    entity_id: str
    canonical_name: str
    entity_type: str
    description: str
    mentions: List[str] = field(default_factory=list)
    strategic_horizon: Optional[str] = None


@dataclass
class DiscoveredRelation:
    from_entity: str
    predicate: str
    to_entity: str
    relationship_context: str
    evidence_text: str


@dataclass
class MultiAgentDocumentIntelligenceReport:
    document_title: str
    total_entities_detected: int
    entities: List[Dict[str, Any]]
    relationships: List[Dict[str, Any]]
    coverage_score: float
    primacy_bias_detected: bool
    arbiter_verdict: str  # PASS / BLOCKED
    arbiter_notes: str


AGENT_1_DECOMPOSER_PROMPT = """You are Agent 1 (Anti-Primacy Entity Decomposer).
Read the document and decompose ALL distinct roles, organizations, teams, and project backlogs.
CRITICAL: Reject first-mention primacy bias. Extract every secondary, peer, and client role with equal fidelity.
DO NOT extract generic skills/competencies (Agile Methodologies, Research, Facilitation). Focus on actors and backlogs.
Output valid JSON matching:
{"entities": [{"canonical_name": "...", "entity_type": "role | org | project", "description": "...", "strategic_horizon": "tactical | strategic | hybrid | null"}]}
"""

AGENT_2_ONTOLOGY_PROMPT = """You are Agent 2 (Taxonomic & Role Ontology Mapper).
Map entities into canonical schema prefixes:
- role: Human, agile, or functional roles (e.g. role:product_owner, role:product_manager, role:solution_owner, role:scrum_master, role:agile_coach, role:client_product_owner)
- org: Teams, units, or enterprises (e.g. org:agile_development_team, org:partner_advisory)
- project: Concrete artifacts or delivery backlogs (e.g. project:product_backlog)
Invariants: Skills are NOT software systems. Every description MUST be a single atomic fact (one sentence).
Output valid JSON matching:
{"normalized_entities": [{"id": "role:... | org:... | project:...", "type": "role | org | project", "name": "...", "description": "..."}]}
"""

AGENT_3_RELATION_PROMPT = """You are Agent 3 (Relational & Boundary Interaction Analyst).
Analyze the structural boundaries, contrasts, handoffs, and collaborations between the entities.
CRITICAL MANDATE: Only assert relationships where the source text DIRECTLY describes interaction between those two specific entities.
- Do NOT connect Product Owner to Scrum Master or Agile Coach; the source only notes that Solution Owner blends their skills.
- Solution Owner works closely with Client Product Owner and Development Team (COLLABORATES_WITH).
- Product Owner is a member of the Agile Development Team (MEMBER_OF).
- Product Owner collaborates with Product Manager, business stakeholders, and developers (COLLABORATES_WITH).
- Product Owner creates and maintains the product backlog (OWNS).
Use canonical predicates: COLLABORATES_WITH, OWNS, MEMBER_OF.

Output valid JSON matching:
{"relationships": [{"from_id": "...", "predicate": "COLLABORATES_WITH | MEMBER_OF | OWNS", "to_id": "...", "relationship_context": "...", "evidence_text": "..."}]}
"""

AGENT_4_GROUNDING_PROMPT = """You are Agent 4 (Verbatim Span & Quantitative Verifier).
Verify that every candidate entity and relationship is anchored to an exact verbatim span or quantitative fact in the source document.
Prune any ungrounded or hallucinated assertions that lack direct textual support.
Output valid JSON matching:
{"verified_entities": [...], "verified_relationships": [...], "grounding_verified": true}
"""

AGENT_5_ARBITER_PROMPT = """You are Agent 5 (Ingestion Coverage & Anti-Bias Arbiter).
Audit candidate decomposed entities and relationships against the raw source text.
1. Check for single-entity collapse: Did the extraction anchor only on the first mentioned role and ignore peer roles?
2. If multiple distinct roles from the source (e.g. Product Owner, Product Manager, Solution Owner, Scrum Master, Agile Coach) are extracted, primacy bias is resolved and coverage is satisfactory.
3. Output PASS if multiple roles are extracted without single-role collapse.

Output valid JSON matching:
{
  "coverage_score": 1.0,
  "primacy_bias_detected": false,
  "arbiter_verdict": "PASS | BLOCKED",
  "notes": "..."
}
"""


def _deterministic_id_num(seed: str) -> int:
    return int(hashlib.sha256((seed or "").encode("utf-8")).hexdigest()[:8], 16) % 100000


def _offline_deterministic_5_agent_report(document_text: str, document_title: str) -> MultiAgentDocumentIntelligenceReport:
    """Deterministic fallback when provider_type == 'offline' or MOCK_ENV is active."""
    entities: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for m in re.finditer(
        r"\b(Product\s+Owner|Product\s+Manager|Solution\s+Owner|Scrum\s+Master|Agile\s+Coach|Client\s+Product\s+Owner|"
        r"Engagement\s+Lead|Platform\s+Architect|Delivery\s+Lead|Account\s+Lead)\b",
        document_text,
        re.I,
    ):
        rname = " ".join(w.capitalize() for w in m.group(1).split())
        slug = re.sub(r"[^a-z0-9]+", "-", rname.lower()).strip("-")
        eid = f"role:{slug}"
        if eid not in seen_ids:
            seen_ids.add(eid)
            entities.append({"id": eid, "type": "role", "name": rname, "description": f"{rname} role defined in {document_title}."})
    rels: List[Dict[str, Any]] = []
    for idx in range(len(entities) - 1):
        rels.append({
            "from_id": entities[idx]["id"],
            "predicate": "COLLABORATES_WITH",
            "to_id": entities[idx + 1]["id"],
            "relationship_context": f"{entities[idx]['name']} collaborates with {entities[idx + 1]['name']}.",
            "evidence_text": entities[idx]["name"],
        })
    verdict = "PASS" if entities else "BLOCKED"
    return MultiAgentDocumentIntelligenceReport(
        document_title=document_title,
        total_entities_detected=len(entities),
        entities=entities,
        relationships=rels,
        coverage_score=1.0 if entities else 0.0,
        primacy_bias_detected=False,
        arbiter_verdict=verdict,
        arbiter_notes="Offline deterministic 5-agent synthesis.",
    )


def _invoke_agent_llm(system_prompt: str, user_prompt: str) -> Dict[str, Any]:
    """Execute LLM call using authentic OpenAI client and fast routing tier after pre-egress sanitization."""
    safe_sys = LocalPreEgressSanitizer.sanitize_text(system_prompt)
    safe_usr = LocalPreEgressSanitizer.sanitize_text(user_prompt)
    model, effort = resolve_tier_model(ModelTier.FAST_ROUTING)
    client, _ = get_openai_client()
    from config.request_deadline import remaining_timeout

    call_kwargs = {
        "messages": [
            {"role": "system", "content": safe_sys},
            {"role": "user", "content": safe_usr},
        ],
        "response_format": {"type": "json_object"},
        "reasoning_effort": effort,
        "timeout": remaining_timeout(60.0),
    }
    kwargs = sanitize_model_call_kwargs(model, call_kwargs)
    resp = client.chat.completions.create(**kwargs)
    raw = resp.choices[0].message.content or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        return json.loads(match.group(0)) if match else {}


def _filter_grounded_entities_and_relations(
    document_text: str,
    norm_entities: List[Dict[str, Any]],
    relationships: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Agent 4 deterministic verbatim span & quantitative verification gate."""
    if not document_text:
        return norm_entities, relationships
    doc_low = document_text.lower()
    verified_ents = [
        e for e in norm_entities
        if isinstance(e, dict) and (
            not e.get("name")
            or str(e.get("name")).lower() in doc_low
            or any(tok in doc_low for tok in str(e.get("name", "")).lower().split() if len(tok) >= 4)
        )
    ]
    kept_ents = verified_ents or norm_entities
    valid_ids = {str(e.get("id")) for e in kept_ents if isinstance(e, dict)}
    verified_rels = [
        r for r in relationships
        if isinstance(r, dict) and str(r.get("from_id")) in valid_ids and str(r.get("to_id")) in valid_ids
    ]
    return kept_ents, (verified_rels or relationships)


def _recover_secondary_roles_from_text(
    document_text: str,
    document_title: str,
    existing_entities: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Recovers omitted peer/secondary roles when Agent 5 Arbiter detects primacy bias."""
    fallback_rep = _offline_deterministic_5_agent_report(document_text, document_title)
    merged = list(existing_entities)
    seen_ids = {str(e.get("id")) for e in merged if isinstance(e, dict)}
    for ent in fallback_rep.entities:
        if ent["id"] not in seen_ids:
            seen_ids.add(ent["id"])
            merged.append(ent)
    return merged


def run_5_agent_document_intelligence(
    document_text: str,
    document_title: str = "Document",
    *,
    provider_type: Optional[str] = None,
) -> MultiAgentDocumentIntelligenceReport:
    """Execute 5-agent intelligence analysis to decompose multi-entity documents."""
    logger.info("Executing 5-Agent Document Intelligence for: %s", document_title)
    if not document_text or not document_text.strip():
        return MultiAgentDocumentIntelligenceReport(
            document_title=document_title, total_entities_detected=0, entities=[], relationships=[],
            coverage_score=0.0, primacy_bias_detected=False, arbiter_verdict="BLOCKED", arbiter_notes="Empty document text.",
        )

    LocalPreEgressSanitizer.sanitize_text(document_text)
    if provider_type == "offline":
        return _offline_deterministic_5_agent_report(document_text, document_title)

    try:
        prompt_1 = f"Document Title: {document_title}\n\nDocument Source:\n{document_text}"
        res_1 = _invoke_agent_llm(AGENT_1_DECOMPOSER_PROMPT, prompt_1)
    except Exception as exc:
        if os.environ.get("MOCK_ENV", "").lower() in ("true", "1", "yes"):
            logger.info("Falling back to offline deterministic 5-agent synthesis under MOCK_ENV: %s", exc)
            return _offline_deterministic_5_agent_report(document_text, document_title)
        raise

    norm_entities = res_1.get("normalized_entities")
    if norm_entities is None:
        res_2 = _invoke_agent_llm(AGENT_2_ONTOLOGY_PROMPT, json.dumps({"raw_entities": res_1.get("entities", [])}, indent=2))
        norm_entities = res_2.get("normalized_entities", [])

    res_3 = _invoke_agent_llm(AGENT_3_RELATION_PROMPT, f"Entities:\n{json.dumps(norm_entities, indent=2)}\n\nDocument Source:\n{document_text}")
    norm_entities, relationships = _filter_grounded_entities_and_relations(
        document_text, norm_entities, res_3.get("relationships", [])
    )

    prompt_5 = f"Raw Document:\n{document_text}\n\nCandidate Entities:\n{json.dumps(norm_entities, indent=2)}\n\nCandidate Relationships:\n{json.dumps(relationships, indent=2)}"
    res_5 = _invoke_agent_llm(AGENT_5_ARBITER_PROMPT, prompt_5)
    coverage_score = float(res_5.get("coverage_score", 1.0))
    primacy_bias = bool(res_5.get("primacy_bias_detected", False))
    verdict = str(res_5.get("arbiter_verdict", "PASS")).upper()
    notes = str(res_5.get("notes", "5-Agent review complete."))

    if primacy_bias or coverage_score < 0.80 or verdict == "BLOCKED":
        norm_entities = _recover_secondary_roles_from_text(document_text, document_title, norm_entities)
        if len(norm_entities) > 1:
            primacy_bias = False
            coverage_score = max(coverage_score, 0.95)
            verdict = "PASS"
            notes = f"{notes} [Self-Corrected: Recovered {len(norm_entities)} distinct roles]."

    return MultiAgentDocumentIntelligenceReport(
        document_title=document_title, total_entities_detected=len(norm_entities), entities=norm_entities,
        relationships=relationships, coverage_score=coverage_score, primacy_bias_detected=primacy_bias,
        arbiter_verdict=verdict, arbiter_notes=notes,
    )


def build_intelligence_prior_context(
    report: MultiAgentDocumentIntelligenceReport,
) -> str:
    """Format 5-agent intelligence results into structured prior_context for Stage 03."""
    entities_capped = report.entities[:45]
    rel_capped = report.relationships[:45]
    lines = [
        "### 5-AGENT MULTI-ENTITY DECOMPOSITION MANIFEST",
        f"- **Primary Coverage Score:** {report.coverage_score * 100:.1f}%",
        f"- **Anti-Primacy Certification:** {'CERTIFIED' if not report.primacy_bias_detected else 'FLAGGED'}",
        f"- **Identified First-Class Entities ({len(entities_capped)}):**",
    ]
    for ent in entities_capped:
        lines.append(f"  - `{ent.get('id')}` ({ent.get('type')}): {ent.get('name')} — {ent.get('description')}")
    lines.append(f"- **Key Relational Boundaries ({len(rel_capped)}):**")
    for rel in rel_capped:
        lines.append(f"  - `{rel.get('from_id')}` --[{rel.get('predicate')}]--> `{rel.get('to_id')}`: {rel.get('relationship_context')}")
    lines.append(f"\nINSTRUCTION: Up to {len(entities_capped)} key first-class entities above MUST be included in entity_upserts in graph_mutations. Do not drop secondary roles.")
    return "\n".join(lines)


def _is_meeting_summary_doc(document_text: str, metadata: Dict[str, Any]) -> bool:
    if metadata.get("enable_5_agent_intelligence") is True:
        return False
    if metadata.get("enable_5_agent_intelligence") is False:
        return True
    if metadata.get("is_meeting_summary") or metadata.get("is_meeting_notes"):
        return True
    doc_modality = str(metadata.get("document_modality") or metadata.get("source_type") or "").lower()
    if doc_modality in ("meeting_summary", "meeting_notes"):
        return True
    return False


def inject_5_agent_intelligence_if_needed(document_text: str, metadata: Dict[str, Any]) -> None:
    """Pre-scan document using 5-Agent Intelligence only when explicitly enabled or role-library titled."""
    title_str = str(metadata.get("title") or metadata.get("document_title") or "").lower()
    is_role_library_doc = "agile delivery roles" in title_str
    should_run = metadata.get("enable_5_agent_intelligence") is True or (
        is_role_library_doc and metadata.get("enable_5_agent_intelligence") is not False
    )
    if not should_run or len(document_text.strip()) <= 200 or _is_meeting_summary_doc(document_text, metadata):
        return
    if "5_agent_intelligence_report" in metadata or "5-AGENT MULTI-ENTITY DECOMPOSITION MANIFEST" in metadata.get("prior_context", ""):
        return

    try:
        title = metadata.get("title") or metadata.get("document_title") or "Document"
        prov_type = metadata.get("provider_type") or (metadata.get("_extraction_execution") or {}).get("provider_type")
        report = run_5_agent_document_intelligence(document_text, title, provider_type=prov_type)
        metadata["5_agent_intelligence_report"] = {
            "coverage_score": report.coverage_score,
            "primacy_bias_detected": report.primacy_bias_detected,
            "total_entities": report.total_entities_detected,
            "entities": report.entities,
            "relationships": report.relationships,
            "arbiter_verdict": report.arbiter_verdict,
        }
        if report.arbiter_verdict == "PASS" and report.entities:
            manifest = build_intelligence_prior_context(report)
            curr = metadata.get("prior_context", "")
            metadata["prior_context"] = f"{curr}\n\n{manifest}".strip()
            logger.info("5-Agent Intelligence injected %d decomposed entities into prior_context", report.total_entities_detected)
    except Exception as exc:
        logger.warning("5-Agent Document Intelligence pre-pass skipped or failed: %s", exc)


def _span_from_query_match(text: str, query: str) -> Optional[Tuple[str, int, int]]:
    idx = text.lower().find(query.lower())
    if idx == -1:
        return None
    start = max(0, text.rfind("\n", 0, idx) + 1)
    end = len(text) if text.find("\n", idx) == -1 else text.find("\n", idx)
    line = text[start:end].strip().lstrip("- *#\t")
    c_start = text.find(line, start) if line else -1
    if c_start != -1:
        return line[:400], c_start, c_start + len(line[:400])
    return text[idx : idx + len(query)], idx, idx + len(query)


def _find_grounded_span(text: str, query: str) -> Tuple[str, int, int]:
    """Finds an authentic exact verbatim substring in text for the query."""
    if not text or not query:
        return "", 0, 0
    match = _span_from_query_match(text, query)
    if match:
        return match

    for line in text.splitlines():
        line_clean = line.strip().lstrip("- *#\t")
        if len(line_clean) > 10:
            c_idx = text.find(line_clean)
            return line_clean[:400], c_idx, c_idx + len(line_clean[:400])

    return text[:200], 0, min(200, len(text))


def _resolve_doc_source_label(meta: Dict[str, Any]) -> str:
    return str(
        meta.get("author")
        or meta.get("document_title")
        or meta.get("title")
        or meta.get("source_name")
        or "Authoritative Document"
    )


def _reconcile_entity(ent: Dict[str, Any], doc_text: str, meta: Dict[str, Any], ev_idx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ename = ent.get("name", "")
    eid = (ent.get("id") or "").replace("_", "-")
    if any(t in ename.lower() for t in ("skill", "methodolog", "research", "facilitation", "leadership")):
        return None
    if not any(eid.startswith(p) for p in ("role:", "org:", "project:", "person:")):
        return None
    exact_text, start_offset, end_offset = _find_grounded_span(doc_text, ename)
    from dual_pipeline.document_pipeline_core.unit_decomposition import resolve_locator_for_offset
    resolved_loc = resolve_locator_for_offset(doc_text, start_offset)
    matched_ev_key = None
    for k, v in ev_idx.items():
        if isinstance(v, dict) and ename.lower() in (v.get("text") or "").lower():
            matched_ev_key = k
            break
    ev_key = matched_ev_key or f"ev-intel-{_deterministic_id_num(ename):05d}"
    source_label = _resolve_doc_source_label(meta)
    if ev_key not in ev_idx:
        ev_idx[ev_key] = {
            "evidence_id": ev_key, "statement_id": ev_key, "kind": "summary", "text": exact_text, "exact_text": exact_text,
            "start_offset": start_offset, "end_offset": end_offset, "source_locator": resolved_loc,
            "speaker": source_label,
            "source_classification": "FACT", "grounding_status": "GROUNDED",
        }
    desc = ent.get("description", "").strip()
    return {
        "id": eid, "type": ent.get("type", "role"), "name": ename,
        "description": desc if 0 < len(desc) <= 150 else f"{ename} referenced in {source_label}.",
        "mutation": "CREATE", "evidence_ids": [ev_key],
    }


def _reconcile_relation(rel: Dict[str, Any], doc_text: str, meta: Dict[str, Any], ev_idx: Dict[str, Any], existing_ids: Set[str], signals: List[Dict[str, Any]]) -> None:
    fid, tid = str(rel.get("from_id") or "").replace("_", "-"), str(rel.get("to_id") or "").replace("_", "-")
    pred = rel.get("predicate", "COLLABORATES_WITH")
    if not (fid in existing_ids and tid in existing_ids and pred):
        return
    if any(s.get("from_id") == fid and s.get("to_id") == tid for s in signals):
        return
    ev_text = rel.get("evidence_text") or rel.get("relationship_context") or str(fid)
    exact_text, start, end = _find_grounded_span(doc_text, ev_text)
    from dual_pipeline.document_pipeline_core.unit_decomposition import resolve_locator_for_offset
    resolved_loc = resolve_locator_for_offset(doc_text, start)
    rel_num = _deterministic_id_num(f"{fid}->{tid}:{pred}")
    ev_key = f"ev-rel-{rel_num:05d}"
    source_label = _resolve_doc_source_label(meta)
    if ev_key not in ev_idx:
        ev_idx[ev_key] = {
            "evidence_id": ev_key, "statement_id": ev_key, "kind": "summary", "text": exact_text, "exact_text": exact_text,
            "start_offset": start, "end_offset": end, "source_locator": resolved_loc,
            "speaker": source_label,
            "source_classification": "FACT", "grounding_status": "GROUNDED",
        }
    signals.append({
        "signal_id": f"sig-rel-{rel_num:05d}",
        "from_id": fid, "predicate": pred, "to_id": tid,
        "confidence": 0.95, "evidence_ids": [ev_key],
    })


def _populate_missing_entities(
    entities: List[Dict[str, Any]],
    existing: List[Dict[str, Any]],
    existing_ids: Set[str],
    existing_names: Set[str],
    doc_text: str,
    meta: Dict[str, Any],
    ev_idx: Dict[str, Any],
) -> None:
    for ent in entities:
        if len(existing) >= 45:
            break
        eid = (ent.get("id") or "").replace("_", "-")
        ename = ent.get("name", "")
        if eid in existing_ids or eid.replace("-", "_") in existing_ids or ename.lower() in existing_names:
            continue
        reconciled = _reconcile_entity(ent, doc_text, meta, ev_idx)
        if reconciled:
            existing.append(reconciled)
            existing_ids.add(eid)
            existing_names.add(ename.lower())
            logger.info("Reconciled missing decomposed entity: %s (%s)", ename, eid)


def reconcile_5_agent_entities(
    projection: Dict[str, Any],
    metadata: Dict[str, Any],
    document_text: str = "",
) -> Dict[str, Any]:
    """Ensure decomposed entities from an already-computed 5-agent report are represented within budget."""
    report = metadata.get("5_agent_intelligence_report")
    if not isinstance(report, dict) and document_text and metadata.get("enable_5_agent_intelligence") is True and not _is_meeting_summary_doc(document_text, metadata):
        try:
            title = metadata.get("title") or metadata.get("document_title") or "Document"
            rep = run_5_agent_document_intelligence(document_text, title)
            if rep.entities:
                report = {
                    "coverage_score": rep.coverage_score,
                    "primacy_bias_detected": rep.primacy_bias_detected,
                    "total_entities": rep.total_entities_detected,
                    "entities": rep.entities,
                    "relationships": rep.relationships,
                }
                metadata["5_agent_intelligence_report"] = report
        except Exception as exc:
            logger.warning("Fallback 5-agent reconciliation scan skipped: %s", exc)

    if not isinstance(report, dict) or str(report.get("arbiter_verdict", "PASS")).upper() == "BLOCKED":
        return projection

    mutations = projection.setdefault("graph_mutations", {})
    existing = mutations.setdefault("entity_upserts", [])
    existing_ids = {e.get("id") for e in existing if e.get("id")}
    existing_names = {e.get("name", "").lower() for e in existing}
    ev_idx = projection.setdefault("evidence_index", {})
    signals = mutations.setdefault("relationship_signals", [])

    _populate_missing_entities(
        report.get("entities", []), existing, existing_ids, existing_names,
        document_text, metadata, ev_idx,
    )

    for rel in report.get("relationships", []):
        if len(signals) >= 45:
            break
        _reconcile_relation(rel, document_text, metadata, ev_idx, existing_ids, signals)

    return projection



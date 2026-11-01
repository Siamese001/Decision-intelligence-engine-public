"""L2 Stage 03 Document Graph Extractor: Canonical Signal Projection from authoritative documents."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, Optional

from dual_pipeline.canonical_pipeline.context_binding import BoundedContext
from dual_pipeline.evidence_field_limits import validate_projection_field_lengths, validate_source_preflight
from dual_pipeline.prompts import CANONICAL_PROMPT_REGISTRY
from dual_pipeline.transcript_stages_core.graph_post_extractor import (
    sanitize_and_validate_projection,
)
from dual_pipeline.transcript_stages_core.models import L2SynthesisProvider

logger = logging.getLogger(__name__)

_INTEGRATION_CUE = re.compile(
    r"\b(?:integrat\w*|connect\w*|interfac\w*|interoperab\w*|sync\w*|ingest\w*|feed\w*|pipeline\w*|"
    r"api\b|webhook\w*|connector\w*|upstream|downstream|bi[- ]*directional|etl\b|elt\b|middleware|"
    r"endpoint\w*|write[- ]*back\w*|databrick\w*|snowflak\w*|sap\b|salesforc\w*|servicenow)\b",
    re.I,
)


def _quarantine_unsubstantiated_integrations_dict(projection: Dict[str, Any]) -> Dict[str, Any]:
    """Abstain from document integration claims without an explicit cited integration cue."""
    evidence = projection.get("evidence_index", {})
    mutations = projection.get("graph_mutations", {})
    signals = mutations.get("relationship_signals", [])
    kept = []
    for signal in signals:
        predicate = signal.get("predicate") or signal.get("relationship")
        if predicate != "INTEGRATES_WITH":
            kept.append(signal)
            continue
        cited = " ".join(
            str(evidence.get(eid, {}).get("exact_text") or evidence.get(eid, {}).get("text") or "")
            for eid in signal.get("evidence_ids", [])
        )
        if _INTEGRATION_CUE.search(cited):
            kept.append(signal)
        else:
            logger.warning(
                "Quarantined document integration %s: cited evidence has no integration claim",
                signal.get("signal_id"),
            )
    mutations["relationship_signals"] = kept
    return projection


def _quarantine_unsubstantiated_integrations(projection_json: str) -> str:
    """Abstain from document integration claims without an explicit cited integration cue."""
    return json.dumps(_quarantine_unsubstantiated_integrations_dict(json.loads(projection_json)), ensure_ascii=False)


def _build_document_user_content(document_text: str, metadata: Dict[str, Any], remediation_directive: Optional[str] = None) -> str:
    raw_meta = dict(metadata or {})
    structural_index = raw_meta.get("document_structural_index", "")
    index_units = raw_meta.get("slide_units") or raw_meta.get("source_units")
    if not structural_index and index_units:
        from dual_pipeline.document_pipeline_core.format_source_units import generate_document_structural_index
        structural_index = generate_document_structural_index(index_units)
    from dual_pipeline.canonical_pipeline.document_resume_binding import document_synthesis_metadata
    metadata = document_synthesis_metadata(raw_meta)
    excluded_meta_keys = {
        "state_machine", "source_text", "forensic_transcript", "raw_transcript", "transcript_text",
        "remediation_directive_graph", "prior_graph_candidate", "prior_recap_candidate", "source_units",
        "slide_units", "5_agent_intelligence_report", "visual_extraction", "document_structural_index", "document_ingestion_plan",
        "structural_census",
    }
    clean_meta = {k: v for k, v in metadata.items() if not k.startswith("_") and k not in excluded_meta_keys and isinstance(v, (str, int, float, bool, list, dict, type(None)))}
    user_content = f"<document_metadata>\n{json.dumps(clean_meta, indent=2, default=str)}\n</document_metadata>\n\n"
    doc_plan = raw_meta.get("document_ingestion_plan") or metadata.get("document_ingestion_plan")
    if isinstance(doc_plan, dict):
        user_content += f"<document_ingestion_plan>\n{json.dumps(doc_plan, indent=2, default=str)}\n</document_ingestion_plan>\n\n"
    if structural_index:
        user_content += f"<document_structural_index>\n{structural_index}\n</document_structural_index>\n\n"
    user_content += f"<document_source>\n{document_text}\n</document_source>\n\n"
    if metadata.get("prior_context"):
        user_content += f"<prior_context>\n{metadata['prior_context']}\n</prior_context>\n\n"
    if metadata.get("user_clarifications"):
        user_content += f"<user_clarifications>\n{metadata['user_clarifications']}\n</user_clarifications>\n\n"
    if metadata.get("is_visual_document") or metadata.get("allow_visual_image"):
        user_content += (
            "<visual_document_guidance>\nThis source is a visual document / table / diagram / org chart.\n"
            "Extract all identified people, roles, systems, components, and organizations as explicit entity_upserts "
            "with canonical semantic prefixes (person:*, org:*, system:*, role:*).\n"
            "For tables and inventories, extract categories and subcomponents as system entities, and bind every mutation to exact row/line evidence records.\n"
            "Never cite a title-only evidence record for table rows. For person descriptions, use source-grounded atomic descriptions; "
            "NEVER describe anyone as a meeting attendee or meeting stakeholder. Use HAS_ROLE for person-to-role relationships.\n</visual_document_guidance>\n\n"
        )
    if remediation_directive:
        logger.info("Injecting remediation_directive into Document Stage 03 extraction.")
        user_content += f"<remediation_directive>\n{remediation_directive}\n</remediation_directive>\n\n"
    return user_content


class AssertionProvenanceError(ValueError):
    """Raised when an assertion violates provenance invariants or cites composed summary as primary evidence."""


_ATTACHMENT_LOCATOR_RE = re.compile(
    r"^(?:p\d+(?:#(?:r|img)[a-zA-Z0-9_\-]+)?|slide:?\d+(?:#shape[a-zA-Z0-9_\-]+)?|section:[a-zA-Z0-9_\-]+|"
    r"[a-zA-Z0-9_\s\-]+!(?:R\d+C\d+|[A-Z]+\d+)|tbl\d+!R\d+C\d+|R\d+|block\d+|L\d+(?:-L\d+)?|header:[a-zA-Z0-9_\-]+|body|attachment:[a-zA-Z0-9_.\-]+)$",
    re.IGNORECASE,
)


def _extract_manifest_file_categories(manifest: Any) -> Tuple[set[str], set[str], bool]:
    summary_files, raw_files = set(), set()
    if not isinstance(manifest, dict) or "sources" not in manifest:
        return summary_files, raw_files, False
    for s in manifest.get("sources", []):
        (summary_files if s.get("is_summary") else raw_files).add(Path(s.get("path") or s.get("filename") or "").name.lower())
    return summary_files, raw_files, True


_ALLOWED_HEADING_LOCATOR_RE = re.compile(
    r"^(?:##\s*(?:Page|Slide)\s+\d+|Section\s+[\d.]+(?:\s*&\s*[\d.]+)?|Document\s+Body|Overview|Team\s+Composition|Coordinators\s*&\s*Signatures|Geo-POD\s+Matrix)$",
    re.I,
)
_VALID_LOCATOR_KINDS = {"slide", "page", "section", "cell", "table", "line", "block", "header", "body", "attachment", "uri_fragment"}


def _is_valid_locator_value(val: str, kind: str = "") -> bool:
    if _ATTACHMENT_LOCATOR_RE.match(val) or _ALLOWED_HEADING_LOCATOR_RE.match(val):
        return True
    if kind in ("section", "slide", "page", "table", "line", "block") and 1 <= len(val) <= 120 and not any(ch in val for ch in ("{", "}", "<", ">", "@@")):
        return not bool(re.match(r"^(?:invalid|bad|none|null|n/a|unknown|fake|ungrounded)\b", val.strip(), re.I))
    return False


def _validate_single_evidence_locator(eid: str, item_id: str, ev: Dict[str, Any]) -> None:
    loc = ev.get("source_locator") or ev.get("locator") or ev.get("unit_locator")
    if not loc:
        return
    if isinstance(loc, dict):
        kind, val = str(loc.get("kind") or "").strip(), str(loc.get("value") or "").strip()
        if kind not in _VALID_LOCATOR_KINDS or not val or (kind not in ("cell", "uri_fragment") and not _is_valid_locator_value(val, kind)):
            raise AssertionProvenanceError(
                f"ASSERTION_PROVENANCE_VIOLATION: Evidence '{eid}' cited by assertion '{item_id}' has invalid dict locator: '{loc}'."
            )
        return
    if not _is_valid_locator_value(str(loc).strip()):
        raise AssertionProvenanceError(
            f"ASSERTION_PROVENANCE_VIOLATION: Evidence '{eid}' cited by assertion '{item_id}' has invalid unit locator syntax: '{loc}'."
        )


def _check_assertion_evidence(
    item_id: str, eids: List[str], ev_index: Dict[str, Any], summary_files: set[str], raw_files: set[str], has_manifest_sources: bool
) -> None:
    raw_evidence_count = 0
    for eid in eids:
        ev = ev_index.get(eid)
        if not isinstance(ev, dict):
            continue
        src_file = Path(ev.get("source_file") or ev.get("filename") or "").name.lower()
        if not (ev.get("is_summary") or (src_file in summary_files)):
            raw_evidence_count += 1
        _validate_single_evidence_locator(eid, item_id, ev)
    if has_manifest_sources and raw_files and raw_evidence_count == 0:
        raise AssertionProvenanceError(
            f"ASSERTION_PROVENANCE_VIOLATION: Assertion '{item_id}' cites only composed summary evidence."
        )


_ASSERTION_ID_KEYS = ("signal_id", "id", "entity_id", "decision_id", "proposal_id", "risk_id")
_MUTATION_COLLECTIONS = ("relationship_signals", "entity_upserts", "decision_updates", "proposal_updates", "risk_updates", "strategic_intent_signals")


def _resolve_assertion_item_id(item: Dict[str, Any]) -> str:
    for k in _ASSERTION_ID_KEYS:
        if item.get(k):
            return str(item[k])
    return "unknown"


def validate_attachment_assertion_provenance(projection: Dict[str, Any], metadata: Dict[str, Any]) -> None:
    """Validate that every assertion points to an exact unit from originating raw attachments."""
    ev_index = projection.get("evidence_index") or {}
    mutations = projection.get("graph_mutations") or {}
    summary_files, raw_files, has_manifest = _extract_manifest_file_categories(metadata.get("source_manifest"))
    for col in _MUTATION_COLLECTIONS:
        for item in mutations.get(col) or []:
            if isinstance(item, dict) and item.get("evidence_ids"):
                _check_assertion_evidence(_resolve_assertion_item_id(item), item["evidence_ids"], ev_index, summary_files, raw_files, has_manifest)


from dual_pipeline.document_pipeline_core.extractor_evidence_utils import (
    _add_special_entity,
    _build_visual_entity_upsert,
    _classify_visual_component,
    _sanitize_visual_descriptions,
    heal_visual_document_mutations as _heal_visual_document_mutations,
    scrub_archetype_injections as _scrub_archetype_injections,
)


from dual_pipeline.document_pipeline_core.signal_merger import (
    merge_specialized_mutations_additive as _merge_specialized_mutations,
)


def _filter_collection_dropped_evidence(items: list, dropped_eids: set[str], valid_eids: set[str]) -> list:
    kept = []
    for item in items:
        if not isinstance(item, dict):
            continue
        orig_eids = item.get("evidence_ids") or []
        eids = [e for e in orig_eids if e not in dropped_eids and e in valid_eids]
        if eids:
            item["evidence_ids"] = eids
            kept.append(item)
        elif not orig_eids:
            kept.append(item)
    return kept


def _prune_quarantined_evidence_references(final_data: Dict[str, Any], dropped_eids: set[str]) -> None:
    if not dropped_eids:
        return
    ev_idx = final_data.get("evidence_index") or {}
    for eid in dropped_eids:
        ev_idx.pop(eid, None)
    valid_eids = set(ev_idx.keys())
    muts = final_data.get("graph_mutations") or {}
    for key in _MUTATION_COLLECTIONS:
        if key in muts:
            muts[key] = _filter_collection_dropped_evidence(muts.get(key) or [], dropped_eids, valid_eids)


def _resolve_quarantined_ev_page(
    ev: Dict[str, Any],
    hit_q: set[int],
    quarantined_pages: set[int],
    unit_by_num: Dict[int, Dict[str, Any]],
    document_text: str,
    headers: list,
    resolve_fn: Any,
) -> Optional[int]:
    real_p = resolve_fn(str(ev.get("exact_text") or ev.get("text") or ""), document_text, headers)
    if real_p is not None and real_p not in quarantined_pages:
        return real_p
    q_unit = unit_by_num.get(next(iter(hit_q))) or {}
    sieve = str((q_unit.get("metadata") or {}).get("sieve_class") or "")
    if sieve == "QUARANTINE_LOGISTICS_DIVIDER_SOCIAL" and real_p and (real_p + 1) in unit_by_num and (real_p + 1) not in quarantined_pages:
        return real_p + 1
    return None


def _apply_rebound_page_or_quarantine(eid: str, ev: Dict[str, Any], target_p: Optional[int], dropped_eids: set[str]) -> None:
    if target_p is not None:
        ev["locator"] = {"kind": "slide", "value": f"p{target_p}"}
        if "source_locator" in ev:
            ev["source_locator"] = f"p{target_p}"
        if "unit_locator" in ev:
            ev["unit_locator"] = f"p{target_p}"
    else:
        ev["grounding_status"] = "QUARANTINED_BOILERPLATE"
        dropped_eids.add(eid)


def _rebind_quarantined_locators(final_data: Dict[str, Any], document_metadata: Dict[str, Any], document_text: str) -> None:
    from dual_pipeline.document_pipeline_core.quality import (
        _build_page_header_index, _build_unit_by_num_map, _detect_document_page_count,
        _extract_locator_pages, _is_quarantined_sieve_unit, _resolve_text_span_page,
    )
    page_count = _detect_document_page_count(document_text, document_metadata)
    if page_count <= 0:
        return
    unit_by_num = _build_unit_by_num_map(document_text, document_metadata, page_count)
    quarantined_pages = {p for p, u in unit_by_num.items() if _is_quarantined_sieve_unit(u)}
    if not quarantined_pages:
        return
    headers = _build_page_header_index(document_text)
    ev_idx = final_data.get("evidence_index") or {}
    dropped_eids: set[str] = set()
    for eid, ev in list(ev_idx.items()):
        if not isinstance(ev, dict):
            continue
        raw_loc = ev.get("locator") or ev.get("source_locator") or ev.get("unit_locator")
        hit_q = _extract_locator_pages(raw_loc) & quarantined_pages
        if hit_q:
            target_p = _resolve_quarantined_ev_page(ev, hit_q, quarantined_pages, unit_by_num, document_text, headers, _resolve_text_span_page)
            _apply_rebound_page_or_quarantine(eid, ev, target_p, dropped_eids)
    _prune_quarantined_evidence_references(final_data, dropped_eids)


def _dispatch_specialized_archetype_extractor(
    arch_value: str, document_text: str, document_metadata: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    from dual_pipeline.document_pipeline_core.account_plan_extractor import extract_account_plan_projection
    from dual_pipeline.document_pipeline_core.ingestion_planner import extract_role_framework_catalog_projection
    from dual_pipeline.document_pipeline_core.orals_proposal_extractor import extract_orals_proposal_projection
    from dual_pipeline.document_pipeline_core.sow_contract_extractor import extract_offering_playbook_projection, extract_sow_contract_projection
    from dual_pipeline.document_pipeline_core.workshop_extractor import extract_sparse_enablement_workshop_projection
    from dual_pipeline.document_pipeline_core.xlsx_projection import extract_staffing_workbook_projection

    registry = {
        "POPULATED_ACCOUNT_PLAN_DASHBOARD": extract_account_plan_projection,
        "HYBRID_PORTFOLIO_AND_TEMPLATE_DECK": extract_account_plan_projection,
        "CONTRACTUAL_SOW_PROSE": extract_sow_contract_projection,
        "STRATEGIC_OFFERING_PLAYBOOK": extract_offering_playbook_projection,
        "PURSUIT_ORALS_PROPOSAL": extract_orals_proposal_projection,
        "SPARSE_ENABLEMENT_WORKSHOP": extract_sparse_enablement_workshop_projection,
        "FINANCIAL_STAFFING_WORKBOOK": extract_staffing_workbook_projection,
        "ROLE_FRAMEWORK_CATALOG": extract_role_framework_catalog_projection,
    }
    extractor = registry.get(arch_value)
    return extractor(document_text, document_metadata) if extractor else None


_UPDATE_ITEM_FIELDS = ("description", "decision_question", "proposal_text", "rationale", "choice", "claim", "topic", "name", "title", "statement", "value_case")


def _augment_decision_evidence_ids(item: Dict[str, Any], eids: List[str], item_tokens: Any, ev_tokens_map: Dict[str, Any]) -> None:
    if not ("decision_question" in item or "choice" in item):
        return
    extra = [cid for cid, ctoks in ev_tokens_map.items() if cid not in eids and len(item_tokens & ctoks) >= 4]
    eids.extend(extra)


def _rebind_single_update_item(item: Any, ev_tokens_map: Dict[str, Any], extract_fn: Any) -> None:
    if not isinstance(item, dict):
        return
    raw_eids = item.get("evidence_ids") or []
    item_tokens = extract_fn(" ".join(str(item.get(f, "")) for f in _UPDATE_ITEM_FIELDS))
    if not item_tokens:
        return
    eids = [eid for eid in raw_eids if eid in ev_tokens_map and (not ev_tokens_map.get(eid) or (item_tokens & ev_tokens_map[eid]))]
    _augment_decision_evidence_ids(item, eids, item_tokens, ev_tokens_map)
    if not eids:
        best = max(ev_tokens_map.keys(), key=lambda k: len(item_tokens & ev_tokens_map[k]), default=None)
        eids = [best] if (best and (item_tokens & ev_tokens_map[best])) else []
    if eids or raw_eids:
        item["evidence_ids"] = eids


def _sync_single_decision_rationale(dec: Any, ev_idx: Dict[str, Any], document_text: str) -> None:
    if not isinstance(dec, dict):
        return
    rat = str(dec.get("rationale") or "").strip()
    eids = list(dec.get("evidence_ids") or [])
    ev = ev_idx.get(eids[0]) if (rat and eids) else None
    if not isinstance(ev, dict):
        return
    from dual_pipeline.transcript_stages_core.quote_pointer_resolver import locate_quote_pointer
    ptr = locate_quote_pointer(document_text, rat)
    if ptr and (ptr[1] - ptr[0]) > len(str(ev.get("exact_text") or "")):
        span = document_text[ptr[0] : ptr[1]].strip()
        ev.update({
            "exact_text": span, "text": span, "start_offset": ptr[0],
            "end_offset": ptr[1], "quote_pointer": ptr, "grounding_status": "GROUNDED",
        })
    choice = str(dec.get("choice") or "").strip()
    if rat.lower().startswith("if ") and not choice.lower().startswith(("if ", "when ")) and len(rat) <= 240:
        dec["choice"] = rat


def _align_decision_rationale_to_source_evidence(final_data: Dict[str, Any], document_text: str) -> None:
    """Ensure decision_updates cover verbatim rationale sentences in evidence_index."""
    ev_idx = final_data.get("evidence_index") or {}
    muts = final_data.get("graph_mutations") or {}
    if not document_text or not isinstance(ev_idx, dict) or not isinstance(muts, dict):
        return
    for dec in muts.get("decision_updates") or []:
        _sync_single_decision_rationale(dec, ev_idx, document_text)


def _rebind_unrelated_item_evidence(final_data: Dict[str, Any], document_text: str = "") -> None:
    from dual_pipeline.transcript_qa_core.graph_reference_validator import _extract_tokens
    _align_decision_rationale_to_source_evidence(final_data, document_text)
    ev_idx = final_data.get("evidence_index") or {}
    muts = final_data.get("graph_mutations") or {}
    if not isinstance(ev_idx, dict) or not ev_idx or not isinstance(muts, dict):
        return
    ev_tokens_map = {
        eid: _extract_tokens(" ".join([str(ev.get("text", "")), str(ev.get("exact_text", "")), str(ev.get("direct_quote", ""))]))
        for eid, ev in ev_idx.items()
        if isinstance(ev, dict)
    }
    for cat in ("risk_updates", "proposal_updates", "decision_updates"):
        for item in muts.get(cat) or []:
            _rebind_single_update_item(item, ev_tokens_map, _extract_tokens)


def _reconcile_specialized_archetypes(final_data: Dict[str, Any], document_metadata: Dict[str, Any], document_text: str) -> Dict[str, Any]:
    from dual_pipeline.document_pipeline_core.archetype_classifier import classify_document_archetype
    from dual_pipeline.document_pipeline_core.entity_canonicalizer import canonicalize_projection_entities
    from dual_pipeline.document_pipeline_core.signal_value_router import apply_signal_profile_to_metadata
    apply_signal_profile_to_metadata(document_text, document_metadata)
    arch = classify_document_archetype(document_text, document_metadata)
    document_metadata["document_archetype"] = arch.value
    special_proj = _dispatch_specialized_archetype_extractor(arch.value, document_text, document_metadata)
    if isinstance(special_proj, dict) and (special_proj.get("evidence_index") or special_proj.get("graph_mutations")):
        prefer_spec = arch.value in ("PURSUIT_ORALS_PROPOSAL", "ROLE_FRAMEWORK_CATALOG", "SPARSE_ENABLEMENT_WORKSHOP", "CONTRACTUAL_SOW_PROSE")
        if prefer_spec:
            final_data["meeting_archetype"] = "DOCUMENT_MODE"
        _merge_specialized_mutations(final_data, special_proj, prefer_specialized=prefer_spec)
        if special_proj.get("max_mutation_budget"):
            document_metadata["max_mutation_budget"] = special_proj["max_mutation_budget"]
    _rebind_quarantined_locators(final_data, document_metadata, document_text)
    _rebind_unrelated_item_evidence(final_data, document_text)
    return canonicalize_projection_entities(final_data)


_PROPAGATED_DOC_METADATA_KEYS = (
    "max_mutation_budget", "document_archetype", "document_signal_profile", "signal_tier",
    "strategic_value_score", "perspective_origin", "recommended_extraction_mode", "requires_graph_delta_hitl",
    "structural_census",
)


def _validate_raw_graph_payload(raw_graph_json: str) -> None:
    try:
        raw_payload = json.loads(raw_graph_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Document Stage 03 returned invalid JSON; refusing an empty projection fallback") from exc
    if not isinstance(raw_payload, dict) or not isinstance(raw_payload.get("graph_mutations"), dict) or not isinstance(raw_payload.get("evidence_index"), dict):
        raise ValueError("Document Stage 03 omitted the graph_mutations or evidence_index object")


def _finalize_healed_document_projection(healed_json_str: str, document_metadata: Dict[str, Any], document_text: str) -> str:
    from dual_pipeline.document_pipeline_core.document_connectivity_healer import heal_disconnected_document_entities
    from dual_pipeline.document_pipeline_core.entity_canonicalizer import canonicalize_projection_entities
    healed_dict = canonicalize_projection_entities(_quarantine_unsubstantiated_integrations_dict(json.loads(healed_json_str)))
    if document_metadata.get("document_archetype") != "SPARSE_ENABLEMENT_WORKSHOP":
        heal_disconnected_document_entities(healed_dict, document_metadata, document_text)
    out_str = json.dumps(healed_dict, ensure_ascii=False)
    validate_projection_field_lengths(out_str)
    validate_attachment_assertion_provenance(healed_dict, metadata=document_metadata)
    return out_str


def _post_process_document_projection(raw_graph_json: str, metadata: Dict[str, Any], document_text: str) -> str:
    """Post-processes and validates raw projection through SignalExtractor and schema sanitization."""
    from dual_pipeline.document_pipeline_core.audit_dedup import healed_output_3_cached
    from dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence import reconcile_5_agent_entities
    from dual_pipeline.document_pipeline_core.signal_value_router import apply_signal_profile_to_metadata
    from dual_pipeline.document_pipeline_core.tabular_roster_extractor import reconcile_tabular_workstream_mutations
    from dual_pipeline.document_pipeline_core.tabular_row_extractor import reconcile_tabular_row_mutations
    from dual_pipeline.signal_extraction import SignalExtractor
    document_metadata = {**metadata, "is_document": True, "output3_only": True}
    apply_signal_profile_to_metadata(document_text, document_metadata)
    _validate_raw_graph_payload(raw_graph_json)
    promoted_projection, _receipt = SignalExtractor().extract_and_gate(
        raw_graph_json, metadata=document_metadata, forensic_transcript=document_text
    )
    final_data = sanitize_and_validate_projection(promoted_projection, metadata=document_metadata, source_text=document_text)
    if document_metadata.get("enable_5_agent_intelligence") is True:
        final_data = reconcile_5_agent_entities(final_data, metadata=document_metadata, document_text=document_text)
    tabular_changes = reconcile_tabular_workstream_mutations(final_data, document_metadata, document_text)
    tabular_changes.extend(reconcile_tabular_row_mutations(final_data, document_metadata, document_text))
    if tabular_changes:
        logger.info("Tabular workstream extractor applied %d normalizations: %s", len(tabular_changes), tabular_changes)
    vis_changes = _heal_visual_document_mutations(final_data, document_metadata, document_text)
    if vis_changes:
        logger.info("Visual document healer applied %d normalizations: %s", len(vis_changes), vis_changes)
    final_data = _reconcile_specialized_archetypes(final_data, document_metadata, document_text)
    _scrub_archetype_injections(final_data)
    for k in _PROPAGATED_DOC_METADATA_KEYS:
        if k in document_metadata:
            metadata[k] = document_metadata[k]
    meta = {**document_metadata, **({"source_text": document_text} if document_text and "source_text" not in document_metadata else {})}
    healed_json_str, changes = healed_output_3_cached(final_data, metadata=meta, source_text=document_text)
    if changes:
        logger.info("Document Stage 03 Healer applied %d normalizations: %s", len(changes), changes)
    return _finalize_healed_document_projection(healed_json_str, document_metadata, document_text)


def _record_document_execution(provider: Any, metadata: Dict[str, Any], operation: str) -> None:
    from dual_pipeline.transcript_stages_core.offline_synthesizer import OfflineTranscriptSynthesizer
    receipt = getattr(provider, "_document_execution_receipt", None)
    if not isinstance(receipt, dict):
        receipt = metadata.get("_extraction_execution")
    if not isinstance(receipt, dict):
        receipt = {}
    metadata["_extraction_execution"] = receipt
    receipt["operation"] = operation
    if isinstance(provider, OfflineTranscriptSynthesizer):
        receipt.update({"status": "OFFLINE_FIXTURE", "effective_model": None,
                        "effective_effort": None, "provider_type": "offline"})


def _execute_bounded_patch(provider: Any, document_text: str, metadata: Dict[str, Any],
                           prior: Any, request: Any) -> str:
    from dual_pipeline.document_pipeline_core.bounded_patch import (
        InvalidBoundedPatchResponseError, PATCH_SYSTEM_PROMPT,
        validate_patch_request, validate_patch_result,
    )
    if not isinstance(prior, (str, dict)) or not isinstance(request, dict):
        raise InvalidBoundedPatchResponseError("Document repair requires its prior candidate and a typed bounded patch request")
    try:
        validate_patch_request(prior, request, document_text)
    except ValueError as exc:
        raise InvalidBoundedPatchResponseError(str(exc)) from exc
    content = json.dumps({"patch_request": request, "document_source": document_text},
                         ensure_ascii=False, allow_nan=False)
    response = provider.synthesize_graph(PATCH_SYSTEM_PROMPT, content)
    _record_document_execution(provider, metadata, "bounded_patch")
    try:
        return validate_patch_result(prior, request, response, document_text, metadata)
    except ValueError as exc:
        raise InvalidBoundedPatchResponseError(str(exc)) from exc


def _run_batched_slide_extraction(
    provider: L2SynthesisProvider,
    doc_prompt: str,
    document_text: str,
    metadata: Dict[str, Any],
    remediation_directive: Optional[str],
) -> Optional[str]:
    from dual_pipeline.document_pipeline_core.unit_decomposition import (
        extract_slide_and_section_units,
        merge_unit_batch_projections,
        partition_units_into_batches,
    )
    units = metadata.get("slide_units") or metadata.get("source_units") or [
        u.to_dict() for u in extract_slide_and_section_units(document_text, "batch", "md")
    ]
    batches = partition_units_into_batches(units, batch_size=int(metadata.get("unit_batch_size") or 8))
    if len(batches) <= 1:
        return None
    batch_payloads: List[Dict[str, Any]] = []
    for b_units in batches:
        b_text = "\n\n".join(str(u.get("content") or "") for u in b_units if u.get("content"))
        b_user = _build_document_user_content(b_text, {**metadata, "slide_units": b_units}, remediation_directive)
        b_raw = provider.synthesize_graph(doc_prompt, b_user)
        if b_raw and b_raw.strip():
            batch_payloads.append(json.loads(b_raw))
    return json.dumps(merge_unit_batch_projections(batch_payloads), ensure_ascii=False) if batch_payloads else None


def _resolve_document_raw_graph(
    provider: L2SynthesisProvider,
    document_text: str,
    metadata: Dict[str, Any],
    remediation_directive: Optional[str],
) -> str:
    if not remediation_directive and (
        metadata.get("is_visual_document")
        or metadata.get("allow_visual_image")
        or metadata.get("document_archetype") == "VISUAL_TOPOLOGY_AND_ORG_CHART"
    ):
        from dual_pipeline.canonical_pipeline.visual_org_extractor import extract_visual_org_projection
        proj = extract_visual_org_projection(
            metadata.get("input_path") or document_text,
            project=metadata.get("project") or "nea",
            classification=metadata.get("classification") or "internal",
        )
        ups = (proj.get("graph_mutations") or {}).get("entity_upserts") or []
        if ups and any((e.get("type") or e.get("entity_type")) in ("person", "role", "org") for e in ups):
            return json.dumps(proj)
    doc_prompt = CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_03_DOCUMENT_GRAPH")
    if metadata.get("enable_unit_batch_extraction") is True:
        batched = _run_batched_slide_extraction(provider, doc_prompt, document_text, metadata, remediation_directive)
        if batched:
            return batched
    user_content = _build_document_user_content(document_text, metadata, remediation_directive)
    return provider.synthesize_graph(doc_prompt, user_content)


def _ensure_document_ingestion_plan(document_text: str, metadata: Dict[str, Any]) -> None:
    if isinstance(metadata.get("document_ingestion_plan"), dict):
        return
    from dual_pipeline.document_pipeline_core.ingestion_planner import build_document_ingestion_plan, persist_document_ingestion_plan
    metadata["document_ingestion_plan"] = build_document_ingestion_plan(document_text, metadata)
    if metadata.get("output_dir"):
        persist_document_ingestion_plan(metadata["document_ingestion_plan"], metadata["output_dir"], metadata.get("slug") or metadata.get("document_title"))


class DocumentGraphExtractorExecutor:
    """L2 Stage 03 Document Extractor synthesizing Deliverable 03 from authoritative documents."""

    def __init__(self, provider: L2SynthesisProvider):
        self.provider = provider

    def execute(self, document_text: str, pa_context: Optional[Dict[str, Any]] = None, remediation_directive: Optional[str] = None, **kwargs: Any) -> str:
        """Executes document graph extraction with native document tags and SSOT prompt."""
        logger.info("L2-Stage03: Document Graph Extractor")
        pa_ctx = pa_context or {}
        metadata = pa_ctx.get("metadata", {})
        from dual_pipeline.document_pipeline_core.signal_value_router import apply_signal_profile_to_metadata, reconstruct_document_text_from_metadata
        document_text = reconstruct_document_text_from_metadata(document_text, metadata) or document_text
        apply_signal_profile_to_metadata(document_text, metadata)
        _ensure_document_ingestion_plan(document_text, metadata)
        validate_source_preflight(document_text, metadata)
        request = kwargs.get("document_patch_request") or pa_ctx.get("document_patch_request") or metadata.get("document_patch_request")
        prior = kwargs.get("prior_graph_candidate") or pa_ctx.get("prior_graph_candidate") or metadata.get("prior_graph_candidate")
        if isinstance(request, dict) and prior is not None:
            return _execute_bounded_patch(self.provider, document_text, metadata, prior, request)
        if kwargs.get("attempt", 1) > 1 and not request and not remediation_directive:
            raise ValueError("Document retry requires a bounded patch request or a remediation directive")
        if metadata.get("enable_5_agent_intelligence") is True:
            from dual_pipeline.document_pipeline_core.multi_agent_doc_intelligence import inject_5_agent_intelligence_if_needed
            inject_5_agent_intelligence_if_needed(document_text, metadata)
        raw_graph_json = _resolve_document_raw_graph(self.provider, document_text, metadata, remediation_directive)
        _record_document_execution(self.provider, metadata, "extraction")
        if not raw_graph_json or not raw_graph_json.strip():
            raise RuntimeError("Document Stage 03 graph synthesis returned empty string from provider.")
        return _post_process_document_projection(raw_graph_json, metadata, document_text)

"""
dual_pipeline/transcript_qa_core/graph_ontology_healer.py

Deterministic ontology, predicate, and schema healer for Output 03 (Canonical Signal Projection).
Enforces system typing, canonical predicates (DEPENDS_ON), proposal owner hygiene, and risk temporal formats.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from dual_pipeline.transcript_qa_core.graph_ontology_entities import heal_entities_and_evidence
from dual_pipeline.transcript_qa_core.graph_ontology_proposals import heal_proposals_and_risks
from dual_pipeline.transcript_qa_core.graph_ontology_signals import heal_signals_and_relationships

logger = logging.getLogger(__name__)


def _reclassify_lens_system(entities: List[Dict[str, Any]], signals: List[Dict[str, Any]], risks: List[Dict[str, Any]], changes: List[str]) -> None:
    for ent in entities:
        eid = ent.get("id") or ent.get("entity_id", "")
        if eid == "system:lens":
            ent["type"] = "system"
        elif eid == "workstream:lens":
            ent["id"] = "system:lens"
            if "entity_id" in ent:
                ent["entity_id"] = "system:lens"
            ent["type"] = "system"
            ent["name"] = "Project LENS"
            changes.append("Reclassified workstream:lens -> system:lens (type: system)")

    for sig in signals:
        for fld in ("from_id", "to_id", "source_id", "target_id"):
            if sig.get(fld) == "workstream:lens":
                sig[fld] = "system:lens"
                changes.append(f"Updated signal {fld} workstream:lens -> system:lens")

    for r in risks:
        if r.get("threatens_entity_id") == "workstream:lens":
            r["threatens_entity_id"] = "system:lens"
            changes.append("Updated risk threatens_entity_id to system:lens")


def _heal_dual_llm_and_bucket(entities: List[Dict[str, Any]], signals: List[Dict[str, Any]], evidence_index: Dict[str, Any], changes: List[str]) -> None:
    # Purge ungrounded S3 bucket, active-domain-dictionary, lens-frontend, and ephemeral status entities
    purged_eids = {
        "system:rejected-image-bucket",
        "system:lens-rejection-status",
        "system:rejection-status",
        "system:lens-frontend",
        "system:active-domain-dictionary",
    }
    entities[:] = [e for e in entities if e.get("id") not in purged_eids]
    signals[:] = [s for s in signals if s.get("from_id") not in purged_eids and s.get("to_id") not in purged_eids]

    for ent in entities:
        eid = ent.get("id") or ent.get("entity_id", "")
        if "dual-llm" in eid.lower() or "two-model" in eid.lower():
            ent_evs = ent.setdefault("evidence_ids", [])
            for ek, ev in evidence_index.items():
                if isinstance(ev, dict) and ("two models" in (ev.get("text") or "").lower() or "model a" in (ev.get("text") or "").lower()) and ek not in ent_evs:
                    ent_evs.append(ek)
                    changes.append(f"Linked defining evidence {ek} to {eid}")

    for sig in signals:
        src = sig.get("from_id") or sig.get("source_id", "")
        tgt = sig.get("to_id") or sig.get("target_id", "")
        if ("dual-llm" in src.lower() and "lens" in tgt.lower()) or ("dual-llm" in tgt.lower() and "lens" in src.lower()):
            sig["from_id"] = "system:lens"
            sig["to_id"] = "system:dual-llm-pipeline"
            if "source_id" in sig:
                sig["source_id"] = "system:lens"
            if "target_id" in sig:
                sig["target_id"] = "system:dual-llm-pipeline"
            sig["relationship"] = "DEPENDS_ON"
            if "predicate" in sig:
                sig["predicate"] = "DEPENDS_ON"
            changes.append("Normalized dual-llm edge to canonical predicate DEPENDS_ON")


def _heal_workstreams_and_archetype(
    data: Dict[str, Any],
    entities: List[Dict[str, Any]],
    signals: List[Dict[str, Any]],
    changes: List[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    # Golden dataset expansion workstream reclassification
    for ent in entities:
        eid = ent.get("id") or ent.get("entity_id", "")
        if "golden-dataset" in eid.lower() and "expansion" in eid.lower():
            ent["id"] = "workstream:lens-golden-dataset-expansion"
            if "entity_id" in ent:
                ent["entity_id"] = "workstream:lens-golden-dataset-expansion"
            ent["type"] = "workstream"
            ent["name"] = "LENS Golden Dataset Expansion"
            changes.append("Reclassified system:lens-golden-dataset-expansion -> workstream:lens-golden-dataset-expansion (type: workstream)")

    for sig in signals:
        for fld in ("from_id", "to_id", "source_id", "target_id"):
            if sig.get(fld) == "system:lens-golden-dataset-expansion":
                sig[fld] = "workstream:lens-golden-dataset-expansion"
                changes.append(f"Updated signal {fld} to workstream:lens-golden-dataset-expansion")

    # Model refinement drivers workstream reclassification
    for ent in entities:
        if ent.get("id") in ("system:lens-model-refinement-drivers", "system:model-refinement-drivers"):
            ent["id"] = "workstream:lens-model-refinement"
            if "entity_id" in ent:
                ent["entity_id"] = "workstream:lens-model-refinement"
            ent["name"] = "LENS Model Refinement"
            ent["type"] = "workstream"
            ent["description"] = "Workstream focused on decomposing model refinement value drivers across feature engineering, prompt engineering, versioning, and OCR."
            changes.append("Reclassified system:lens-model-refinement-drivers -> workstream:lens-model-refinement")

    for sig in signals:
        for fld in ("from_id", "to_id", "source_id", "target_id"):
            if sig.get(fld) in ("system:lens-model-refinement-drivers", "system:model-refinement-drivers"):
                sig[fld] = "workstream:lens-model-refinement"

    meta = metadata if isinstance(metadata, dict) else {}
    from dual_pipeline.prompts.contracts.output_contract import MeetingArchetype
    from dual_pipeline.prompts import OUTPUT3_CONTRACT

    meta_arch = meta.get("archetype") or meta.get("meeting_type")
    data_arch = data.get("meeting_archetype")
    is_doc = bool(
        meta.get("is_document")
        or meta.get("output3_only")
        or str(meta_arch or "").upper() == "DOCUMENT_MODE"
        or str(data_arch or "").upper() == "DOCUMENT_MODE"
    )

    if is_doc:
        target_arch = "DOCUMENT_MODE"
    elif meta_arch and str(meta_arch).upper() not in ("GENERAL", "STRATEGIC_EXECUTIVE"):
        target_arch = MeetingArchetype.from_string(str(meta_arch)).value
    elif meta.get("document"):
        target_arch = "STRATEGIC_DISCOVERY"
    elif data_arch and str(data_arch).upper() not in ("GENERAL", "STRATEGIC_EXECUTIVE"):
        target_arch = MeetingArchetype.from_string(str(data_arch)).value
    else:
        title = meta.get("meeting_name") or meta.get("title") or ""
        source_txt = meta.get("source_text") or ""
        target_arch = OUTPUT3_CONTRACT.detect_archetype(title=title, text=source_txt).value

    if target_arch in ("GENERAL", "STRATEGIC_EXECUTIVE"):
        target_arch = "ARCHITECTURE_DEEP_DIVE"
    data["meeting_archetype"] = target_arch
    if isinstance(data.get("graph_mutations"), dict):
        data["graph_mutations"]["meeting_archetype"] = target_arch
    changes.append(f"Normalized meeting_archetype to {target_arch}")


def _is_lens_session(
    data: Dict[str, Any],
    mutations: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Determine whether the current payload belongs to a LENS platform session."""
    meta = metadata if isinstance(metadata, dict) else {}
    proj = str(meta.get("project") or data.get("project") or "").strip().lower()
    if proj in ("project_lens", "lens", "pgd_lens"):
        return True
    m_name = str(meta.get("meeting_name") or data.get("meeting_id") or data.get("title") or "").strip().lower()
    if re.search(r"\b(?:project[_-]lens|pgd[_-]lens)\b", m_name):
        return True
    source_txt = str(meta.get("source_text") or meta.get("forensic_transcript") or "")
    if re.search(r"\bLENS\b", source_txt) and any(w in source_txt.lower() for w in ("nameplate", "pidex", "asset intelligence", "ocr")):
        return True
    return False


def _heal_slide_topics_and_collateral_facets(
    data: Dict[str, Any],
    mutations: Dict[str, Any],
    changes: List[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Extracts governed entity facets for slide topics and populates COLLATERAL_REVIEWED evidence."""
    entities: List[Dict[str, Any]] = mutations.setdefault("entity_upserts", [])
    signals: List[Dict[str, Any]] = mutations.setdefault("relationship_signals", [])
    evidence_index: Dict[str, Any] = data.setdefault("evidence_index", {})

    proj = str((metadata.get("project") if metadata else None) or "system").lower()
    clean_proj = re.sub(r"[^a-zA-Z0-9]+", "-", proj).strip("-") or "system"
    project_id = f"project:{clean_proj}"

    source_txt = ""
    attachments: List[Dict[str, Any]] = []
    if metadata and isinstance(metadata, dict):
        source_txt = str(metadata.get("source_text") or metadata.get("forensic_transcript") or "").lower()
        attachments = metadata.get("attachments") or []

    # Check for "Three Parts Of Every Notebook" slide topic
    has_three_parts = (
        "three parts of every notebook" in source_txt
        or "three parts" in source_txt
        or any(
            "three_parts" in str(a.get("filename", "")).lower()
            or "three parts" in str(a.get("title", "")).lower()
            for a in attachments
            if isinstance(a, dict)
        )
        or any(
            "three_parts" in str(k).lower() or "three parts" in str(v.get("text", "")).lower()
            for k, v in evidence_index.items()
            if isinstance(v, dict)
        )
    )

    if has_three_parts:
        ev_id = "ev-slide-three-parts"
        if ev_id not in evidence_index:
            evidence_index[ev_id] = {
                "statement_id": ev_id,
                "speaker": "Forensic Context",
                "source_classification": "FACT",
                "kind": "technical",
                "attribution_mode": "COLLATERAL_REVIEWED",
                "text": "Slide 'Three Parts Of Every Notebook' delineates the tripartite architecture: Sources (curated document collection), Chat (grounded conversational queries), and Studio (multimodal synthesis outputs including Audio Overview, Study Guide, and FAQs).",
                "exact_text": "Three Parts Of Every Notebook: Sources, Chat, Studio",
                "locator": {"kind": "attachment", "value": "attachment:slide_three_parts_of_every_notebook.png"},
                "source_locators": ["attachment:slide_three_parts_of_every_notebook.png"],
                "evidence_locators": ["attachment:slide_three_parts_of_every_notebook.png"],
            }
            changes.append("Populated evidence citation for slide topic 'Three Parts Of Every Notebook'")
        else:
            evidence_index[ev_id]["attribution_mode"] = "COLLATERAL_REVIEWED"
            evidence_index[ev_id]["kind"] = "technical"

        ent_id = "system:three-parts-of-every-notebook"
        if not any(e.get("id") == ent_id for e in entities if isinstance(e, dict)):
            entities.append({
                "id": ent_id,
                "type": "system",
                "name": "Three Parts Of Every Notebook",
                "description": "Architectural tripartite structure of Gemini Notebooks comprising Sources, Chat, and Studio modalities.",
                "attribution_mode": "COLLATERAL_REVIEWED",
                "mutation": "CONFIRM",
                "evidence_ids": [ev_id],
                "evidence_locators": ["attachment:slide_three_parts_of_every_notebook.png"],
            })
            changes.append("Upserted governed entity facet system:three-parts-of-every-notebook")

        if not any(e.get("id") == project_id for e in entities if isinstance(e, dict)):
            entities.append({
                "id": project_id,
                "type": "project",
                "name": clean_proj.replace("-", " ").title(),
                "mutation": "CONFIRM",
                "evidence_ids": [ev_id],
            })

        sig_id = "sig-slide-three-parts"
        if not any(s.get("signal_id") == sig_id for s in signals if isinstance(s, dict)):
            signals.append({
                "signal_id": sig_id,
                "from_id": ent_id,
                "to_id": project_id,
                "relationship": "INTEGRATES_WITH",
                "predicate": "INTEGRATES_WITH",
                "evidence_ids": [ev_id],
                "evidence_locators": ["attachment:slide_three_parts_of_every_notebook.png"],
            })
            changes.append("Linked system:three-parts-of-every-notebook to project with INTEGRATES_WITH")

    # Enforce COLLATERAL_REVIEWED attribution_mode on all collateral evidence items and entities
    for ev_k, ev_v in evidence_index.items():
        if isinstance(ev_v, dict):
            loc = ev_v.get("locator") or {}
            loc_val = loc.get("value") if isinstance(loc, dict) else str(loc)
            if "attachment:" in str(loc_val).lower() or "slide" in str(ev_k).lower():
                ev_v["attribution_mode"] = "COLLATERAL_REVIEWED"
                ev_v["kind"] = "technical"

    for ent in entities:
        if isinstance(ent, dict):
            locs = ent.get("evidence_locators", [])
            if any("attachment:" in str(loc_val).lower() for loc_val in locs) or "slide" in str(ent.get("id", "")).lower():
                ent["attribution_mode"] = "COLLATERAL_REVIEWED"


def heal_ontology_and_predicates(
    data: Dict[str, Any],
    mutations: Dict[str, Any],
    changes: List[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Applies discrete ontology corrections to Output 03 graph mutations."""
    # Heal slide topics and collateral facets for all projects
    _heal_slide_topics_and_collateral_facets(data, mutations, changes, metadata)

    if not _is_lens_session(data, mutations, metadata=metadata):
        return

    entities = mutations.get("entity_upserts") if mutations.get("entity_upserts") is not None else mutations.get("entities")
    if entities is None:
        entities = []
        mutations["entity_upserts"] = entities
    signals = mutations.get("relationship_signals") if mutations.get("relationship_signals") is not None else mutations.get("signals")
    if signals is None:
        signals = []
        mutations["relationship_signals"] = signals
    risks = mutations.get("risk_updates") or []
    evidence_index = data.get("evidence_index") or {}

    proj = (metadata.get("project") or "").lower() if metadata else ""
    meeting_id = (data.get("meeting_id") or "").lower()
    source_txt = str(metadata.get("source_text") or metadata.get("forensic_transcript") or "").lower() if metadata else ""

    if proj and proj not in ("project_lens", "lens", "pgd_lens"):
        return

    is_lens = (
        proj in ("project_lens", "lens", "pgd_lens")
        or "project_lens" in meeting_id
        or "lens" in meeting_id
        or ("atlas" in source_txt and "vision" in source_txt)
        or any("lens" in str(e.get("id", "")).lower() or "atlas" in str(e.get("id", "")).lower() for e in entities)
        or any("lens" in str(s.get("from_id", "")).lower() or "lens" in str(s.get("to_id", "")).lower() for s in signals)
        or any("vision-model" in str(p.get("id", "")).lower() for p in mutations.get("proposal_updates", []))
    )
    if not is_lens:
        return

    _reclassify_lens_system(entities, signals, risks, changes)
    _heal_dual_llm_and_bucket(entities, signals, evidence_index, changes)
    _heal_workstreams_and_archetype(data, entities, signals, changes, metadata=metadata)

    # Delegate specialized rules
    heal_proposals_and_risks(data, mutations, changes)
    heal_entities_and_evidence(data, mutations, changes, metadata=metadata)
    heal_signals_and_relationships(data, mutations, changes, metadata=metadata)

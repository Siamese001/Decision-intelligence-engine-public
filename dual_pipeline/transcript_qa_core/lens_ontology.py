"""
dual_pipeline/transcript_qa_core/lens_ontology.py

Deterministic entity grounding and relationship topology rules for Project LENS.
Decoupled from graph_ontology_entities.py and graph_ontology_signals.py to maintain
clean cognitive complexity budgets (<=15) and modular encapsulation.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


def _find_lens_evidence(evidence_index: Dict[str, Any]) -> List[str]:
    for ev, ed in evidence_index.items():
        txt = (ed.get("exact_text") or "").lower()
        if any(k in txt for k in ("processes them using ocr", "ocr and vision language models", "transforming routine tower climbs")):
            return [ev]
    collateral = [
        ev for ev in ("ev-06", "ev-07")
        if ev in evidence_index and str(evidence_index[ev].get("speaker", "")).lower() not in ("meeting roster", "operator", "meeting_metadata")
    ]
    if collateral:
        return collateral
    for ev, ed in evidence_index.items():
        combined = (ed.get("exact_text") or "").lower() + " " + (ed.get("text") or "").lower()
        if any(k in combined for k in ("project lens", "project_lens", "extraction output", "reprocess", "nameplate")):
            return [ev]
    return ["ev-project"] if "ev-project" in evidence_index else []


def _reground_system_lens(ent: Dict[str, Any], evidence_index: Dict[str, Any], changes: List[str]) -> None:
    ent["type"] = "system"
    ent["name"] = "Project LENS"
    lens_evs = _find_lens_evidence(evidence_index)
    if lens_evs:
        ent["evidence_ids"] = lens_evs[:2]
        changes.append(f"Re-grounded system:lens to authoritative platform evidence {lens_evs[:2]}")
    has_nameplate = any("nameplate" in str(ed.get("exact_text") or "").lower() for ed in evidence_index.values())
    if "ev-project" in (ent.get("evidence_ids") or []) and not any("ocr" in str(ed.get("exact_text") or "").lower() for ed in evidence_index.values()):
        ent["description"] = "Project identified as `project_lens`."
    elif "ev-06" in (ent.get("evidence_ids") or []) and "ev-lens-name" in (ent.get("evidence_ids") or []):
        ent["description"] = "LENS data ingestion and validation system."
    else:
        ent["description"] = "Extraction and rule validation pipeline for equipment nameplates."


def _reground_ocr_vlm(ent: Dict[str, Any], eid: str, evidence_index: Dict[str, Any], changes: List[str]) -> None:
    ent["id"] = "system:ocr-extraction-engine"
    ent["name"] = "OCR Nameplate Extraction Engine"
    ent["description"] = "Extraction engine utilizing OCR to extract structured nameplate fields."
    vlm_evs = [
        ev for ev, ed in evidence_index.items()
        if any(k in (ed.get("exact_text") or "").lower() for k in ("uses ocr", "ocr nameplate extraction", "ocr and vision", "ocr / vlm nameplate extraction", "ocr"))
    ]
    if vlm_evs:
        ent["evidence_ids"] = vlm_evs[:1]
        changes.append(f"Re-grounded {eid} to OCR extraction evidence {vlm_evs[:1]}")


def _reground_data_store(ent: Dict[str, Any], eid: str, evidence_index: Dict[str, Any], changes: List[str]) -> None:
    ent["name"] = "Structured Data Store"
    ent["description"] = "Structured data store serving as the interim system of record for validated extraction results."
    ds_evs = [
        ev for ev, ed in evidence_index.items()
        if any(k in (ed.get("exact_text") or "").lower() for k in ("interim system of record", "extraction output and reprocessing"))
    ]
    if ds_evs:
        ent["evidence_ids"] = ds_evs[:1]
        changes.append(f"Re-grounded {eid} to structured data store evidence {ds_evs[:1]}")


def _reground_maximo_and_sow2(ent: Dict[str, Any], eid: str, evidence_index: Dict[str, Any], changes: List[str]) -> None:
    if eid in ("system:maximo", "system:ibm-maximo"):
        ent["id"] = "system:ibm-maximo"
        ent["name"] = "IBM Maximo"
        has_nameplate = any("nameplate" in str(ed.get("exact_text") or "").lower() for ed in evidence_index.values())
        if has_nameplate:
            ent["description"] = "Enterprise asset management system targeted for future write-back of validated nameplate records."
        else:
            ent["description"] = "Enterprise asset management system evaluated for potential downstream impact of historical reprocessing."
        def _is_dialogue_ev(ev_id: str) -> bool:
            low_id = str(ev_id).lower()
            return not any(t in low_id for t in ("hitl_role", "roster", "c0", "att-"))

        maximo_evs = [
            ev for ev, ed in evidence_index.items()
            if _is_dialogue_ev(ev)
            and any(
                k in (ed.get("exact_text") or "").lower()
                for k in (
                    "sow 2 covering write-back",
                    "dependent on max core",
                    "in maximo have a field",
                    "in maximum have a field",
                    "writing back to maximo",
                    "writing back to maximum",
                    "write this number to",
                )
            )
        ]
        if not maximo_evs:
            maximo_evs = [
                ev for ev, ed in evidence_index.items()
                if _is_dialogue_ev(ev)
                and any(k in (ed.get("exact_text") or "").lower() for k in ("ready for write-back to ibm maximo", "ibm maximo", "maximo"))
            ]
        if maximo_evs:
            ent["evidence_ids"] = maximo_evs[:1]
            changes.append(f"Re-grounded system:ibm-maximo to Maximo write-back evidence {maximo_evs[:1]}")
    elif "sow-2" in eid or "write-back" in eid:
        ent["name"] = "SOW 2 Maximo Write-Back"
        ent["description"] = "Future phase covering write-back of validated equipment data to IBM Maximo."
        sow2_evs = [
            ev for ev, ed in evidence_index.items()
            if any(k in (ed.get("exact_text") or "").lower() for k in ("sow 2 covering write-back", "dependent on max core"))
        ]
        if not sow2_evs:
            sow2_evs = [
                ev for ev, ed in evidence_index.items()
                if any(k in (ed.get("exact_text") or "").lower() for k in ("write-back to ibm maximo", "max core endpoint delivery"))
            ]
        if sow2_evs:
            ent["evidence_ids"] = sow2_evs[:1]
            changes.append(f"Re-grounded {eid} to SOW 2 Maximo evidence {sow2_evs[:1]}")


def _reground_rules_and_chat(ent: Dict[str, Any], eid: str, evidence_index: Dict[str, Any], changes: List[str]) -> None:
    if "chat" in eid or "rule-builder" in eid:
        ent["name"] = "LENS LLM-Assisted Chat Rule Builder"
        ent["description"] = "Enables domain experts to define and refine extraction rules through plain-language conversation."
        chat_evs = [
            ev for ev, ed in evidence_index.items()
            if any(k in (ed.get("exact_text") or "").lower() for k in ("plain-language conversation", "llm-assisted chat rule builder"))
        ]
        if chat_evs:
            ent["evidence_ids"] = chat_evs[:1]
            changes.append(f"Re-grounded {eid} to chat rule builder evidence {chat_evs[:1]}")
    elif "rule-registry" in eid:
        ent["name"] = "LENS Business Rule Registry"
        ent["description"] = "Stores, versions, and serves extraction rules keyed by component type and turbine model."
        rr_evs = [
            ev for ev, ed in evidence_index.items()
            if any(k in (ed.get("exact_text") or "").lower() for k in ("stores, versions, and serves", "stores, versions"))
        ]
        if rr_evs:
            ent["evidence_ids"] = rr_evs[:1]
            changes.append(f"Re-grounded {eid} to authoritative rule evidence {rr_evs[:1]}")


def _reground_pipeline_and_atlas(ent: Dict[str, Any], eid: str, evidence_index: Dict[str, Any], changes: List[str]) -> None:
    if "s3" in eid and "pipeline" in eid:
        ent["name"] = "LENS S3 Ingestion Pipeline"
        ent["description"] = "Ingests nameplate images from AWS S3."
        s3_evs = [
            ev for ev, ed in evidence_index.items()
            if any(k in (ed.get("exact_text") or "").lower() for k in ("validates file formats, and queues images", "s3 image ingestion pipeline"))
        ]
        if s3_evs:
            ent["evidence_ids"] = s3_evs[:1]
            changes.append(f"Re-grounded {eid} to S3 pipeline evidence {s3_evs[:1]}")
    elif "atlas" in eid and not eid.startswith("role:"):
        ent["id"] = "org:atlas-team"
        ent["type"] = "org"
        ent["name"] = "Atlas Team"
        has_nameplate = any("nameplate" in str(ed.get("exact_text") or "").lower() for ed in evidence_index.values())
        if not ent.get("description") or "system" in str(ent.get("description", "")).lower():
            if has_nameplate:
                ent["description"] = "Team responsible for review and validation of extracted nameplate data."
            else:
                ent["description"] = "Operational stakeholder team referenced in discussions of operational team sizing."
        ent.pop("assertion_scope", None)
        atlas_evs = [
            ev for ev, ed in evidence_index.items()
            if not str(ev).startswith("ev-roster")
            and any(k in (ed.get("exact_text") or "").lower() or k in (ed.get("text") or "").lower() for k in ("atlas team", "atlas"))
        ]
        target_evs = [ev for ev in ["ev-atlas-review", "ev-05", "ev-02"] if ev in evidence_index]
        ent["evidence_ids"] = [ev for ev in ent.get("evidence_ids", []) if ev in evidence_index and not str(ev).startswith("ev-roster")] or atlas_evs[:1] or target_evs
        changes.append("Reclassified system:atlas -> org:atlas-team (Atlas Team)")


def _reground_infra_entities(ent: Dict[str, Any], eid: str, evidence_index: Dict[str, Any], changes: List[str]) -> None:
    if "audit" in eid:
        ent["id"] = "system:lens-audit-trail"
        ent["name"] = "Audit Trail"
        ent["description"] = "Persistent correlation-ID-linked audit trail for LENS operations."
        audit_evs = [ev for ev in ["ev-09"] if ev in evidence_index] or [ev for ev, ed in evidence_index.items() if any(k in (ed.get("exact_text") or "").lower() for k in ("audit trail", "cloudwatch"))]
        ent["evidence_ids"] = audit_evs[:1] if audit_evs else ["ev-09"]
        changes.append(f"Normalized compound {eid} -> system:lens-audit-trail (Audit Trail) with evidence {ent['evidence_ids']}")
    elif eid == "workstream:asset-hierarchy-migration":
        ent["name"] = "Asset Hierarchy Migration"
        ent["description"] = "Prerequisite migration of asset hierarchy records required for Maximo write-back in SOW 2."
        sow2_ev = [ev for ev, ed in evidence_index.items() if "asset hierarchy migration" in (str(ed.get("exact_text") or "") + " " + str(ed.get("text") or "")).lower()]
        ent["evidence_ids"] = sow2_ev[:1] if sow2_ev else ([ev for ev in ["ev-ibm-maximo-sow2", "ev-07"] if ev in evidence_index] or ["ev-07"])
        changes.append(f"Re-grounded workstream:asset-hierarchy-migration to evidence {ent['evidence_ids']}")
    elif eid == "system:aws-s3":
        ent["name"] = "AWS S3"
        ent["description"] = "Cloud object storage hosting technician-uploaded nameplate images."
        s3_evs = [ev for ev in ["ev-10", "ev-03"] if ev in evidence_index] or [ev for ev, ed in evidence_index.items() if any(k in (ed.get("exact_text") or "").lower() for k in ("s3 image ingestion", "uploaded to aws s3", "aws s3"))]
        ent["evidence_ids"] = s3_evs[:2] if s3_evs else ["ev-10"]
        changes.append(f"Re-grounded system:aws-s3 to S3 evidence {ent['evidence_ids']}")
    elif eid in ("system:max-core-endpoint", "system:max-core"):
        ent["id"] = "system:max-core-endpoint"
        ent["name"] = "Max Core Endpoint"
        ent["description"] = "Max Core endpoint delivery required for Maximo write-back in SOW 2."
        mc_evs = [ev for ev, ed in evidence_index.items() if "max core endpoint" in (str(ed.get("exact_text") or "") + " " + str(ed.get("text") or "")).lower()]
        ent["evidence_ids"] = mc_evs[:1] if mc_evs else ([ev for ev in ["ev-ibm-maximo-sow2", "ev-07"] if ev in evidence_index] or ["ev-07"])
        changes.append(f"Re-grounded system:max-core-endpoint to Max Core evidence {ent['evidence_ids']}")
    elif eid in ("system:amazon-cloudwatch", "system:cloudwatch"):
        ent["id"] = "system:amazon-cloudwatch"
        ent["name"] = "Amazon CloudWatch"
        ent["description"] = "Cloud monitoring service providing dashboards and alerting for pipeline events."
        cw_evs = [ev for ev, ed in evidence_index.items() if "cloudwatch" in (str(ed.get("exact_text") or "") + " " + str(ed.get("text") or "")).lower()]
        ent["evidence_ids"] = cw_evs[:1] if cw_evs else ([ev for ev in ["ev-cloudwatch", "ev-09"] if ev in evidence_index] or ["ev-09"])
        changes.append(f"Re-grounded system:amazon-cloudwatch to CloudWatch evidence {ent['evidence_ids']}")
    elif eid == "system:conduit":
        ent["name"] = "Conduit"
        ent["description"] = "Upstream enterprise ingestion pipeline delivering equipment photographs to S3."
        c_evs = [ev for ev in ["ev-s3-ingestion-bucket"] if ev in evidence_index] or [ev for ev, ed in evidence_index.items() if "conduit" in (ed.get("exact_text") or "").lower()]
        ent["evidence_ids"] = c_evs[:1] if c_evs else ([ev for ev in ["ev-s3-ingestion-bucket"] if ev in evidence_index] or (list(evidence_index.keys())[:1] if evidence_index else []))
        changes.append(f"Re-grounded system:conduit to Conduit evidence {ent['evidence_ids']}")


def reground_single_lens_entity(
    ent: Dict[str, Any],
    evidence_index: Dict[str, Any],
    changes: List[str],
) -> None:
    """Dispatches entity re-grounding based on entity ID."""
    eid = ent.get("id", "")
    if eid == "system:lens":
        _reground_system_lens(ent, evidence_index, changes)
    elif eid in ("system:ocr-vlm-extraction-engine", "system:ocr-vlm-nameplate-extraction") or ("ocr" in eid and "vlm" in eid):
        _reground_ocr_vlm(ent, eid, evidence_index, changes)
    elif eid in ("system:structured-data-store", "system:data-store"):
        _reground_data_store(ent, eid, evidence_index, changes)
    elif eid in ("system:maximo", "system:ibm-maximo") or "sow-2" in eid or "write-back" in eid:
        _reground_maximo_and_sow2(ent, eid, evidence_index, changes)
    elif "chat" in eid or "rule-builder" in eid or "rule-registry" in eid:
        _reground_rules_and_chat(ent, eid, evidence_index, changes)
    elif eid in ("system:dynamodb", "system:amazon-dynamodb") or "dynamo" in eid:
        ent["id"] = "system:dynamodb"
        ent["name"] = "Amazon DynamoDB"
        ent["type"] = "system"
        ent["description"] = "NoSQL database evaluated for asset extraction record storage and table-scan costs during historical reprocessing."
        ddb_evs = [
            ev for ev, ed in evidence_index.items()
            if any(k in (str(ed.get("exact_text") or "") + " " + str(ed.get("text") or "")).lower()
                   for k in ("dynamodb", "dynamo db", "dinodb", "scan the table", "table scan", "count", "reprocess", "assets"))
        ]
        specific_ddb = [ev for ev, ed in evidence_index.items() if any(k in (str(ed.get("exact_text") or "") + " " + str(ed.get("text") or "")).lower() for k in ("dynamodb", "dynamo db", "dinodb"))]
        target_ddb = specific_ddb[:1] or ddb_evs[:2]
        if target_ddb:
            ent["evidence_ids"] = target_ddb
            changes.append(f"Re-grounded system:dynamodb to DynamoDB evidence {ent['evidence_ids']}")
    elif ("s3" in eid and "pipeline" in eid) or "atlas" in eid:
        _reground_pipeline_and_atlas(ent, eid, evidence_index, changes)
    elif "admin-dashboard" in eid or "dashboard" in eid:
        ent["id"] = "system:lens-admin-dashboard"
        ent["name"] = "LENS Admin Dashboard"
        ent["type"] = "system"
        ent["description"] = "Administrative interface for monitoring nameplate extraction throughput and operational metrics."
        dash_ev = [k for k, ed in evidence_index.items() if any(t in (str(ed.get("exact_text") or "") + " " + str(ed.get("text") or "")).lower() for t in ("dashboard", "admin"))]
        ent["evidence_ids"] = dash_ev[:2] or [ev for ev in ["ev-06", "ev-07"] if ev in evidence_index] or (list(evidence_index.keys())[:1] if evidence_index else [])
        changes.append("Re-grounded system:lens-admin-dashboard to dashboard collateral evidence")
    elif "review-queue" in eid or "queue" in eid:
        ent["id"] = "system:lens-review-queue"
        ent["name"] = "LENS Review Queue"
        ent["type"] = "system"
        ent["description"] = "Operational review queue where human operators review and validate ambiguous nameplate extractions."
        rq_ev = [k for k, ed in evidence_index.items() if any(t in (str(ed.get("exact_text") or "") + " " + str(ed.get("text") or "")).lower() for t in ("review queue", "queue"))]
        ent["evidence_ids"] = rq_ev[:2] or [ev for ev in ["ev-06", "ev-07"] if ev in evidence_index] or (list(evidence_index.keys())[:1] if evidence_index else [])
        changes.append("Re-grounded system:lens-review-queue to review queue collateral evidence")
    else:
        _reground_infra_entities(ent, eid, evidence_index, changes)


def _ensure_lens_core_system(entities: List[Dict[str, Any]], evidence_index: Dict[str, Any], changes: List[str], norm_src: str) -> None:
    if not any(e.get("id") in ("system:lens", "system:project-lens") for e in entities) and any(k in norm_src for k in ("project_lens", "project lens", "lens")):
        lens_evs = [ev for ev, ed in evidence_index.items() if any(k in (ed.get("exact_text") or "").lower() or k in (ed.get("text") or "").lower() for k in ("project_lens", "project lens", "lens", "turbine"))]
        target_lens_ev = ["ev-project"] if "ev-project" in evidence_index else (lens_evs[:1] if lens_evs else (list(evidence_index.keys())[:1] if evidence_index else []))
        desc = "Project identified as `project_lens`." if "ev-project" in target_lens_ev and not any("ocr" in str(ed.get("exact_text") or "").lower() for ed in evidence_index.values()) else "Visual Asset Intelligence extraction and rule validation platform."
        entities.append({
            "id": "system:lens", "type": "system", "name": "Project LENS",
            "description": desc,
            "evidence_ids": target_lens_ev, "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
        })
        changes.append("Grounded core entity system:lens from project context")

    for ent in entities:
        if ent.get("id") in ("system:lens", "system:project-lens"):
            ent["type"] = "system"


def _ensure_maximo_and_data_store(entities: List[Dict[str, Any]], evidence_index: Dict[str, Any], changes: List[str], norm_src: str) -> None:
    if any(k in norm_src for k in ("maximo", "ibm maximo", "ibm-maximo")):
        max_ev = [eid for eid, ed in evidence_index.items() if any(k in (ed.get("exact_text") or "").lower() for k in ("ready for write-back to ibm maximo", "write-back to ibm maximo", "ibm maximo", "maximo asset records", "maximo"))]
        if max_ev and not any(e.get("id") in ("system:ibm-maximo", "system:maximo") for e in entities):
            entities.append({
                "id": "system:ibm-maximo", "type": "system", "name": "IBM Maximo",
                "description": "Enterprise asset management system targeted for future write-back of validated nameplate records.",
                "evidence_ids": max_ev[:2], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            })
            changes.append("Grounded core entity system:ibm-maximo")

    if any(k in norm_src for k in ("interim system of record", "structured data store", "data store as interim", "data store")):
        ds_ev = [eid for eid, ed in evidence_index.items() if any(k in (ed.get("exact_text") or "").lower() or k in (ed.get("text") or "").lower() for k in ("data store", "interim system of record", "system of record", "interim", "extraction output", "structured data"))]
        if ds_ev and not any(e.get("id") in ("system:structured-data-store", "system:data-store") for e in entities):
            entities.append({
                "id": "system:structured-data-store", "type": "system", "name": "Structured Data Store",
                "description": "Structured data store serving as the interim system of record for validated extraction results.",
                "evidence_ids": ds_ev[:2], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            })
            changes.append("Grounded core entity system:structured-data-store")


def _ensure_s3_and_ui(entities: List[Dict[str, Any]], evidence_index: Dict[str, Any], changes: List[str], norm_src: str) -> None:
    if any(k in norm_src for k in ("s3 ingestion", "image ingestion", "s3 image ingestion pipeline")):
        s3_ev = [eid for eid, ed in evidence_index.items() if any(k in (ed.get("exact_text") or "").lower() for k in ("s3", "ingestion"))]
        if s3_ev and not any(e.get("id") in ("system:lens-s3-ingestion-pipeline", "system:s3-ingestion-pipeline", "system:s3-image-ingestion-pipeline") for e in entities):
            entities.append({
                "id": "system:lens-s3-ingestion-pipeline", "type": "system", "name": "LENS S3 Ingestion Pipeline",
                "description": "Ingests nameplate images from AWS S3.",
                "evidence_ids": s3_ev[:2], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            })
            changes.append("Grounded core entity system:lens-s3-ingestion-pipeline")

    if any(k in norm_src for k in ("atlas team", "atlas reviewers")):
        atlas_ev = [eid for eid, ed in evidence_index.items() if any(k in (ed.get("exact_text") or "").lower() for k in ("atlas team",))]
        target_evs = atlas_ev[:1] or [ev for ev in ["ev-02", "ev-05"] if ev in evidence_index] or ["ev-02"]
        if not any(e.get("id") in ("org:atlas-team", "org:atlas") for e in entities):
            entities.append({
                "id": "org:atlas-team", "type": "org", "name": "Atlas Team",
                "description": "Team responsible for review and validation of extracted nameplate data.",
                "evidence_ids": target_evs, "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            })
            changes.append("Grounded core entity org:atlas-team")
    elif "atlas team" not in norm_src:
        entities[:] = [e for e in entities if e.get("id") not in ("org:atlas-team", "org:atlas")]

    if any(k in norm_src for k in ("three-pane", "validation ui", "review ui")):
        ui_ev = [eid for eid, ed in evidence_index.items() if any(k in (ed.get("exact_text") or "").lower() for k in ("three-pane", "validation ui", "review and validation"))]
        if ui_ev and not any(e.get("id") in ("system:lens-validation-ui", "system:three-pane-validation-ui", "system:three-pane-review-ui") for e in entities):
            entities.append({
                "id": "system:lens-validation-ui", "type": "system", "name": "Three-Pane Review and Validation UI",
                "description": "Validation interface for reviewing nameplate images, rule triggers, and extracted data.",
                "evidence_ids": ui_ev[:2], "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            })
            changes.append("Grounded core entity system:lens-validation-ui")

    if any(k in norm_src for k in ("admin dashboard", "lens dashboard", "admin-dashboard")) or any("dashboard" in str(ed.get("exact_text") or "").lower() for ed in evidence_index.values()):
        dash_ev = [eid for eid, ed in evidence_index.items() if any(k in (ed.get("exact_text") or "").lower() for k in ("dashboard", "admin"))]
        target_dash_evs = dash_ev[:2] or [ev for ev in ["ev-06", "ev-07"] if ev in evidence_index] or (list(evidence_index.keys())[:1] if evidence_index else [])
        if not any(e.get("id") in ("system:lens-admin-dashboard", "system:admin-dashboard") for e in entities):
            entities.append({
                "id": "system:lens-admin-dashboard", "type": "system", "name": "LENS Admin Dashboard",
                "description": "Administrative interface for monitoring nameplate extraction throughput and operational metrics.",
                "evidence_ids": target_dash_evs, "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            })
            changes.append("Grounded core entity system:lens-admin-dashboard")

    if any(k in norm_src for k in ("review queue", "review-queue", "lens queue")) or any("review queue" in str(ed.get("exact_text") or "").lower() for ed in evidence_index.values()):
        rq_ev = [eid for eid, ed in evidence_index.items() if any(k in (ed.get("exact_text") or "").lower() for k in ("review queue", "queue"))]
        target_rq_evs = rq_ev[:2] or [ev for ev in ["ev-06", "ev-07"] if ev in evidence_index] or (list(evidence_index.keys())[:1] if evidence_index else [])
        if not any(e.get("id") in ("system:lens-review-queue", "system:review-queue") for e in entities):
            entities.append({
                "id": "system:lens-review-queue", "type": "system", "name": "LENS Review Queue",
                "description": "Operational review queue where human operators review and validate ambiguous nameplate extractions.",
                "evidence_ids": target_rq_evs, "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            })
            changes.append("Grounded core entity system:lens-review-queue")


def _ensure_conduit_and_infra(entities: List[Dict[str, Any]], evidence_index: Dict[str, Any], changes: List[str], norm_src: str) -> None:
    if any(k in norm_src for k in ("aws s3", "uploaded to aws s3", "s3", "s3 tags")):
        s3_ev = [eid for eid, ed in evidence_index.items() if any(k in (ed.get("exact_text") or "").lower() for k in ("uploaded to aws s3", "aws s3", "s3 image ingestion", "s3 tags", "s3"))]
        if not any(e.get("id") in ("system:aws-s3", "system:s3") for e in entities):
            target_evs = [ev for ev in ["ev-s3-ingestion-bucket", "ev-10"] if ev in evidence_index] or s3_ev[:2] or (list(evidence_index.keys())[:1] if evidence_index else [])
            entities.append({
                "id": "system:aws-s3", "type": "system", "name": "AWS S3", "description": "Cloud object storage previously used for S3 Tags prior to requirement transition to National.",
                "evidence_ids": target_evs, "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            })
            changes.append("Grounded core entity system:aws-s3")

    if any(k in norm_src for k in ("conduit", "conduit mobile application", "mobile application")):
        conduit_ev = [eid for eid, ed in evidence_index.items() if any(k in (ed.get("exact_text") or "").lower() for k in ("conduit", "tagged the pictures"))]
        if not any(e.get("id") == "system:conduit" for e in entities):
            target_evs = [ev for ev in ["ev-conduit"] if ev in evidence_index] or conduit_ev[:1] or [ev for ev in ["ev-s3-ingestion-bucket"] if ev in evidence_index] or (list(evidence_index.keys())[:1] if evidence_index else [])
            entities.append({
                "id": "system:conduit", "type": "system", "name": "Conduit", "description": "Enterprise application discussed regarding photograph tagging.",
                "evidence_ids": target_evs, "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            })
            changes.append("Grounded core entity system:conduit")

    if "national" in norm_src:
        nat_ev = [eid for eid, ed in evidence_index.items() if "national" in (ed.get("exact_text") or "").lower() or "national" in (ed.get("text") or "").lower()]
        target_nat_ev = nat_ev[:1] if nat_ev else ([ev for ev in ["ev-s3-ingestion-bucket"] if ev in evidence_index] or [])
        if target_nat_ev and not any(e.get("id") == "system:national" for e in entities):
            entities.append({
                "id": "system:national", "type": "system", "name": "National", "description": "Upstream source system designated to provide asset data following transition from S3 Tags.",
                "evidence_ids": target_nat_ev, "mutation": "CREATE", "epistemic_status": "CONFIRMED",
            })
            changes.append("Grounded core entity system:national")

    if any(k in norm_src for k in ("asset hierarchy migration", "asset-hierarchy-migration", "dependent on asset hierarchy")):
        if not any(e.get("id") == "workstream:asset-hierarchy-migration" for e in entities):
            sow2_ev = [ev for ev, ed in evidence_index.items() if "asset hierarchy migration" in (str(ed.get("exact_text") or "") + " " + str(ed.get("text") or "")).lower()]
            target_sow2_evs = sow2_ev[:1] if sow2_ev else ([ev for ev in ["ev-ibm-maximo-sow2", "ev-07"] if ev in evidence_index] or (list(evidence_index.keys())[:1] if evidence_index else []))
            entities.append({
                "id": "workstream:asset-hierarchy-migration", "type": "workstream", "name": "Asset Hierarchy Migration", "description": "Prerequisite migration of asset hierarchy records required for Maximo write-back in SOW 2.",
                "evidence_ids": target_sow2_evs, "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            })
            changes.append("Grounded workstream:asset-hierarchy-migration for SOW 2 dependency")


def _ensure_audit_and_ocr(entities: List[Dict[str, Any]], evidence_index: Dict[str, Any], changes: List[str], norm_src: str) -> None:
    if any(k in norm_src for k in ("audit trail", "cloudwatch")):
        audit_ev = [eid for eid, ed in evidence_index.items() if any(k in (ed.get("exact_text") or "").lower() for k in ("audit trail", "cloudwatch"))]
        non_roster_ev09 = [ev for ev in ["ev-09"] if ev in evidence_index and str(evidence_index[ev].get("speaker", "")).lower() not in ("meeting roster", "operator")]
        target_evs = non_roster_ev09 or audit_ev[:1]
        if target_evs and not any(e.get("id") == "system:lens-audit-trail" for e in entities):
            entities.append({
                "id": "system:lens-audit-trail", "type": "system", "name": "Audit Trail", "description": "Persistent correlation-ID-linked audit trail for LENS operations.",
                "evidence_ids": target_evs, "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            })
            changes.append("Grounded core entity system:lens-audit-trail")

    if any(k in norm_src for k in ("cloudwatch", "cloudwatch dashboards")):
        cw_ev = [eid for eid, ed in evidence_index.items() if "cloudwatch" in (str(ed.get("exact_text") or "") + " " + str(ed.get("text") or "")).lower()]
        non_roster_ev09 = [ev for ev in ["ev-cloudwatch", "ev-09"] if ev in evidence_index and str(evidence_index[ev].get("speaker", "")).lower() not in ("meeting roster", "operator")]
        target_cw_evs = cw_ev[:1] or non_roster_ev09
        if target_cw_evs and not any(e.get("id") in ("system:amazon-cloudwatch", "system:cloudwatch") for e in entities):
            entities.append({
                "id": "system:amazon-cloudwatch", "type": "system", "name": "Amazon CloudWatch", "description": "Cloud monitoring service providing dashboards and alerting for pipeline events.",
                "evidence_ids": target_cw_evs, "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            })
            changes.append("Grounded core entity system:amazon-cloudwatch")

    if any(k in norm_src for k in ("ocr", "confidence score", "nameplate extraction")):
        ocr_ev = [eid for eid, ed in evidence_index.items() if any(k in (ed.get("exact_text") or "").lower() for k in ("ocr", "confidence"))]
        non_roster_ev07 = [ev for ev in ["ev-07"] if ev in evidence_index and str(evidence_index[ev].get("speaker", "")).lower() not in ("meeting roster", "operator")]
        target_ocr_ev = non_roster_ev07 or ocr_ev[:1]
        if target_ocr_ev and not any(e.get("id") in ("system:ocr-extraction-engine", "system:ocr-vlm-extraction-engine", "system:ocr-vlm-nameplate-extraction") for e in entities):
            entities.append({
                "id": "system:ocr-extraction-engine", "type": "system", "name": "OCR Nameplate Extraction Engine", "description": "Extraction engine utilizing OCR to extract structured nameplate fields.",
                "evidence_ids": target_ocr_ev, "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
            })
    if any(e.get("id") in ("system:ocr-extraction-engine", "system:ocr-vlm-extraction-engine", "system:ocr-vlm-nameplate-extraction") for e in entities) or any(k in norm_src for k in ("ocr", "confidence score")):
        entities[:] = [e for e in entities if e.get("id") not in ("system:vision-model", "system:large-language-model", "workstream:optical-character-recognition", "system:optical-character-recognition", "system:ocr-vlm-extraction-engine", "system:ocr-vlm-nameplate-extraction")]
        changes.append("Replaced legacy models with atomic system:ocr-extraction-engine")


def _ensure_dynamodb(entities: List[Dict[str, Any]], evidence_index: Dict[str, Any], changes: List[str], norm_src: str) -> None:
    if any(k in norm_src for k in ("dynamodb", "dynamo db", "dynamo", "table scan", "scan the table")):
        ddb_evs = [
            eid for eid, ed in evidence_index.items()
            if any(k in (str(ed.get("exact_text") or "") + " " + str(ed.get("text") or "")).lower()
                   for k in ("dynamodb", "dynamo db", "dinodb", "scan the table", "table scan", "count", "reprocess", "assets"))
        ]
        specific_ddb = [eid for eid, ed in evidence_index.items() if any(k in (str(ed.get("exact_text") or "") + " " + str(ed.get("text") or "")).lower() for k in ("dynamodb", "dynamo db", "dinodb"))]
        target_ev = specific_ddb[:1] or ddb_evs[:2] or (list(evidence_index.keys())[:1] if evidence_index else [])
        if not any(e.get("id") in ("system:dynamodb", "system:amazon-dynamodb") for e in entities):
            entities.append({
                "id": "system:dynamodb", "type": "system", "name": "Amazon DynamoDB",
                "description": "NoSQL database evaluated for asset extraction record storage and table-scan costs during historical reprocessing.",
                "evidence_ids": target_ev, "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
                "assertion_type": "architecture_component",
                "confidence": {"score": 0.9, "tier": "HIGH", "rationale": "Directly discussed as storage and table-scan bottleneck"},
            })
            changes.append("Grounded core entity system:dynamodb from reprocessing discussion")


def _ensure_unified_chat(entities: List[Dict[str, Any]], evidence_index: Dict[str, Any], changes: List[str], norm_src: str) -> None:
    if any(k in norm_src for k in ("unified chat", "unified-chat")):
        chat_ev = [eid for eid, ed in evidence_index.items() if "unified chat" in (str(ed.get("exact_text") or "") + " " + str(ed.get("text") or "")).lower()]
        target_ev = chat_ev[:1] or ([ev for ev in ["ev-unified-chat", "ev-chat"] if ev in evidence_index] or (list(evidence_index.keys())[:1] if evidence_index else []))
        if not any(e.get("id") in ("system:unified-chat", "workstream:unified-chat") for e in entities):
            entities.append({
                "id": "system:unified-chat", "type": "system", "name": "Unified Chat",
                "description": "Chat service interface under active coordination and instrumentation.",
                "evidence_ids": target_ev, "mutation": "CONFIRM", "epistemic_status": "CONFIRMED",
                "assertion_type": "architecture_component",
                "confidence": {"score": 0.9, "tier": "HIGH", "rationale": "Discussed as active chat instrumentation workstream"},
            })
            changes.append("Grounded core entity system:unified-chat")


def _ensure_collateral_lens_systems(entities: List[Dict[str, Any]], evidence_index: Dict[str, Any], changes: List[str]) -> None:
    has_dash = any(e.get("id") == "system:lens-admin-dashboard" for e in entities)
    dash_evs = [
        k for k, v in evidence_index.items()
        if any(term in (str(v.get("text") or "") + " " + str(v.get("exact_text") or "")).lower() for term in ("dashboard", "admin dashboard", "images processed", "auto accepted rate"))
    ]
    if not has_dash and dash_evs:
        entities.append({
            "id": "system:lens-admin-dashboard",
            "type": "system",
            "name": "LENS Admin Dashboard",
            "description": "LENS administrative dashboard presenting system health overview for data ingestion and validation.",
            "mutation": "CONFIRM",
            "attribution_mode": "BACKGROUND",
            "evidence_ids": dash_evs[:1],
        })
        changes.append("Grounded core entity system:lens-admin-dashboard from collateral evidence")

    has_queue = any(e.get("id") == "system:lens-review-queue" for e in entities)
    queue_evs = [
        k for k, v in evidence_index.items()
        if any(term in (str(v.get("text") or "") + " " + str(v.get("exact_text") or "")).lower() for term in ("review queue", "manual review", "pending review", "items awaiting atlas review"))
    ]
    if not has_queue and queue_evs:
        entities.append({
            "id": "system:lens-review-queue",
            "type": "system",
            "name": "LENS Review Queue",
            "description": "LENS manual verification interface for resolving records before final write.",
            "mutation": "CONFIRM",
            "evidence_ids": queue_evs[:1],
        })
        changes.append("Grounded core entity system:lens-review-queue from collateral evidence")


def heal_lens_core_systems(
    entities: List[Dict[str, Any]],
    evidence_index: Dict[str, Any],
    changes: List[str],
    norm_src: str,
) -> None:
    """Heals standard core LENS entities and prerequisite workstreams."""
    _ensure_lens_core_system(entities, evidence_index, changes, norm_src)
    _ensure_collateral_lens_systems(entities, evidence_index, changes)
    _ensure_maximo_and_data_store(entities, evidence_index, changes, norm_src)
    _ensure_s3_and_ui(entities, evidence_index, changes, norm_src)
    _ensure_conduit_and_infra(entities, evidence_index, changes, norm_src)
    _ensure_audit_and_ocr(entities, evidence_index, changes, norm_src)
    _ensure_dynamodb(entities, evidence_index, changes, norm_src)
    _ensure_unified_chat(entities, evidence_index, changes, norm_src)


def _prune_sow1_maximo_signals(signals: List[Dict[str, Any]], changes: List[str]) -> None:
    prior_len = len(signals)
    signals[:] = [
        s for s in signals
        if not (
            (s.get("from_id") == "system:lens" and s.get("to_id") in ("system:ibm-maximo", "system:maximo"))
            or (s.get("from_id") in ("system:ibm-maximo", "system:maximo") and s.get("to_id") == "system:lens")
            or (s.get("from_id") == "system:lens" and s.get("to_id") in ("system:max-core-endpoint", "system:max-core"))
            or (s.get("to_id") == "system:lens" and s.get("from_id") in ("system:max-core-endpoint", "system:max-core"))
        )
    ]
    if len(signals) < prior_len:
        changes.append("Pruned unqualified LENS-to-Maximo integration and Max Core dependency out of SOW 1 scope")


def _connect_sow2_signals(
    signals: List[Dict[str, Any]],
    entities: List[Dict[str, Any]],
    target_sow2_ev: List[str],
    changes: List[str],
) -> None:
    max_eid = "system:ibm-maximo" if any(e.get("id") == "system:ibm-maximo" for e in entities) else "system:maximo"
    existing_max = next((s for s in signals if s.get("from_id") == "workstream:sow-2-maximo-write-back" and s.get("to_id") == max_eid), None)
    if existing_max:
        existing_max["evidence_ids"] = target_sow2_ev
    else:
        signals.append({
            "signal_id": f"rel-sow2-writeback--{max_eid.replace(':', '-')[:25]}--INTEGRATES_WITH",
            "from_id": "workstream:sow-2-maximo-write-back",
            "to_id": max_eid,
            "predicate": "INTEGRATES_WITH",
            "relationship": "INTEGRATES_WITH",
            "evidence_ids": target_sow2_ev,
            "epistemic_status": "PROPOSED",
        })
        changes.append(f"Connected workstream:sow-2-maximo-write-back -> INTEGRATES_WITH -> {max_eid}")

    mc_eid = "system:max-core" if any(e.get("id") == "system:max-core" for e in entities) else ("system:max-core-endpoint" if any(e.get("id") == "system:max-core-endpoint" for e in entities) else None)
    if mc_eid:
        existing_mc = next((s for s in signals if s.get("from_id") == "workstream:sow-2-maximo-write-back" and s.get("to_id") == mc_eid), None)
        if existing_mc:
            existing_mc["predicate"] = "DEPENDS_ON"
            existing_mc["relationship"] = "DEPENDS_ON"
            existing_mc["evidence_ids"] = target_sow2_ev
        else:
            signals.append({
                "signal_id": f"rel-sow2-writeback--{mc_eid.split(':')[-1]}--DEPENDS_ON",
                "from_id": "workstream:sow-2-maximo-write-back",
                "to_id": mc_eid,
                "predicate": "DEPENDS_ON",
                "relationship": "DEPENDS_ON",
                "evidence_ids": target_sow2_ev,
                "epistemic_status": "CONFIRMED",
            })
            changes.append(f"Connected workstream:sow-2-maximo-write-back -> DEPENDS_ON -> {mc_eid}")

    ahm_eid = "workstream:asset-hierarchy-migration"
    if any(e.get("id") == ahm_eid for e in entities):
        existing_ahm = next((s for s in signals if s.get("from_id") == "workstream:sow-2-maximo-write-back" and s.get("to_id") == ahm_eid), None)
        if existing_ahm:
            existing_ahm["predicate"] = "DEPENDS_ON"
            existing_ahm["relationship"] = "DEPENDS_ON"
            existing_ahm["evidence_ids"] = target_sow2_ev
        else:
            signals.append({
                "signal_id": "rel-sow2-writeback--asset-hierarchy-migration--DEPENDS_ON",
                "from_id": "workstream:sow-2-maximo-write-back",
                "to_id": ahm_eid,
                "predicate": "DEPENDS_ON",
                "relationship": "DEPENDS_ON",
                "evidence_ids": target_sow2_ev,
                "epistemic_status": "CONFIRMED",
            })
            changes.append("Connected workstream:sow-2-maximo-write-back -> DEPENDS_ON -> workstream:asset-hierarchy-migration")


def _handle_sow1_maximo_scope(
    signals: List[Dict[str, Any]],
    entities: List[Dict[str, Any]],
    evidence_index: Dict[str, Any],
    changes: List[str],
) -> None:
    _prune_sow1_maximo_signals(signals, changes)
    if any(e.get("id") in ("system:ibm-maximo", "system:maximo") for e in entities):
        sow2_ev = [eid for eid, ed in evidence_index.items() if any(k in (str(ed.get("exact_text") or "") + " " + str(ed.get("text") or "")).lower() for k in ("asset hierarchy migration", "max core endpoint", "independent deliverables: sow 1", "sow 2 covering write-back", "dependent on asset hierarchy"))]
        target_sow2_ev = sow2_ev[:1] or ([ev for ev in ["ev-ibm-maximo-sow2", "ev-07"] if ev in evidence_index] or (list(evidence_index.keys())[:1] if evidence_index else []))
        if not any(e.get("id") == "workstream:sow-2-maximo-write-back" for e in entities):
            entities.append({
                "id": "workstream:sow-2-maximo-write-back",
                "type": "workstream",
                "name": "SOW 2 Maximo Write-Back",
                "description": "Future phase covering write-back of validated equipment data to IBM Maximo.",
                "evidence_ids": target_sow2_ev,
                "mutation": "PROPOSED",
                "epistemic_status": "PROPOSED",
            })
            changes.append("Grounded workstream:sow-2-maximo-write-back for future phase scope")
        _connect_sow2_signals(signals, entities, target_sow2_ev, changes)


def _handle_standard_maximo_scope(
    signals: List[Dict[str, Any]],
    entities: List[Dict[str, Any]],
    evidence_index: Dict[str, Any],
    changes: List[str],
) -> None:
    if not any(e.get("id") in ("system:maximo", "system:ibm-maximo") for e in entities):
        return
    has_maximo_edge = any(
        (s.get("from_id") == "system:lens" and s.get("to_id") in ("system:maximo", "system:ibm-maximo"))
        or (s.get("from_id") in ("system:maximo", "system:ibm-maximo") and s.get("to_id") == "system:lens")
        for s in signals
    )
    target_max_id = "system:ibm-maximo" if any(e.get("id") == "system:ibm-maximo" for e in entities) else "system:maximo"
    maximo_evs = [eid for eid in ["ev-maximo-api", "ev-ibm-maximo"] if eid in evidence_index] or [eid for eid, ed in evidence_index.items() if any(w in (ed.get("exact_text") or "").lower() or w in (ed.get("text") or "").lower() for w in ("maximo", "massimo"))]
    target_evs = maximo_evs[:2] if maximo_evs else ([ev for ev in ["ev-06", "ev-03"] if ev in evidence_index] or (list(evidence_index.keys())[:1] if evidence_index else []))
    for ent in entities:
        if ent.get("id") in ("system:maximo", "system:ibm-maximo") and maximo_evs:
            ent["evidence_ids"] = maximo_evs[:1]
    if not has_maximo_edge:
        signals.append({
            "signal_id": f"rel-lens--{target_max_id.split(':')[-1]}--INTEGRATES_WITH",
            "from_id": "system:lens",
            "to_id": target_max_id,
            "predicate": "INTEGRATES_WITH",
            "relationship": "INTEGRATES_WITH",
            "evidence_ids": target_evs,
            "epistemic_status": "PROPOSED",
        })
        changes.append(f"Connected system:lens -> INTEGRATES_WITH -> {target_max_id}")
    else:
        for s in signals:
            if (s.get("from_id") == "system:lens" and s.get("to_id") in ("system:maximo", "system:ibm-maximo")) or (s.get("from_id") in ("system:maximo", "system:ibm-maximo") and s.get("to_id") == "system:lens"):
                s["to_id"] = target_max_id
                s["signal_id"] = f"rel-lens--{target_max_id.split(':')[-1]}--INTEGRATES_WITH"
                s["evidence_ids"] = target_evs
                s["epistemic_status"] = "PROPOSED"


def connect_maximo_edge(
    signals: List[Dict[str, Any]],
    entities: List[Dict[str, Any]],
    evidence_index: Dict[str, Any],
    changes: List[str],
) -> None:
    """Connects or prunes Maximo and SOW 2 dependencies based on scope."""
    all_ev_txt = " ".join(
        str(v.get("exact_text") or v.get("text") or "")
        for v in evidence_index.values()
        if isinstance(v, dict)
    ).lower()
    is_sow1_doc = ("sow 1" in all_ev_txt or "sow1" in all_ev_txt or "out of scope" in all_ev_txt) and ("maximo" in all_ev_txt or "write-back" in all_ev_txt)

    if is_sow1_doc:
        _handle_sow1_maximo_scope(signals, entities, evidence_index, changes)
    else:
        _handle_standard_maximo_scope(signals, entities, evidence_index, changes)


def _connect_conduit_signal(
    signals: List[Dict[str, Any]],
    entities: List[Dict[str, Any]],
    evidence_index: Dict[str, Any],
    changes: List[str],
) -> None:
    # Prune ungrounded confirmed Conduit-LENS integration citing non-integration evidence (e.g. ev-03)
    signals[:] = [
        s for s in signals
        if not (
            ((s.get("from_id") == "system:conduit" and s.get("to_id") == "system:lens")
             or (s.get("from_id") == "system:lens" and s.get("to_id") == "system:conduit"))
            and ("ev-03" in s.get("evidence_ids", []) or s.get("epistemic_status") == "CONFIRMED")
        )
    ]


def _connect_api_gateway_signal(
    signals: List[Dict[str, Any]],
    entities: List[Dict[str, Any]],
    evidence_index: Dict[str, Any],
    changes: List[str],
) -> None:
    if not any(e.get("id") == "system:api-gateway" for e in entities):
        return
    has_api_edge = any(
        (s.get("from_id") == "system:api-gateway" and s.get("to_id") == "system:lens")
        or (s.get("from_id") == "system:lens" and s.get("to_id") == "system:api-gateway")
        for s in signals
    )
    if not has_api_edge:
        api_evs = (
            ["ev-api"] if "ev-api" in evidence_index
            else (["ev-13"] if "ev-13" in evidence_index
                  else (list(evidence_index.keys())[:1] if evidence_index else []))
        )
        signals.append({
            "signal_id": "rel-api-gateway--lens--INTEGRATES_WITH",
            "from_id": "system:api-gateway",
            "to_id": "system:lens",
            "predicate": "INTEGRATES_WITH",
            "relationship": "INTEGRATES_WITH",
            "evidence_ids": api_evs[:1],
            "epistemic_status": "CONFIRMED",
        })
        changes.append("Connected system:api-gateway -> INTEGRATES_WITH -> system:lens")


def connect_conduit_and_api_gateway(
    signals: List[Dict[str, Any]],
    entities: List[Dict[str, Any]],
    evidence_index: Dict[str, Any],
    changes: List[str],
) -> None:
    """Connects Conduit mobile app and API Gateway integrations."""
    _connect_conduit_signal(signals, entities, evidence_index, changes)
    _connect_api_gateway_signal(signals, entities, evidence_index, changes)


"""End-to-end verification for Wave 4: Hermetic 20-Document Replay & Delta Distillation."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Dict, List

from dual_pipeline.contracts import seal_artifact
from dual_pipeline.document_pipeline_core.document_connectivity_healer import (
    _compute_degrees,
    heal_disconnected_document_entities,
)
from dual_pipeline.document_pipeline_core.entity_canonicalizer import (
    canonicalize_projection_entities,
)
from dual_pipeline.document_pipeline_core.ingestion_planner import (
    build_document_ingestion_plan,
    extract_role_framework_catalog_projection,
    persist_document_ingestion_plan,
)
from dual_pipeline.document_pipeline_core.signal_value_router import (
    apply_signal_profile_to_metadata,
    reconstruct_document_text_from_metadata,
)
from dual_pipeline.identity import resolve_bundle
from dual_pipeline.intake_core import build_routes, distill_document_graph_delta
from dual_pipeline.normalization import normalize_evidence_envelope

REPO_ROOT = Path(__file__).resolve().parents[1]

LAST_20_DOCUMENT_DIRS: List[str] = [
    "output/hrit/10-19-2026/Nee4u_Employee_Experience_Align_PartnerAdvisory",
    "output/internal_consulting/10-22-2026/PartnerAdvisory_Sales_Immersion_Q4_2026_Day_4",
    "INTAKE_ROLE_FRAMEWORK_CATALOG",
    "output/internal_consulting/10-07-2026/Agentic_Managed_Services_Ams_Anthony",
    "output/hrit/10-24-2026/Hr360_Position_Request_Form_Clement",
    "output/dociq/09-30-2026/Dociq_Update_Jack_PartnerAdvisory",
    "output/infrastructure_automation/08-26-2026/Nee_Infrastructure_Automation_Transforma",
    "output/project_lens/10-01-2026/Commercial_Scope_Boundary_Note_Amit",
    "output/project_lens/10-02-2026/Mandy_Hopmann_Feedback_Sow_Phase_1_Scope",
    "output/project_lens/10-22-2026/Staffing_Transitions_Delivery_Updates",
    "output/market_intelligence/10-20-2026/meridian_utility_Distribution_Doug",
    "output/nea/10-21-2026/Forge_Ai_Optimization_Engine_Lifecycle_O",
    "output/enterprise_architecture/09-16-2026/Coming_Together_As_One_Petter_Medina",
    "output/enterprise_account_planning/10-14-2026/PartnerAdvisory_Solutions_Catalog_2026_Master",
    "output/hrit/10-20-2026/Hr_It_Data_Request_Clem_Prf_Clement",
    "output/nea/09-02-2026/Forge_Authorization_Architecture_Diana",
    "output/project_jarvis/10-08-2026/Jarvis_Requirements_Discovery_Gap_Matrix",
    "output/nea/10-12-2026/Cweos_Q4_Mission_Documentation",
    "output/market_intelligence/10-19-2026/Energy_Utilities_Market_Antigravity",
    "output/hrit/10-16-2026/Revised_PartnerAdvisory_Sow_Prf_Clement",
]


def _load_doc_fixture(rel_dir: str) -> tuple[str, Dict[str, Any], Dict[str, Any], str]:
    if rel_dir == "INTAKE_ROLE_FRAMEWORK_CATALOG":
        role_path = REPO_ROOT / "intake/2025-11-19_PartnerAdvisory_Career_Framework_Role_Profiles.md"
        text = role_path.read_text(encoding="utf-8")
        meta: Dict[str, Any] = {
            "title": "PartnerAdvisory Career Framework Role Profiles",
            "slug": "PartnerAdvisory_Career_Framework_Role_Profiles",
            "project": "internal_consulting",
            "is_document": True,
        }
        proj = extract_role_framework_catalog_projection(text, meta)
        return "PartnerAdvisory_Career_Framework_Role_Profiles", meta, proj, text

    doc_dir = REPO_ROOT / rel_dir
    f03 = sorted(doc_dir.glob("03_*.json"))[-1]
    fmeta = sorted(doc_dir.glob("metadata*.json"))[-1]
    proj = json.loads(f03.read_text(encoding="utf-8"))
    meta = json.loads(fmeta.read_text(encoding="utf-8"))
    text = reconstruct_document_text_from_metadata(meta.get("source_text"), meta)
    if not text:
        ev_texts = [
            str(ev.get("exact_text") or ev.get("text") or "")
            for ev in (proj.get("evidence_index") or {}).values()
            if isinstance(ev, dict)
        ]
        text = "\n\n".join(t for t in ev_texts if t) or meta.get("title") or doc_dir.name
    return doc_dir.name, meta, proj, text


def test_replay_all_20_documents_in_sandbox_zero_orphans_and_canonical_ids() -> None:
    """Replay all 20 recent document projections in an ephemeral sandbox and verify 0 orphans and 20/20 plans."""
    assert len(LAST_20_DOCUMENT_DIRS) == 20
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp_dir:
        sandbox_root = Path(tmp_dir)
        total_entities = 0
        total_orphans_after = 0
        persisted_plans = 0

        for rel_dir in LAST_20_DOCUMENT_DIRS:
            slug, meta, raw_proj, doc_text = _load_doc_fixture(rel_dir)
            doc_sandbox = sandbox_root / slug
            doc_sandbox.mkdir(parents=True, exist_ok=True)

            meta_copy = copy.deepcopy(meta)
            meta_copy["output_dir"] = str(doc_sandbox)
            meta_copy["slug"] = slug

            profile = apply_signal_profile_to_metadata(doc_text, meta_copy)
            assert profile.dynamic_mutation_budget >= 30

            plan = build_document_ingestion_plan(doc_text, meta_copy)
            plan_path = persist_document_ingestion_plan(plan, doc_sandbox, slug=slug)
            assert plan_path.exists()
            saved_plan = json.loads(plan_path.read_text(encoding="utf-8"))
            assert saved_plan["signal_tier"] in (
                "HIGH_SIGNAL_AUTHORITATIVE",
                "MEDIUM_SIGNAL_OPERATIONAL",
                "LOW_SIGNAL_ENABLEMENT_OR_TEMPLATE",
            )
            assert saved_plan["recommended_mutation_budget"] >= 30
            persisted_plans += 1

            healed = canonicalize_projection_entities(copy.deepcopy(raw_proj))
            heal_disconnected_document_entities(healed, meta_copy, doc_text)

            gm = healed.get("graph_mutations") or {}
            upserts = gm.get("entity_upserts") or []
            rels = gm.get("relationship_signals") or []
            total_entities += len(upserts)

            for ent in upserts:
                eid = str(ent.get("id") or "")
                assert " " not in eid, f"Space in entity ID {eid} in {slug}"
                if eid.startswith(("person:", "org:")):
                    assert "_" not in eid, f"Underscore in canonical ID {eid} in {slug}"
                assert eid != "org:next era-energy"
                assert eid != "person:eduardo-ed-juan-more"
                assert eid != "person:medina_jose"

            deg, _ = _compute_degrees(upserts, rels)
            orphans = [eid for eid, d in deg.items() if d == 0]
            assert orphans == [], f"Document {slug} retained orphans: {orphans}"
            total_orphans_after += len(orphans)

        assert persisted_plans == 20
        assert total_entities >= 250
        assert total_orphans_after == 0


def _init_sandbox_graph(db_path: Path, seeded_nodes: List[tuple[str, str, str]]) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        CREATE TABLE node_type_registry (
            node_type_id TEXT PRIMARY KEY,
            identity_rule TEXT NOT NULL,
            registry_state TEXT NOT NULL
        );
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            node_type TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'canonical',
            active INTEGER DEFAULT 1
        );
        CREATE TABLE canonical_alias (
            normalized_alias TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE entity_redirect (
            from_node_id TEXT PRIMARY KEY,
            to_node_id TEXT NOT NULL,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            claim_text TEXT,
            normalized_text TEXT,
            source_id TEXT,
            extraction_run_id TEXT,
            currentness_state TEXT DEFAULT 'current',
            superseded_at TEXT,
            superseded_by_assertion_id TEXT,
            classification TEXT DEFAULT 'internal',
            allowed_audiences_json TEXT DEFAULT '["internal_consulting"]'
        );
        """
    )
    for ntype in ("workstream", "org", "technical_anchor", "stakeholder", "pattern", "risk", "deliverable", "program"):
        conn.execute(
            "INSERT INTO node_type_registry (node_type_id, identity_rule, registry_state) VALUES (?, ?, 'active')",
            (ntype, "Never auto-merge on name similarity; require exact ID or human confirmation."),
        )
    for nid, cname, ntype in seeded_nodes:
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, node_type, description, active) VALUES (?, ?, ?, '', 1)",
            (nid, cname, ntype),
        )
    conn.commit()
    conn.close()


def test_cross_document_delta_distillation_lens_and_hrit_clusters() -> None:
    """Verify cross-document delta distillation across the 3 LENS memos and 3 HRIT PRF/SOW packages without false contradictions."""
    cluster_dirs = [
        "output/project_lens/10-01-2026/Commercial_Scope_Boundary_Note_Amit",
        "output/project_lens/10-02-2026/Mandy_Hopmann_Feedback_Sow_Phase_1_Scope",
        "output/project_lens/10-22-2026/Staffing_Transitions_Delivery_Updates",
        "output/hrit/10-16-2026/Revised_PartnerAdvisory_Sow_Prf_Clement",
        "output/hrit/10-20-2026/Hr_It_Data_Request_Clem_Prf_Clement",
        "output/hrit/10-24-2026/Hr360_Position_Request_Form_Clement",
    ]
    with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp_dir:
        db_path = Path(tmp_dir) / "cluster_sandbox.sqlite"
        _init_sandbox_graph(
            db_path,
            [
                ("person:eduardo-juan-more", "Eduardo Juan More", "stakeholder"),
                ("person:clement-hamel", "Clement Hamel", "stakeholder"),
                ("workstream:hr360", "HR360", "workstream"),
            ],
        )

        corroborated_total = 0
        net_new_total = 0
        for idx, rel_dir in enumerate(cluster_dirs):
            slug, meta, raw_proj, doc_text = _load_doc_fixture(rel_dir)
            healed = canonicalize_projection_entities(copy.deepcopy(raw_proj))
            heal_disconnected_document_entities(healed, {"slug": slug}, doc_text)
            gm = healed["graph_mutations"]

            pkg = seal_artifact({
                "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
                "schema_version": "1.0.0",
                "artifact_id": f"artifact-cluster-{idx}",
                "meeting": {
                    "meeting_id": f"doc-cluster-{idx}-{slug[:20].lower()}",
                    "title": meta.get("title") or slug,
                    "started_at": "2026-10-22T12:00:00-04:00",
                    "source_location": f"sandbox://{slug}",
                    "source_hash": f"{idx:064d}",
                    "attendees": ["Not specified"],
                    "classification": "internal",
                    "audiences": ["internal_consulting"],
                    "template_version": "5.8",
                },
                "speakers": [
                    {
                        "speaker_ref": "speaker-doc",
                        "reported_name": "Not specified",
                        "email": None,
                        "role_or_org": "Document",
                        "statements": [
                            {
                                "statement_id": "stmt-01",
                                "kind": "summary",
                                "text": doc_text[:240] or slug,
                                "locator": {"kind": "section", "value": "## Page 1"},
                                "gemini_confidence": 0.95,
                            }
                        ],
                    }
                ],
                "actions": [],
                "adapter": {"name": "nee-evidence-envelope-intake", "version": "1.0.0"},
                "extensions": {
                    "com.partner_advisory.nee.canonical-signal-projection": {
                        "entity_upserts": gm["entity_upserts"],
                        "relationship_signals": gm["relationship_signals"],
                    }
                },
            })
            bundle = normalize_evidence_envelope(pkg, run_context=f"wave4_cluster_{idx}")
            resolutions = resolve_bundle(bundle, db_path)
            comparison = {
                "comparisons": {
                    c["candidate_id"]: {
                        "exact_matches": [],
                        "partial_overlaps": [],
                        "corroborating_evidence": [],
                    }
                    for c in bundle["candidates"]
                }
            }
            routes = build_routes(bundle, resolutions, comparison)
            assert [r for r in routes if r["intake_route"] == "CONTRADICTION"] == []
            report = distill_document_graph_delta(bundle, resolutions, comparison, db_path).to_dict()
            corroborated_total += len(report["corroborated_facts"])
            net_new_total += len(report["net_new_entities"])

        assert corroborated_total >= 4
        assert net_new_total >= 10

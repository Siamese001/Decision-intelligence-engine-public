"""
End-to-end tests for Wave 3: Graph-Aware Delta Distillation, Perspective Gap Analyzer & Targeted HITL Batching.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.contracts import seal_artifact
from dual_pipeline.document_pipeline_core.tabular_roster_extractor import (
    reconcile_tabular_workstream_mutations,
)
from dual_pipeline.identity import resolve_bundle
from dual_pipeline.intake_core import (
    build_routes,
    distill_document_graph_delta,
)
from dual_pipeline.normalization import normalize_evidence_envelope
from tests.test_nea_q4_planning_roster_e2e import _build_90_workstream_nea_fixture


def _seed_partner_advisory_meeting_graph(db_path: Path) -> None:
    """Seed an isolated SQLite graph with 10 existing PartnerAdvisory-known workstreams, Jarvis tool, and prior owner assertions."""
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
    for ntype in (
        "workstream", "org", "technical_anchor", "stakeholder", "pattern", "risk", "deliverable", "program"
    ):
        conn.execute(
            "INSERT INTO node_type_registry (node_type_id, identity_rule, registry_state) VALUES (?, ?, 'active')",
            (ntype, "Never auto-merge on name similarity; require exact ID or human confirmation."),
        )

    # Seed 10 existing PartnerAdvisory-known workstreams matching workstreams in our 90-workstream fixture
    existing_workstreams = [
        ("workstream:5-forge-customer", "5.Forge.Customer"),
        ("workstream:beacon", "Beacon"),
        ("workstream:swarm-validation-and-pilot", "SWARM - Validation and Pilot"),
        ("workstream:initiative-charter-2", "Initiative Charter 2"),
        ("workstream:initiative-charter-3", "Initiative Charter 3"),
        ("workstream:initiative-charter-4", "Initiative Charter 4"),
        ("workstream:initiative-charter-5", "Initiative Charter 5"),
        ("workstream:initiative-charter-7", "Initiative Charter 7"),
        ("workstream:initiative-charter-8", "Initiative Charter 8"),
        ("workstream:initiative-charter-9", "Initiative Charter 9"),
    ]
    for wid, wname in existing_workstreams:
        conn.execute(
            "INSERT INTO node (node_id, canonical_name, node_type, description, active) VALUES (?, ?, 'workstream', ?, 1)",
            (wid, wname, f"Existing PartnerAdvisory graph workstream {wname}"),
        )

    # Seed existing technical_anchor 'Jarvis', stakeholder 'Prior PartnerAdvisory Lead', and placeholder 'unresolved_jake_wellington_roster_2026_07_11'
    conn.execute(
        "INSERT INTO node (node_id, canonical_name, node_type, description, active) VALUES (?, ?, 'technical_anchor', ?, 1)",
        ("entity-487c43902705f2a28c2fc9d05687", "Jarvis", "Internal AI knowledge assistant tool."),
    )
    conn.execute(
        "INSERT INTO node (node_id, canonical_name, node_type, description, active) VALUES (?, ?, 'stakeholder', ?, 1)",
        ("person:prior-partner_advisory-lead", "Prior PartnerAdvisory Lead", "Prior workstream owner from July meeting."),
    )
    conn.execute(
        "INSERT INTO node (node_id, canonical_name, node_type, description, active) VALUES (?, ?, 'stakeholder', ?, 1)",
        ("unresolved_jake_wellington_roster_2026_07_11", "unresolved_jake_wellington_roster_2026_07_11", "Unresolved placeholder from July roster."),
    )

    # Seed prior PartnerAdvisory meeting assertion claiming 'Prior PartnerAdvisory Lead' leads '5.Forge.Customer'
    conn.execute(
        """INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, claim_text, normalized_text, source_id, currentness_state)
           VALUES ('assertion-prior-lead-01', 'person:prior-partner_advisory-lead', 'OWNS_OR_LEADS', 'workstream:5-forge-customer',
                   'Prior PartnerAdvisory Lead leads 5.Forge.Customer.', 'Prior PartnerAdvisory Lead OWNS_OR_LEADS 5.Forge.Customer.',
                   'source-prior-partner_advisory-meeting', 'current')"""
    )
    conn.commit()
    conn.close()


def test_distill_document_graph_delta_full_90_workstreams_e2e() -> None:
    """Verify distill_document_graph_delta partitions 80 net-new NEA workstreams vs 10 corroborated workstreams, detects owner shifts, and reconciles placeholders."""
    doc_text = _build_90_workstream_nea_fixture()
    stage03_data: Dict[str, Any] = {}
    reconcile_tabular_workstream_mutations(stage03_data, {"is_document": True}, doc_text)
    mutations = stage03_data["graph_mutations"]

    with tempfile.TemporaryDirectory(prefix="wave3_delta_e2e_") as tmp_dir:
        graph_path = Path(tmp_dir) / "sandbox_graph.sqlite"
        _seed_partner_advisory_meeting_graph(graph_path)

        package = seal_artifact({
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "artifact-nea-q4-delta-e2e",
            "meeting": {
                "meeting_id": "document-nea-q4-planning-2026-10-20",
                "title": "Document Team Assignments - NEA Planning",
                "started_at": "2026-10-20T17:21:00-04:00",
                "source_location": "https://planning.apex_globalanalytics.com/quarterly-planning/assignments?quarterId=3",
                "source_hash": "b" * 64,
                "attendees": ["Not specified"],
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "template_version": "5.8",
            },
            "speakers": [
                {
                    "speaker_ref": "speaker-not-specified",
                    "reported_name": "Not specified",
                    "email": None,
                    "role_or_org": "Document Table Row",
                    "statements": [
                        {
                            "statement_id": "stmt-summary-01",
                            "kind": "summary",
                            "text": "NEA Q4 2026 Team Assignments across 90 workstreams.",
                            "locator": {"kind": "section", "value": "## Page 1"},
                            "gemini_confidence": 0.92,
                        }
                    ],
                }
            ],
            "actions": [],
            "adapter": {"name": "nee-evidence-envelope-intake", "version": "1.0.0"},
            "extensions": {
                "com.partner_advisory.nee.canonical-signal-projection": {
                    "entity_upserts": mutations["entity_upserts"],
                    "relationship_signals": mutations["relationship_signals"],
                }
            },
        })

        bundle = normalize_evidence_envelope(package, run_context="wave3_e2e")
        resolutions = resolve_bundle(bundle, graph_path)

        # Simulate partial_overlaps on multi-row portfolio relationships to verify 0 false-positive contradictions
        comparison = {
            "comparisons": {
                c["candidate_id"]: {
                    "exact_matches": [],
                    "partial_overlaps": (
                        [{"record_id": "sd-1", "object_id": "obj-1", "lane": "candidate"}]
                        if c["candidate_type"] == "architecture_relationship"
                        else []
                    ),
                    "corroborating_evidence": [],
                }
                for c in bundle["candidates"]
            }
        }

        routes = build_routes(bundle, resolutions, comparison)
        contradiction_routes = [r for r in routes if r["intake_route"] == "CONTRADICTION"]
        assert len(contradiction_routes) == 0

        report = distill_document_graph_delta(bundle, resolutions, comparison, graph_path)
        report_dict = report.to_dict()

        assert report_dict["perspective_origin"] == "CLIENT_INTERNAL_NEA"
        assert len(report_dict["corroborated_workstreams"]) == 10
        assert len(report_dict["net_new_workstreams"]) == 80
        assert "5.Forge.Customer" in report_dict["corroborated_workstreams"]
        assert "NEA JARVIS" in report_dict["net_new_workstreams"]

        # Verify perspective owner shift detection (Prior PartnerAdvisory Lead vs Andrew Turvey on 5.Forge.Customer)
        assert len(report_dict["perspective_deltas"]) >= 1
        forge_delta = next(
            d for d in report_dict["perspective_deltas"]
            if d["workstream_or_target"] == "5.Forge.Customer"
        )
        assert forge_delta["prior_partner_advisory_owner"] == "Prior PartnerAdvisory Lead"
        assert forge_delta["new_client_owner"] == "Andrew Turvey"

        # Verify placeholder identity reconciliation for Jake Wellington
        assert len(report_dict["placeholder_reconciliations"]) >= 1
        jake_ph = report_dict["placeholder_reconciliations"][0]
        assert jake_ph["placeholder_node_id"] == "unresolved_jake_wellington_roster_2026_07_11"
        assert jake_ph["resolved_canonical_name"] == "Jake Wellington"

        # Verify HITL review items are generated with margin <= 0.20
        assert len(report_dict["hitl_review_items"]) >= 2
        for item in report_dict["hitl_review_items"]:
            assert item["confidence_margin"] <= 0.20
            assert item["requires_operator_decision"] is True

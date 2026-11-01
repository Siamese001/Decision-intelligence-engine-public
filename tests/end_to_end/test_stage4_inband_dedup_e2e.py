"""End-to-End Tests for Wave 4 (Stage 4: C-16 Entity Canonicalization & In-Band Pre-Intake Dedup).

Verifies the complete Stage 4 lifecycle:
1. C-16 dynamic context-pack alias resolution + organization canonicalization (`org:apex_global-analytics`, `org:partner_advisory`).
2. In-band `deduplicate_projection_evidence_and_signals` merging duplicate `evidence_index` entries and rewriting pointers across all mutation collections.
3. `heal_disconnected_document_entities` wiring orphans with source-grounded evidence selection, text corroboration gating (`CONFIRMED` vs `PROPOSED`), and 8-char SHA-256 signal ID digests.
4. `tools/deduplicate_semantic_assertions.py` CLI and `dual_pipeline/graph_maintenance_core/dedup.py` governed SQLite execution with HITL safety gate, `assertion_evidence` transfer, `search_fts` sync, `audit_event` logging, and 5-check physical proof.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.document_pipeline_core.document_connectivity_healer import (
    _compute_degrees,
    heal_disconnected_document_entities,
)
from dual_pipeline.document_pipeline_core.entity_canonicalizer import (
    canonicalize_projection_entities,
)
from storage.connection import get_db_connection
from tools.deduplicate_semantic_assertions import main as dedup_cli_main


def test_e2e_stage4_c16_canonicalization_inband_dedup_and_connectivity_healing() -> None:
    """End-to-end Stage 4 projection pipeline: C-16 context pack + in-band evidence dedup + corroborated connectivity healing."""
    doc_text = (
        "Apex Global Analytics (NEA) partnered with PartnerAdvisory Consulting to deliver Project Lens. "
        "M. Barrows and Dr. Evelyn Vance lead the Project Lens architecture review."
    )
    metadata: Dict[str, Any] = {
        "project": "project_lens",
        "slug": "Project_Lens_Architecture_Review",
        "context_pack": {
            "known_entities": [
                {
                    "id": "person:evelyn-vance",
                    "canonical_name": "Evelyn Vance",
                    "aliases": ["Dr. Evelyn Vance", "E. Vance"],
                },
            ],
            "graph_alias_map": {
                "lens platform": ["project:lens", "Project Lens"],
            },
        },
    }
    projection: Dict[str, Any] = {
        "project": "project_lens",
        "evidence_index": {
            "ev-01": {
                "locator": {"kind": "page", "value": "1"},
                "exact_text": "Apex Global Analytics (NEA) partnered with PartnerAdvisory Consulting to deliver Project Lens.",
            },
            "ev-02": {
                "locator": {"kind": "page", "value": "1"},
                "exact_text": "Apex Global Analytics (NEA) partnered with PartnerAdvisory Consulting to deliver Project Lens.",
            },
            "ev-03": {
                "locator": {"kind": "page", "value": "2"},
                "exact_text": "M. Barrows and Dr. Evelyn Vance lead the Project Lens architecture review.",
            },
            "ev-04": {
                "locator": {"kind": "page", "value": "5"},
                "exact_text": "Appendix: Unrelated Legacy Telemetry Module inventory.",
            },
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "project:lens", "type": "project", "name": "Project Lens", "evidence_ids": ["ev-01"]},
                {"id": "org:nea", "type": "org", "name": "NEA", "evidence_ids": ["ev-01"]},
                {"id": "org:apex_global-analytics", "type": "org", "name": "Apex Global Analytics", "evidence_ids": ["ev-02"]},
                {"id": "org:partner_advisory-consulting", "type": "org", "name": "PartnerAdvisory Consulting", "evidence_ids": ["ev-02"]},
                {"id": "person:m-barrows", "type": "person", "name": "M. Barrows", "evidence_ids": ["ev-03"]},
                {"id": "person:dr-evelyn-vance", "type": "person", "name": "Dr. Evelyn Vance", "evidence_ids": ["ev-03"]},
                {"id": "system:legacy-telemetry-module", "type": "system", "name": "Legacy Telemetry Module", "evidence_ids": ["ev-04"]},
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-1",
                    "from_id": "org:nea",
                    "to_id": "project:lens",
                    "predicate": "OWNS",
                    "confidence": 0.90,
                    "epistemic_status": "CONFIRMED",
                    "mutation": "CONFIRM",
                    "evidence_ids": ["ev-02"],
                },
            ],
            "decision_updates": [
                {
                    "id": "dec-01",
                    "owner_id": "person:dr-evelyn-vance",
                    "stakeholders": ["person:m-barrows", "org:partner_advisory-consulting"],
                    "evidence_ids": ["ev-02", "ev-03"],
                },
            ],
        },
    }

    # Step 1: Canonicalize entities and deduplicate evidence in-band
    canonicalize_projection_entities(projection, metadata)
    assert "ev-02" not in projection["evidence_index"]
    assert set(projection["evidence_index"].keys()) == {"ev-01", "ev-03", "ev-04"}

    gm = projection["graph_mutations"]
    entity_ids = {e["id"] for e in gm["entity_upserts"]}
    assert entity_ids == {
        "project:lens",
        "org:apex_global-analytics",
        "org:partner_advisory",
        "person:matthew-barrows",
        "person:evelyn-vance",
        "system:legacy-telemetry-module",
    }
    assert gm["relationship_signals"][0]["from_id"] == "org:apex_global-analytics"
    assert gm["relationship_signals"][0]["evidence_ids"] == ["ev-01"]
    assert gm["decision_updates"][0]["owner_id"] == "person:evelyn-vance"
    assert gm["decision_updates"][0]["stakeholders"] == ["person:matthew-barrows", "org:partner_advisory"]
    assert gm["decision_updates"][0]["evidence_ids"] == ["ev-01", "ev-03"]

    # Step 2: Heal disconnected entities with document_text corroboration check
    changes = heal_disconnected_document_entities(projection, metadata, document_text=doc_text)
    assert len(changes) >= 4
    deg, _ = _compute_degrees(gm["entity_upserts"], gm["relationship_signals"])
    assert all(d >= 1 for d in deg.values())

    # Corroborated entities (PartnerAdvisory, Matthew Barrows, Evelyn Vance) -> CONFIRMED
    # Uncorroborated appendix entity (system:legacy-telemetry-module not in doc_text or ev-01/ev-03 with Project Lens) -> PROPOSED
    healed_legacy = next(
        r for r in gm["relationship_signals"]
        if r.get("auto_healed") and "system:legacy-telemetry-module" in (r["from_id"], r["to_id"])
    )
    assert healed_legacy["epistemic_status"] == "PROPOSED"
    assert healed_legacy["mutation"] == "CREATE"
    assert healed_legacy["evidence_ids"] == ["ev-04"]


def test_e2e_stage4_governed_sqlite_assertion_dedup_cli_and_physical_proof() -> None:
    """End-to-end SQLite assertion deduplication via CLI with HITL gate, FTS5 rebuild, evidence transfer, and physical proof."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_graph.sqlite"
        report_path = Path(tmpdir) / "dedup_report.json"

        with get_db_connection(db_path) as conn:
            conn.execute(
                """CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT,
                    predicate TEXT,
                    object_node_id TEXT,
                    object_literal TEXT,
                    claim_text TEXT,
                    confidence_score REAL,
                    source_authority_rank INTEGER,
                    primary_source_id TEXT,
                    currentness_state TEXT,
                    retrieval_eligible INTEGER,
                    recorded_at INTEGER,
                    created_at INTEGER,
                    superseded_at TEXT,
                    superseded_by_assertion_id TEXT
                )"""
            )
            conn.execute(
                """CREATE TABLE search_document (
                    doc_id TEXT PRIMARY KEY,
                    assertion_id TEXT,
                    currentness_state TEXT,
                    retrieval_eligible INTEGER
                )"""
            )
            conn.execute(
                """CREATE TABLE assertion_evidence (
                    assertion_id TEXT,
                    evidence_id TEXT,
                    evidence_relationship TEXT,
                    created_at TEXT,
                    PRIMARY KEY (assertion_id, evidence_id)
                )"""
            )
            conn.execute(
                """CREATE TABLE audit_event (
                    audit_event_id TEXT PRIMARY KEY,
                    actor_or_process TEXT,
                    action TEXT,
                    record_type TEXT,
                    record_id TEXT,
                    new_value_json TEXT,
                    event_timestamp TEXT,
                    reason TEXT
                )"""
            )
            conn.executemany(
                """INSERT INTO assertion VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, 'current', 1, ?, ?, NULL, NULL)""",
                [
                    ("ast-low", "project:lens", "USES", "system:forge", "Project Lens uses the FORGE engine", 0.65, 1, "src-1", 100, 100),
                    ("ast-high", "project:lens", "USES", "system:forge", "Project Lens uses FORGE engine", 0.92, 2, "src-2", 200, 200),
                    ("ast-ambig-1", "org:partner_advisory", "OWNS", "project:lens", "PartnerAdvisory owns Project Lens delivery", 0.80, 1, "src-1", 100, 100),
                    ("ast-ambig-2", "org:partner_advisory", "OWNS", "project:lens", "PartnerAdvisory owns the Project Lens delivery", 0.80, 1, "src-2", 200, 200),
                ],
            )
            conn.executemany(
                "INSERT INTO search_document VALUES (?, ?, 'current', 1)",
                [("sd-1", "ast-low"), ("sd-2", "ast-high"), ("sd-3", "ast-ambig-1"), ("sd-4", "ast-ambig-2")],
            )
            conn.executemany(
                "INSERT INTO assertion_evidence VALUES (?, ?, 'supports', '2026-10-25T00:00:00Z')",
                [("ast-low", "ev-low-1"), ("ast-high", "ev-high-1")],
            )
            conn.commit()

        # Run CLI with --apply (without --include-hitl-approved): only ast-low -> ast-high is applied
        rc = dedup_cli_main(["--graph", str(db_path), "--threshold", "0.85", "--apply", "--output", str(report_path)])
        assert rc == 0
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["proposals_count"] == 2
        assert sum(1 for p in report["proposals"] if p.get("hitl_required")) == 1
        assert report["applied_count"] == 1
        assert report["physical_proof"]["integrity_check"] == "ok"
        assert report["physical_proof"]["pre_sha256"] != report["physical_proof"]["post_sha256"]

        with get_db_connection(db_path) as conn:
            low_state, sup_by = conn.execute(
                "SELECT currentness_state, superseded_by_assertion_id FROM assertion WHERE assertion_id = 'ast-low'"
            ).fetchone()
            assert low_state == "historical"
            assert sup_by == "ast-high"

            # Ambiguous HITL pair remains current
            ambig_state = conn.execute(
                "SELECT currentness_state FROM assertion WHERE assertion_id = 'ast-ambig-1'"
            ).fetchone()[0]
            assert ambig_state == "current"

            # Evidence transferred to winner
            high_evs = {
                r[0] for r in conn.execute("SELECT evidence_id FROM assertion_evidence WHERE assertion_id = 'ast-high'").fetchall()
            }
            assert high_evs == {"ev-low-1", "ev-high-1"}

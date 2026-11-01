"""End-to-end tests for the 5-Agent Graph Hygiene & HITL Learning Hardening wave."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.graph_maintenance_core.hygiene_agent import _audit_promotable_candidates
from dual_pipeline.graph_maintenance_core.hygiene_mutation_agents import (
    normalize_merge_finding_survivors,
)
from dual_pipeline.graph_scout_core.matching import has_conflicting_qualifiers
from dual_pipeline.learning_engine_core.option_nuance import synthesize_hitl_option_nuance
from dual_pipeline.learning_engine_core.rollback import (
    snapshot_database_digests,
    verify_zero_graph_mutation,
)
from tests.end_to_end.test_graph_hygiene_uwg_role_e2e import _bootstrap_test_graph
from tools.apply_graph_hygiene_remediation import apply_hygiene_remediation


def test_wave1_staging_leak_mutual_exclusion_and_unpromotable_guard(tmp_path: Path) -> None:
    """Verify leaked (retrieval_eligible=1) and fact_promotion_eligible=false candidates are excluded from CLUSTER-06."""
    db_path = tmp_path / "graph.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            canonical_status TEXT,
            currentness_state TEXT,
            retrieval_eligible INTEGER,
            is_quarantined INTEGER,
            validation_state TEXT,
            confidence_score REAL,
            qualifiers_json TEXT
        )"""
    )
    conn.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT)")
    # 1. Leaked candidate (retrieval_eligible=1) -> must be excluded from CLUSTER-06
    conn.execute(
        "INSERT INTO assertion VALUES ('asrt-leaked', 'n1', 'P', 'candidate', 'current', 1, 0, 'needs_review', 0.95, '{}')"
    )
    # 2. Candidate with fact_promotion_eligible=false -> must be excluded from CLUSTER-06
    conn.execute(
        """INSERT INTO assertion VALUES (
            'asrt-uneligible', 'n1', 'P', 'candidate', 'current', 0, 0, 'needs_review', 0.95,
            '{"fact_promotion_eligible": false}'
        )"""
    )
    # 3. Claimed by CLUSTER-01B -> must be excluded when passed in excluded_ids
    conn.execute(
        "INSERT INTO assertion VALUES ('asrt-claimed', 'n1', 'P', 'candidate', 'current', 0, 0, 'needs_review', 0.95, '{}')"
    )
    # 4. Valid promotable candidate -> must be included
    conn.execute(
        "INSERT INTO assertion VALUES ('asrt-valid', 'n1', 'P', 'candidate', 'current', 0, 0, 'needs_review', 0.95, '{}')"
    )
    for aid in ("asrt-leaked", "asrt-uneligible", "asrt-claimed", "asrt-valid"):
        conn.execute("INSERT INTO assertion_evidence VALUES (?, 'ev-1')", (aid,))
    conn.commit()

    cur = conn.cursor()
    cols = {r[1] for r in cur.execute("PRAGMA table_info(assertion)").fetchall()}
    finding = _audit_promotable_candidates(cur, cols, excluded_ids={"asrt-claimed"})
    assert finding is not None
    assert finding.entity_references == ["asrt-valid"]
    assert finding.raw_data["promotable_ids"] == ["asrt-valid"]
    conn.close()


def test_wave1_transitive_survivor_resolution_and_role_scope() -> None:
    """Verify overlapping merge_entities pairs elect a single connected-component survivor and role scopes stay distinct."""
    findings = [
        {
            "proposed_fix": "merge_entities",
            "entity_references": ["entity-proto", "workstream_insights_hub"],
            "raw_data": {
                "node_id_a": "entity-proto",
                "node_id_b": "workstream_insights_hub",
                "canonical_name_a": "APEX_TRANS Insights Hub Prototype",
                "canonical_name_b": "APEX_TRANS Insights Hub",
            },
        },
        {
            "proposed_fix": "merge_entities",
            "entity_references": ["entity-short", "entity-proto"],
            "raw_data": {
                "node_id_a": "entity-short",
                "node_id_b": "entity-proto",
                "canonical_name_a": "Insights Hub",
                "canonical_name_b": "APEX_TRANS Insights Hub Prototype",
            },
        },
    ]
    normalize_merge_finding_survivors(None, findings)
    surv_0 = findings[0]["raw_data"]["survivor_node_id"]
    surv_1 = findings[1]["raw_data"]["survivor_node_id"]
    assert surv_0 == "workstream_insights_hub"
    assert surv_1 == "workstream_insights_hub"
    assert findings[0]["raw_data"]["retired_node_id"] != findings[1]["raw_data"]["retired_node_id"]

    # Role-scope separation (CLUSTER-02D2)
    assert has_conflicting_qualifiers("DevOps Engineer", "Cloud-DevOps Engineer") is True
    assert has_conflicting_qualifiers("Cloud Engineer", "Cloud-DevOps Engineer") is True
    assert has_conflicting_qualifiers("Business Lead", "Maximo Business Lead") is True
    assert has_conflicting_qualifiers("Project Development Specialist", "Learning & Development Specialist") is True


def test_wave1_learning_simulation_ignores_ephemeral_test_timings(tmp_path: Path) -> None:
    """Verify concurrent writes to test_timings.sqlite never trigger false-positive graph mutation errors."""
    gov_db = tmp_path / "graph.sqlite"
    timings_db = tmp_path / "test_timings.sqlite"
    gov_db.write_bytes(b"governed-v1")
    timings_db.write_bytes(b"timings-v1")

    before = snapshot_database_digests(tmp_path)
    assert str(timings_db) not in before
    assert str(gov_db) in before

    timings_db.write_bytes(b"timings-v2-modified-by-pytest")
    after = snapshot_database_digests(tmp_path)
    ok, mutations = verify_zero_graph_mutation(before, after)
    assert ok is True
    assert mutations == []

    # Synthesize option store and verify auto_apply=False governance metadata
    ledger_path = tmp_path / "hitl_decision_ledger.json"
    ledger_path.write_text(
        json.dumps(
            {
                "decisions": [
                    {
                        "decision_id": "d1",
                        "category": "hygiene_review",
                        "actor": "human_operator",
                        "selected_option": "Keep distinct",
                        "options": ["Keep distinct", "Merge"],
                        "outcome_type": "AFFIRMED",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    out_store = tmp_path / "learned_hitl_option_store.json"
    payload = synthesize_hitl_option_nuance(ledger_path, out_store)
    assert payload["auto_apply"] is False
    assert payload["approval_required"] is True


def test_wave2_e2e_remediation_with_preexisting_redirect_and_cross_project_quarantine(tmp_path: Path) -> None:
    """Verify full apply_hygiene_remediation passes strict_delta=True with pre-existing redirect and CLUSTER-07C quarantine."""
    db_path = tmp_path / "wave2_e2e.sqlite"
    _bootstrap_test_graph(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute("ALTER TABLE assertion ADD COLUMN qualifiers_json TEXT DEFAULT '{}'")
    # Insert 2 active nodes to merge where node-retired ALREADY has a stale entity_redirect row
    conn.execute(
        "INSERT OR REPLACE INTO node (node_id, node_type, canonical_name, description, status, active, created_at, updated_at) "
        "VALUES ('org_surv', 'organization', 'Eng and Tech', 'Dept', 'canonical', 1, '2026-10-01', '2026-10-01')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO node (node_id, node_type, canonical_name, description, status, active, created_at, updated_at) "
        "VALUES ('entity_ret', 'organization', 'Eng Tech', 'Dept', 'canonical', 1, '2026-10-01', '2026-10-01')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO entity_merge_event (merge_event_id, canonical_node_id, event_type, status, approval_reference, reason, created_at, completed_at) "
        "VALUES ('merge-stale-001', 'org_surv', 'merge', 'completed', 'HITL-OLD', 'Old merge', '2026-10-01', '2026-10-01')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO entity_redirect (from_node_id, to_node_id, merge_event_id, active, created_at) "
        "VALUES ('entity_ret', 'org_surv', 'merge-stale-001', 1, '2026-10-01')"
    )
    # Insert a cross-project bleed assertion on entity_ret so redirect healing attempts to touch it
    conn.execute(
        "INSERT OR REPLACE INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, scope_text, "
        "canonical_status, canonical_eligible, retrieval_eligible, currentness_state, validation_state, confidence_score, qualifiers_json) "
        "VALUES ('asrt-bleed-07c', 'entity_ret', 'USES', 'org_surv', 'Cross project bleed claim', "
        "'canonical', 1, 1, 'current', 'verified', 0.90, '{}')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO search_document (search_document_id, object_type, object_id, title, body, "
        "canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id, updated_at) "
        "VALUES ('asrt-bleed-07c', 'assertion', 'asrt-bleed-07c', 'Bleed', 'Bleed', 'canonical', 'current', 1, 'src-1', 'asrt-bleed-07c', '2026-10-01')"
    )
    conn.commit()
    conn.close()

    pkg_path = tmp_path / "pkg_wave2.json"
    pkg_path.write_text(
        json.dumps(
            {
                "findings": [
                    {
                        "proposed_fix": "merge_entities",
                        "entity_references": ["org_surv", "entity_ret"],
                        "raw_data": {
                            "node_id_a": "org_surv",
                            "node_id_b": "entity_ret",
                            "canonical_name_a": "Eng and Tech",
                            "canonical_name_b": "Eng Tech",
                        },
                    },
                    {
                        "proposed_fix": "quarantine_cross_project_assertion",
                        "entity_references": ["asrt-bleed-07c"],
                        "raw_data": {"assertion_id": "asrt-bleed-07c"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    receipt = apply_hygiene_remediation(graph_path=db_path, package_path=pkg_path, strict_delta=True)
    assert receipt["terminal_outcome"] == "PASS"
    assert receipt["itemized_hitl_contract"]["status"] == "PASS"
    assert receipt["itemized_hitl_contract"]["coverage_ratio"] == 1.0

    ro_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    row = ro_conn.execute(
        "SELECT canonical_status, retrieval_eligible, currentness_state, validation_state, qualifiers_json "
        "FROM assertion WHERE assertion_id = 'asrt-bleed-07c'"
    ).fetchone()
    assert row is not None
    assert row[1] == 0
    assert row[2] == "historical"
    assert row[3] == "rejected"
    assert "disjoint_workstream_exclusion" in row[4]
    ro_conn.close()


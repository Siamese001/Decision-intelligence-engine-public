"""Adversarial & Edge-Case Tests for Hygiene Boundary Exclusions and Generation Promotion.

Covers mandatory edge-case categories per `.agents/rules/e2e-and-edge-case-coverage.md`:
1. Boundary payload: Empty findings, empty `excluded_findings`, self-referential node pairs (`node_a == node_b`).
2. Malformed input: Non-dict items in `excluded_findings`, missing `raw_data` keys, reversed pair ordering (`(B, A)` vs `(A, B)`), duplicate exclusion entries.
3. Strict package gating: Omitting `batch_promote_candidates` and `resync_search_sidecars` with `strict_package_gating=True` leaves candidate assertions and search documents untouched.
4. Rollback & idempotency recovery: Re-running exclusion recording and `GraphHygienePanel` across multiple cycles is idempotent and never mutates excluded distinct nodes.
5. Exact production regression: Pins the `10-24-2026` repeated-findings failure scenario (`CLUSTER-02D2-ROLE-PROJECT-SCOPE` and `CLUSTER-02C2-PROPOSAL-LIFECYCLE-BOUNDARY` resurfacing on consecutive runs + `auto_link_edge` and `archive_node` ignored).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from dual_pipeline.graph_maintenance_core.hygiene_agent import GraphHygienePanel
from dual_pipeline.graph_maintenance_core.hygiene_council import (
    load_active_boundary_exclusions,
)
from tools.apply_graph_hygiene_remediation import apply_hygiene_remediation
from tests.test_hygiene_generation_promotion_e2e import _init_minimal_runtime_pair


def test_adversarial_malformed_and_boundary_exclusions(tmp_path: Path) -> None:
    """Category 1 & 2 (Boundary & Malformed Input): Handles empty, self-pair, reversed, and malformed exclusion entries gracefully."""
    runtime_root = tmp_path / "runtime"
    graph_path, _, _ = _init_minimal_runtime_pair(runtime_root)

    pkg_path = tmp_path / "malformed_pkg.json"
    pkg_path.write_text(
        json.dumps(
            {
                "status": "approved",
                "strict_package_gating": True,
                "findings": [],
                "excluded_findings": [
                    None,
                    "invalid_string_entry",
                    {},
                    {"entity_references": ["only_one_node"]},
                    {"entity_references": ["role_generic_lead", "role_generic_lead"]},  # self-pair
                    {
                        "entity_references": ["role_atlas_lead", "role_generic_lead"],
                        "exclusion_reason": "First valid entry (reversed order)",
                    },
                    {
                        "raw_data": {
                            "node_id_a": "role_generic_lead",
                            "node_id_b": "role_atlas_lead",
                        },
                        "exclusion_reason": "Duplicate of same pair in forward order",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    receipt = apply_hygiene_remediation(
        package_path=pkg_path,
        promote_generation=True,
        runtime_root=runtime_root,
    )
    assert receipt["terminal_outcome"] == "PASS"
    # Deduplicated to exactly 1 valid canonical pair (role_atlas_lead::role_generic_lead)
    assert receipt["remediation_stats"]["boundary_exclusions_recorded"] == 1
    assert receipt["remediation_stats"]["candidates_promoted"] == 0
    assert receipt["remediation_stats"]["search_documents_resynced"] == 0

    promoted_graph = runtime_root / receipt["promoted_generation"]["graph_path"]
    with sqlite3.connect(f"file:{promoted_graph}?mode=ro", uri=True) as conn:
        ex_set = load_active_boundary_exclusions(conn)
        assert ("role_atlas_lead", "role_generic_lead") in ex_set
        assert len(ex_set) == 1


def test_strict_package_gating_prevents_unapproved_promotions(tmp_path: Path) -> None:
    """Category 5 (Invariant Bypass Attempt): Unapproved Cluster 6 & Cluster 7 do not run when strict_package_gating=True."""
    runtime_root = tmp_path / "runtime"
    graph_path, _, _ = _init_minimal_runtime_pair(runtime_root)

    with sqlite3.connect(str(graph_path)) as conn:
        conn.execute(
            """INSERT INTO assertion (
                assertion_id, subject_node_id, predicate, object_node_id, scope_text,
                canonical_status, canonical_eligible, retrieval_eligible, currentness_state,
                validation_state, confidence_score
            ) VALUES (
                'assertion-high-conf-cand', 'stakeholder_jose_medina', 'HAS_ROLE', 'role_generic_lead',
                'Jose Medina has role Technical Lead.', 'candidate', 0, 0, 'current', 'needs_review', 0.95
            )"""
        )
        conn.execute(
            "INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, created_at) VALUES ('assertion-high-conf-cand', 'ev-1', 'supports', '2026-10-01T00:00:00Z')"
        )
        conn.commit()

    pkg_path = tmp_path / "only_edge_link.json"
    pkg_path.write_text(
        json.dumps(
            {
                "status": "approved",
                "strict_package_gating": True,
                "findings": [
                    {
                        "detective": "StructuralValidator",
                        "severity": "MEDIUM",
                        "description": "Unmapped edge",
                        "entity_references": ["edge-jose-medina-affiliated-nea"],
                        "proposed_fix": "auto_link_edge",
                        "raw_data": {
                            "edge_id": "edge-jose-medina-affiliated-nea",
                            "matching_assertion_id": "assertion-jose-affiliated-nea",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    receipt = apply_hygiene_remediation(
        package_path=pkg_path,
        promote_generation=True,
        runtime_root=runtime_root,
    )
    assert receipt["terminal_outcome"] == "PASS"
    assert receipt["remediation_stats"]["edges_auto_linked"] == 1
    assert receipt["remediation_stats"]["candidates_promoted"] == 0
    assert receipt["remediation_stats"]["search_documents_resynced"] == 0

    promoted_graph = runtime_root / receipt["promoted_generation"]["graph_path"]
    with sqlite3.connect(f"file:{promoted_graph}?mode=ro", uri=True) as conn:
        status = conn.execute(
            "SELECT canonical_status FROM assertion WHERE assertion_id='assertion-high-conf-cand'"
        ).fetchone()[0]
        assert status == "candidate"


def test_production_regression_10_24_2026_no_repeat_across_runs(tmp_path: Path) -> None:
    """Category 6 & 7 (Idempotent Re-ingestion & Exact Production Regression 10-24-2026):
    Verifies that CLUSTER-02C2 (proposal lifecycle boundary), CLUSTER-02D2 (project-scoped role),
    CLUSTER-03 (auto_link_edge), and CLUSTER-08 (archive_node) do NOT repeat on Run 2 after Run 1 promotion.
    """
    runtime_root = tmp_path / "runtime"
    graph_path, _, _ = _init_minimal_runtime_pair(runtime_root)

    # Seed a CLUSTER-02C2 proposal boundary pair
    with sqlite3.connect(str(graph_path)) as conn:
        conn.executemany(
            "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, status, active, created_at, updated_at) VALUES (?, 'workstream', ?, ?, ?, 'active', 1, '2026-08-01T00:00:00Z', '2026-10-01T00:00:00Z')",
            [
                ("ws_infra_auto", "Infrastructure Automation", "Infrastructure Automation", "Active automation program"),
                ("ws_infra_auto_prop", "Infrastructure Automation Proposal", "Infrastructure Automation Proposal", "Pre-award pursuit proposal"),
            ],
        )
        conn.execute(
            """INSERT INTO assertion (
                assertion_id, subject_node_id, predicate, object_node_id, scope_text,
                canonical_status, canonical_eligible, retrieval_eligible, currentness_state, validation_state
            ) VALUES (
                'asrt-ws-1', 'ws_infra_auto', 'AFFILIATED_WITH', 'org_nea',
                'Infrastructure Automation at NEA.', 'canonical', 1, 1, 'current', 'accepted_legacy'
            )"""
        )
        conn.execute("INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, created_at) VALUES ('asrt-ws-1', 'ev-1', 'supports', '2026-10-01T00:00:00Z')")
        conn.execute(
            """INSERT INTO assertion (
                assertion_id, subject_node_id, predicate, object_node_id, scope_text,
                canonical_status, canonical_eligible, retrieval_eligible, currentness_state, validation_state
            ) VALUES (
                'asrt-ws-2', 'ws_infra_auto_prop', 'AFFILIATED_WITH', 'org_nea',
                'Infrastructure Automation Proposal at NEA.', 'canonical', 1, 1, 'current', 'accepted_legacy'
            )"""
        )
        conn.execute("INSERT INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, created_at) VALUES ('asrt-ws-2', 'ev-1', 'supports', '2026-10-01T00:00:00Z')")
        conn.commit()

    panel = GraphHygienePanel()
    run1_pkg = panel.run_panel(graph_path)
    cluster_ids = {c["cluster_id"] for c in run1_pkg.hitl_clusters}
    assert "CLUSTER-02C2-PROPOSAL-LIFECYCLE-BOUNDARY" in cluster_ids
    assert "CLUSTER-02D2-ROLE-PROJECT-SCOPE" in cluster_ids
    assert "CLUSTER-03-EDGE-PARITY" in cluster_ids
    assert "CLUSTER-08-GOVERNANCE-AUDIT" in cluster_ids

    # Operator approves all clusters EXCEPT 02C2 and 02D2 (kept distinct)
    approved = []
    excluded = []
    for c in run1_pkg.hitl_clusters:
        if c["cluster_id"] in ("CLUSTER-02C2-PROPOSAL-LIFECYCLE-BOUNDARY", "CLUSTER-02D2-ROLE-PROJECT-SCOPE"):
            excluded.extend(c["findings"])
        else:
            approved.extend(c["findings"])

    pkg_file = tmp_path / "run1_approved.json"
    excl_file = tmp_path / "run1_excluded.json"
    pkg_file.write_text(json.dumps({"status": "approved", "strict_package_gating": True, "findings": approved}), encoding="utf-8")
    excl_file.write_text(json.dumps({"excluded_findings": excluded}), encoding="utf-8")

    receipt = apply_hygiene_remediation(
        package_path=pkg_file,
        excluded_package_path=excl_file,
        promote_generation=True,
        runtime_root=runtime_root,
    )
    assert receipt["terminal_outcome"] == "PASS"
    assert receipt["remediation_stats"]["boundary_exclusions_recorded"] == 2

    # Run 2 reads from active_pair.json just like run_hygiene_agent_cron.py
    active_pair = json.loads((runtime_root / "active_pair.json").read_text(encoding="utf-8"))
    run2_graph = runtime_root / active_pair["graph_path"]
    run2_pkg = panel.run_panel(run2_graph)
    assert len(run2_pkg.findings) == 0
    assert len(run2_pkg.hitl_clusters) == 0

    # Verify excluded nodes remain active=1 and distinct in the promoted graph
    with sqlite3.connect(f"file:{run2_graph}?mode=ro", uri=True) as conn:
        for nid in ("role_generic_lead", "role_atlas_lead", "ws_infra_auto", "ws_infra_auto_prop"):
            active_val = conn.execute("SELECT active FROM node WHERE node_id=?", (nid,)).fetchone()[0]
            assert active_val == 1


def test_prefiltered_hitl_clusters_and_hex_suffix_dedup(tmp_path: Path) -> None:
    """Verifies filter_package_by_clusters routes omitted cluster findings to excluded_findings and dedup strips [6-hex] suffixes."""
    from dual_pipeline.graph_maintenance_core.dedup import find_semantic_duplicates
    from tools.tier2_dedup_core.hygiene_promotion import filter_package_by_clusters

    f_keep = {
        "detective": "StructuralValidator",
        "proposed_fix": "merge_entities",
        "entity_references": ["node_a", "node_b"],
        "description": "Merge A and B",
    }
    f_omit = {
        "detective": "StructuralValidator",
        "proposed_fix": "merge_entities",
        "entity_references": ["role_generic_lead", "role_atlas_lead"],
        "description": "Keep distinct role pair",
    }
    pkg = {
        "status": "approved",
        "findings": [f_keep],
        "hitl_clusters": [
            {"cluster_id": "C1", "findings": [f_keep]},
            {"cluster_id": "C2", "findings": [f_omit]},
        ],
    }
    filtered = filter_package_by_clusters(pkg)
    assert len(filtered["findings"]) == 1
    assert len(filtered["excluded_findings"]) == 1
    assert filtered["excluded_findings"][0]["entity_references"] == ["role_generic_lead", "role_atlas_lead"]

    db_path = tmp_path / "hex_dedup.sqlite"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            """CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                scope_text TEXT,
                currentness_state TEXT DEFAULT 'current',
                confidence_score REAL DEFAULT 0.9,
                recorded_at TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO assertion VALUES ('a-1', 'org_nea', 'HAS_CLAIM_TEXT', NULL, 'Apex Global Analytics governs AI delivery [8b2bc7].', 'Apex Global Analytics governs AI delivery [8b2bc7].', 'current', 0.9, '2026-10-25T00:00:00Z')"
        )
        conn.execute(
            "INSERT INTO assertion VALUES ('a-2', 'org_nea', 'HAS_CLAIM_TEXT', NULL, 'Apex Global Analytics governs AI delivery [8c4e10].', 'Apex Global Analytics governs AI delivery [8c4e10].', 'current', 0.9, '2026-10-26T00:00:00Z')"
        )
        conn.commit()
        dups = find_semantic_duplicates(conn, similarity_threshold=0.9)
        assert len(dups) == 1
        assert dups[0]["winner_id"] == "a-2"
        assert dups[0]["loser_id"] == "a-1"


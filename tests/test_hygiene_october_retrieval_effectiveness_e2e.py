"""End-to-end tests for October 27-28 graph retrieval effectiveness detectors and remediation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.graph_maintenance_core.hygiene_agent import GraphHygienePanel
from dual_pipeline.graph_maintenance_core.hygiene_mutation_agents import (
    compile_promised_mutations,
    verify_hitl_cluster_mutations_applied,
)
from dual_pipeline.graph_scout_core.scout import GraphScout
from storage.database import sqlite3
from tools.apply_graph_hygiene_remediation import apply_hygiene_remediation


def _seed_october_retrieval_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT,
            canonical_name TEXT,
            display_name TEXT,
            description TEXT,
            aliases_json TEXT DEFAULT '[]',
            active INTEGER DEFAULT 1,
            status TEXT DEFAULT 'canonical',
            canonical_status TEXT DEFAULT 'canonical',
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_name TEXT,
            source_date TEXT,
            created_at TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            canonical_status TEXT DEFAULT 'canonical',
            currentness_state TEXT DEFAULT 'current',
            validation_state TEXT DEFAULT 'verified',
            canonical_eligible INTEGER DEFAULT 1,
            retrieval_eligible INTEGER DEFAULT 1,
            is_quarantined INTEGER DEFAULT 0,
            namespace TEXT DEFAULT 'PUBLIC',
            qualifiers_json TEXT DEFAULT '{}',
            confidence_score REAL DEFAULT 0.92,
            primary_source_id TEXT,
            superseded_by_assertion_id TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            source_id TEXT,
            direct_quote TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT
        );
        CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY,
            object_type TEXT,
            object_id TEXT,
            assertion_id TEXT,
            title TEXT,
            body TEXT,
            canonical_status TEXT DEFAULT 'canonical',
            currentness_state TEXT DEFAULT 'current',
            retrieval_eligible INTEGER DEFAULT 1,
            updated_at TEXT
        );
        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT,
            to_node_id TEXT,
            edge_type TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE legacy_edge_assertion_map (
            edge_id TEXT,
            assertion_id TEXT
        );
        CREATE TABLE entity_redirect (
            old_node_id TEXT PRIMARY KEY,
            new_node_id TEXT,
            redirect_reason TEXT,
            created_at TEXT
        );
        CREATE TABLE audit_event (
            audit_event_id TEXT PRIMARY KEY,
            actor_or_process TEXT,
            action TEXT,
            record_type TEXT,
            record_id TEXT,
            previous_value_json TEXT,
            new_value_json TEXT,
            source_id TEXT,
            event_timestamp TEXT,
            reason TEXT,
            validation_reference TEXT,
            approval_reference TEXT DEFAULT 'APP_OK',
            migration_or_ingestion_run_id TEXT,
            release_version TEXT,
            operator_signature TEXT DEFAULT 'SIG_OK',
            created_at TEXT
        );
        """
    )

    # Seed sources (October 21-28, 2026)
    conn.executemany(
        "INSERT INTO source (source_id, source_name, source_date, created_at) VALUES (?, ?, ?, ?)",
        [
            ("src-1021", "Sherpa Intro 10-21-2026", "2026-10-21", "2026-10-21T10:00:00Z"),
            ("src-1028", "Sherpa & LENS Architecture Sync 10-28-2026", "2026-10-28", "2026-10-28T14:00:00Z"),
        ],
    )

    # Seed nodes:
    # 1. Speaker_Alpha Muschett with stale/frozen description ("Exact title remain unresolved") + role node Enterprise Architect
    # 2. Catalina Orostegui + target node Project Beacon Platform (for literal-only mention missing object_node_id)
    # 3. Tyler McCord (pure PartnerAdvisory NEE operations) + Amit Ayer (executive misattributed owner)
    conn.executemany(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, active, canonical_status) VALUES (?, ?, ?, ?, ?, 1, 'canonical')",
        [
            ("person_brien_muschett", "person", "Speaker_Alpha Muschett", "Speaker_Alpha Muschett", "Exact title and reporting line remain unresolved",),
            ("role_enterprise_architect", "role", "Enterprise Architect", "Enterprise Architect", "Enterprise Architecture role"),
            ("person_catalina_orostegui", "person", "Catalina Orostegui", "Catalina Orostegui", "Senior Business Analyst"),
            ("workstream_project_beacon", "workstream", "Project Beacon", "Project Beacon", "Project Beacon EA program"),
            ("person_tyler_mccord", "person", "Tyler McCord", "Tyler McCord", "100% operations (onboarding, access, laptops) NEE PartnerAdvisory"),
            ("person_amit", "person", "Amit Ayer", "Amit Ayer", "Account executive leadership"),
            ("person_karyn", "person", "Morgan Vance", "Morgan Vance", "LENS Delivery Lead"),
            ("workstream_lens", "workstream", "Project LENS", "Project LENS", "LENS inspection platform"),
        ],
    )

    # Seed assertions:
    # A1: Authoritative role assertion for Speaker_Alpha Muschett -> role_enterprise_architect (object_literal is NULL!)
    # A2: Stale HAS_CLAIM_TEXT caveat for Speaker_Alpha Muschett ("Exact title and reporting line remain unresolved")
    # A3: Literal-only assertion on Catalina Orostegui mentioning "Project Beacon" with object_node_id = NULL
    # A4: Disjoint operations bleed: Tyler McCord attached to workstream_project_beacon
    # A5: Executive owner misattribution: Amit Ayer marked OWNS_OR_LEADS on Project LENS development task instead of Morgan Vance and Jake Stark
    conn.executemany(
        """INSERT INTO assertion (
            assertion_id, subject_node_id, predicate, object_node_id, object_literal,
            scope_text, canonical_status, currentness_state, validation_state,
            canonical_eligible, retrieval_eligible, qualifiers_json, primary_source_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'canonical', 'current', 'verified', 1, 1, ?, ?, ?)""",
        [
            (
                "asrt-brien-role-auth",
                "person_brien_muschett",
                "HAS_ROLE_IN",
                "role_enterprise_architect",
                None,
                "Speaker_Alpha Muschett serves as Enterprise Architect on Project Beacon.",
                "{}",
                "src-1028",
                "2026-10-28T14:00:00Z",
            ),
            (
                "asrt-brien-role-stale",
                "person_brien_muschett",
                "HAS_CLAIM_TEXT",
                None,
                "Speaker_Alpha Muschett attended the session; exact title and reporting line remain unresolved.",
                "Speaker_Alpha Muschett exact title and reporting line remain unresolved.",
                "{}",
                "src-1021",
                "2026-10-21T10:00:00Z",
            ),
            (
                "asrt-catalina-literal-only",
                "person_catalina_orostegui",
                "DELIVERS",
                None,
                "Catalina Orostegui leads business analysis for Project Beacon sprint backlog.",
                "Catalina Orostegui leads business analysis for Project Beacon sprint backlog.",
                "{}",
                "src-1028",
                "2026-10-28T14:00:00Z",
            ),
            (
                "asrt-tyler-sherpa-bleed",
                "person_tyler_mccord",
                "AFFILIATED_WITH",
                "workstream_project_beacon",
                "Tyler McCord handles onboarding and laptop operations.",
                "Tyler McCord handles 100% operations (onboarding, access, laptops) for NEE PartnerAdvisory.",
                '{"boundary_type": "disjoint_workstream_exclusion", "excluded_workstream": "workstream_project_beacon"}',
                "src-1028",
                "2026-10-28T14:00:00Z",
            ),
            (
                "asrt-amit-owner-bleed",
                "person_amit",
                "OWNS_OR_LEADS",
                "workstream_lens",
                "Amit Ayer owns sprint development tasks aligned to Morgan Vance and Jake Stark.",
                "Amit Ayer owns sprint development tasks that should align to Morgan Vance and Jake Stark.",
                "{}",
                "src-1028",
                "2026-10-28T14:00:00Z",
            ),
        ],
    )

    for aid in (
        "asrt-brien-role-auth",
        "asrt-brien-role-stale",
        "asrt-catalina-literal-only",
        "asrt-tyler-sherpa-bleed",
        "asrt-amit-owner-bleed",
    ):
        conn.execute(
            "INSERT INTO evidence_item (evidence_id, source_id, direct_quote) VALUES (?, 'src-1028', 'Verbatim quote for ' || ?)",
            (f"ev-{aid}", aid),
        )
        conn.execute(
            "INSERT INTO assertion_evidence (assertion_id, evidence_id) VALUES (?, ?)",
            (aid, f"ev-{aid}"),
        )
        conn.execute(
            "INSERT INTO search_document (search_document_id, assertion_id, title, body, canonical_status, currentness_state, retrieval_eligible) VALUES (?, ?, ?, ?, 'canonical', 'current', 1)",
            (f"sd-{aid}", aid, f"Title {aid}", f"Body {aid}"),
        )

    conn.commit()
    conn.close()


def test_october_retrieval_effectiveness_discovery_and_remediation_e2e(tmp_path: Path) -> None:
    """Verify discovery, council clustering, and governed remediation of Oct 27-28 retrieval gaps."""
    db_path = tmp_path / "october_retrieval.sqlite"
    _seed_october_retrieval_db(db_path)

    conn = sqlite3.connect(str(db_path))
    scout = GraphScout(graph_path=db_path)
    ap_findings = scout.scan_retrieval_antipatterns(conn)
    conn.close()

    actions = {f.action_recommended for f in ap_findings}
    assert "reconcile_stale_role_and_description" in actions
    assert "rebind_literal_entity_mention_edge" in actions
    assert "quarantine_disjoint_role_or_owner_bleed" in actions

    panel = GraphHygienePanel()
    pkg = panel.run_panel(db_path)
    cluster_ids = {c["cluster_id"] for c in pkg.hitl_clusters}
    assert "CLUSTER-05C-ROLE-AND-BIO-SUPERSESSION" in cluster_ids
    assert "CLUSTER-03B-LITERAL-ENTITY-EDGE-HYDRATION" in cluster_ids
    assert "CLUSTER-07D-DISJOINT-ROLE-AND-OWNER-BLEED" in cluster_ids

    pkg_path = tmp_path / "hygiene_package.json"
    pkg_dict: Dict[str, Any] = {
        "schema": "learning-change-proposal:1.0.0",
        "strict_package_gating": True,
        "findings": [
            {
                "detective": f.detective,
                "severity": f.severity,
                "description": f.description,
                "entity_references": f.entity_references,
                "proposed_fix": f.proposed_fix,
                "raw_data": f.raw_data,
            }
            for f in pkg.findings
        ],
        "hitl_clusters": pkg.hitl_clusters,
    }
    pkg_path.write_text(json.dumps(pkg_dict, indent=2), encoding="utf-8")
    receipt_path = tmp_path / "remediation_receipt.json"
    receipt = apply_hygiene_remediation(
        graph_path=db_path,
        package_path=pkg_path,
        receipt_path=receipt_path,
        approved_clusters=["ALL"],
    )
    assert receipt["terminal_outcome"] == "PASS"
    assert receipt["itemized_hitl_contract"]["status"] == "PASS"
    assert receipt["itemized_hitl_contract"]["coverage_ratio"] == 1.0

    # Verify independent state readback on SQLite database
    ro_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    brien_desc = ro_conn.execute(
        "SELECT description FROM node WHERE node_id = 'person_brien_muschett'"
    ).fetchone()[0]
    assert "Enterprise Architect" in brien_desc
    assert "unresolved" not in brien_desc.lower()

    stale_row = ro_conn.execute(
        "SELECT currentness_state, retrieval_eligible, superseded_by_assertion_id FROM assertion WHERE assertion_id = 'asrt-brien-role-stale'"
    ).fetchone()
    assert stale_row[0] == "historical"
    assert stale_row[1] == 0
    assert stale_row[2] == "asrt-brien-role-auth"

    rebound_obj = ro_conn.execute(
        "SELECT object_node_id FROM assertion WHERE assertion_id = 'asrt-catalina-literal-only'"
    ).fetchone()[0]
    assert rebound_obj == "workstream_project_beacon"

    bleed_rows = ro_conn.execute(
        "SELECT assertion_id, currentness_state, retrieval_eligible FROM assertion WHERE assertion_id IN ('asrt-tyler-sherpa-bleed', 'asrt-amit-owner-bleed')"
    ).fetchall()
    assert len(bleed_rows) == 2
    for _, c_state, r_elig in bleed_rows:
        assert c_state == "historical"
        assert r_elig == 0

    ro_conn.close()

    # Verify idempotency: second run detects 0 remaining findings for these 3 actions
    pkg_after = panel.run_panel(db_path)
    remaining_fixes = {
        f.proposed_fix
        for f in pkg_after.findings
        if f.proposed_fix
        in (
            "reconcile_stale_role_and_description",
            "rebind_literal_entity_mention_edge",
            "quarantine_disjoint_role_or_owner_bleed",
        )
    }
    assert remaining_fixes == set()

    conn2 = sqlite3.connect(str(db_path))
    promised = compile_promised_mutations(conn2, pkg_dict)
    conn2.close()
    ver = verify_hitl_cluster_mutations_applied(db_path, promised)
    assert ver["status"] == "PASS"

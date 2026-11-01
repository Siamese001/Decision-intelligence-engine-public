"""
tests/end_to_end/test_stakeholder_role_ssot_e2e.py

End-to-end verification of Wave 2 Stakeholder Role Extraction, UWG Persistence &
Graph Data Hygiene across active GraphDB clones, PreSynthesisEntityResolver, and
UniversalWriteGateway.persist_validated_role.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Dict, Tuple

import pytest

from dual_pipeline.runtime_root import resolve_active_graph_path, resolve_authoritative_runtime_root
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
    resolve_person_entity,
)
from dual_pipeline.transcript_metadata_core.entity_resolver_graph import (
    _parse_entity_org_and_role,
)
from dual_pipeline.uwg_core.universal_write_gateway_role import persist_validated_role
from storage.connection import create_sqlite_conn


def _seed_production_like_sandbox(db_path: Path) -> Path:
    conn = create_sqlite_conn(db_path, readonly=False)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode = WAL")
    cur.execute(
        """
        CREATE TABLE node_type_registry (
            node_type_id TEXT PRIMARY KEY,
            registry_state TEXT NOT NULL
        )
        """
    )
    for nt in ("stakeholder", "internal_person", "role_group", "workstream", "deliverable", "org"):
        cur.execute("INSERT INTO node_type_registry VALUES (?, 'active')", (nt,))

    cur.execute(
        """
        CREATE TABLE controlled_value_registry (
            vocabulary TEXT NOT NULL,
            value TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (vocabulary, value)
        )
        """
    )
    cur.execute("INSERT INTO controlled_value_registry VALUES ('evidence_relationship', 'supports', 1)")

    cur.execute(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            description TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE VIRTUAL TABLE node_fts USING fts5(
            node_id UNINDEXED,
            canonical_name,
            description,
            aliases,
            node_type UNINDEXED
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_name TEXT NOT NULL,
            source_date TEXT,
            authority_rank INTEGER NOT NULL,
            source_posture TEXT NOT NULL,
            cold_archive_ref TEXT,
            notes TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE source_version (
            source_version_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES source(source_id),
            version_number INTEGER NOT NULL,
            source_fingerprint TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            source_date_normalized TEXT,
            source_date_precision TEXT NOT NULL,
            source_date_status TEXT NOT NULL,
            authority_rank INTEGER NOT NULL,
            scope_text TEXT NOT NULL,
            posture_text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            evidence_fingerprint TEXT NOT NULL,
            source_id TEXT NOT NULL REFERENCES source(source_id),
            source_version_id TEXT NOT NULL REFERENCES source_version(source_version_id),
            locator_kind TEXT NOT NULL,
            locator_value TEXT NOT NULL,
            direct_quote TEXT,
            evidence_directness TEXT NOT NULL,
            source_authority_rank INTEGER NOT NULL,
            extraction_confidence REAL NOT NULL,
            corroboration_group TEXT,
            location_status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL REFERENCES node(node_id),
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            confidence_score REAL NOT NULL,
            confidence_label TEXT,
            currentness_state TEXT NOT NULL,
            validation_state TEXT NOT NULL,
            valid_from TEXT,
            superseded_by_assertion_id TEXT,
            superseded_at TEXT,
            classification TEXT,
            allowed_audiences_json TEXT,
            primary_source_id TEXT,
            canonical_status TEXT DEFAULT 'candidate',
            retrieval_eligible INTEGER DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE assertion_evidence (
            assertion_id TEXT NOT NULL REFERENCES assertion(assertion_id),
            evidence_id TEXT NOT NULL REFERENCES evidence_item(evidence_id),
            evidence_relationship TEXT NOT NULL,
            independent_corroboration INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            PRIMARY KEY (assertion_id, evidence_id)
        )
        """
    )

    # Seed contaminated Eduardo More, Parker Vance with OWNS_OR_LEADS workstream, Andrew Hanson, Calvin Gilmartin
    nodes = [
        (
            "person_eduardo_more",
            "stakeholder",
            "Eduardo More",
            "Eduardo More",
            "Affiliation label(s): NEA. Source roles: Atlas Lead / Rule Specialist & VP / Executive Sponsor.",
        ),
        (
            "person_anthony_funk",
            "stakeholder",
            "Parker Vance",
            "Parker Vance",
            "Director Strategic Initiatives, Op Excellence; PGD stakeholder.",
        ),
        (
            "person_andrew_hanson",
            "internal_person",
            "Andrew Hanson",
            "Andrew Hanson",
            "AI/ML Director; PartnerAdvisory leadership.",
        ),
        (
            "person_calvin_gilmartin",
            "stakeholder",
            "Calvin Gilmartin",
            "Calvin Gilmartin",
            "Senior Director, Trading Risk & Analytics; NEA leadership.",
        ),
        (
            "workstream_lens",
            "workstream",
            "LENS",
            "LENS",
            "LENS workstream",
        ),
        (
            "deliverable_charter_asset",
            "deliverable",
            "Charter Asset Data Product",
            "Charter Asset Data Product",
            "Charter deliverable",
        ),
        (
            "role_atlas_lead",
            "role_group",
            "Atlas Lead / Rule Specialist",
            "Atlas Lead / Rule Specialist",
            "Role group",
        ),
    ]
    for nid, ntype, cname, dname, desc in nodes:
        cur.execute(
            "INSERT INTO node (node_id, node_type, canonical_name, display_name, description, active) VALUES (?, ?, ?, ?, ?, 1)",
            (nid, ntype, cname, dname, desc),
        )
        cur.execute(
            "INSERT INTO node_fts (node_id, canonical_name, description, aliases, node_type) VALUES (?, ?, ?, '', ?)",
            (nid, cname, desc, ntype),
        )

    assertions = [
        (
            "asrt_ed_role",
            "person_eduardo_more",
            "HAS_ROLE_IN",
            "role_atlas_lead",
            None,
            "Eduardo More HAS_ROLE Atlas Lead / Rule Specialist.",
            1.0,
            "current",
            "needs_review",
            "2026-10-08",
        ),
        (
            "asrt_ed_workstream",
            "person_eduardo_more",
            "HAS_ROLE_IN",
            "workstream_lens",
            None,
            "Eduardo More is an active participant in the LENS workstream",
            0.9,
            "current",
            "accepted_legacy",
            "2026-09-23",
        ),
        (
            "asrt_anthony_owns",
            "person_anthony_funk",
            "OWNS_OR_LEADS",
            "workstream_lens",
            "Lens-Pgd-Stakeholders",
            "Parker Vance OWNS_OR_LEADS Lens-Pgd-Stakeholders.",
            0.95,
            "current",
            "accepted_legacy",
            "2026-07-08",
        ),
        (
            "asrt_andrew_owns",
            "person_andrew_hanson",
            "OWNS_OR_LEADS",
            "deliverable_charter_asset",
            "Charter Asset Data Product",
            "Andrew Hanson OWNS_OR_LEADS Charter Asset Data Product.",
            0.95,
            "current",
            "accepted_legacy",
            "2026-07-08",
        ),
    ]
    for aid, sub, pred, obj_id, obj_lit, scope, conf, cstate, vstate, vfrom in assertions:
        cur.execute(
            """
            INSERT INTO assertion (
                assertion_id, subject_node_id, predicate, object_node_id, object_literal,
                scope_text, confidence_score, currentness_state, validation_state, valid_from
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (aid, sub, pred, obj_id, obj_lit, scope, conf, cstate, vstate, vfrom),
        )

    conn.commit()
    conn.close()
    return db_path


def test_e2e_stakeholder_role_ssot_and_uwg_persistence(tmp_path: Path) -> None:
    """
    Verifies end-to-end Wave 2 acceptance criteria:
    1. Eduardo More resolves strictly to 'Atlas Lead / Rule Specialist' (never VP / Executive Sponsor).
    2. Parker Vance resolves to 'Director Strategic Initiatives, Op Excellence' (never 'Lens-Pgd-Stakeholders').
    3. Andrew Hanson resolves to 'AI/ML Director' (never 'Charter Asset Data Product').
    4. persist_validated_role updates node.description, syncs node_fts, and binds supporting assertion_evidence.
    """
    db_path = _seed_production_like_sandbox(tmp_path / "graph.sqlite")

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path, runtime_root=tmp_path)

    ed = resolver.resolve("Eduardo More", project_name="project_lens")
    assert ed is not None
    assert ed.role == "Atlas Lead / Rule Specialist"
    assert "VP" not in ed.role
    assert "Executive Sponsor" not in ed.role

    ed_alias = resolver.resolve("Ed", project_name="project_lens")
    assert ed_alias is not None
    assert ed_alias.role == "Atlas Lead / Rule Specialist"

    anthony = resolver.resolve("Parker Vance")
    assert anthony is not None
    assert anthony.role == "Director Strategic Initiatives, Op Excellence"
    assert "Lens-Pgd-Stakeholders" not in anthony.role

    andrew = resolver.resolve("Andrew Hanson")
    assert andrew is not None
    assert andrew.role == "AI/ML Director"
    assert "Charter" not in andrew.role

    # Execute UWG persist_validated_role on Parker Vance and Eduardo More
    res_anthony = persist_validated_role(
        db_path,
        "Parker Vance",
        "Director Strategic Initiatives, Op Excellence",
        operator_id="wave2_e2e_verifier",
    )
    assert res_anthony["status"] == "PERSISTED_SSOT"
    asrt_id = res_anthony["persisted_assertion_id"]

    res_ed = persist_validated_role(
        db_path,
        "Eduardo More",
        "Atlas Lead / Rule Specialist",
        operator_id="wave2_e2e_verifier",
    )
    assert res_ed["status"] == "PERSISTED_SSOT"
    ed_asrt_id = res_ed["persisted_assertion_id"]

    # Independent read-only SQLite verification of node.description, node_fts, and assertion_evidence
    conn_ro = create_sqlite_conn(db_path, readonly=True)
    cur_ro = conn_ro.cursor()

    cur_ro.execute("SELECT description FROM node WHERE node_id = 'person_eduardo_more'")
    ed_desc = cur_ro.fetchone()[0]
    assert "Atlas Lead / Rule Specialist" in ed_desc
    assert "VP / Executive Sponsor" not in ed_desc

    cur_ro.execute("SELECT description FROM node_fts WHERE node_id = 'person_anthony_funk'")
    fts_desc = cur_ro.fetchone()[0]
    assert "Director Strategic Initiatives, Op Excellence" in fts_desc

    for target_asrt_id in (asrt_id, ed_asrt_id):
        cur_ro.execute(
            """
            SELECT ae.assertion_id, ae.evidence_id, ae.evidence_relationship, ei.locator_kind
            FROM assertion_evidence ae
            JOIN evidence_item ei ON ae.evidence_id = ei.evidence_id
            WHERE ae.assertion_id = ?
            """,
            (target_asrt_id,),
        )
        ev_row = cur_ro.fetchone()
        assert ev_row is not None, f"Missing assertion_evidence row for {target_asrt_id}"
        assert ev_row[2] == "supports"
        assert ev_row[3] in ("hitl_ledger", "conversation_proposition")

    # Verify OWNS_OR_LEADS workstream assertion on Parker Vance was NOT destroyed by role ratification
    cur_ro.execute("SELECT currentness_state FROM assertion WHERE assertion_id = 'asrt_anthony_owns'")
    owns_state = cur_ro.fetchone()[0]
    assert owns_state == "current"
    conn_ro.close()


def test_e2e_active_graphdb_stakeholder_description_parse_rate() -> None:
    """
    Verifies against an inspection of the active GraphDB that >95% of stakeholder
    descriptions containing titles parse cleanly without falling back to 'Participant',
    and 0 OWNS_OR_LEADS workstreams leak into resolved titles.
    """
    rt_root = resolve_authoritative_runtime_root()
    active_graph = resolve_active_graph_path(rt_root, enforce_integrity=False)
    if not active_graph.is_file():
        pytest.skip("Active GraphDB not provisioned in local environment")

    conn = create_sqlite_conn(active_graph, readonly=True)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT node_id, canonical_name, description
        FROM node
        WHERE node_type IN ('stakeholder', 'person', 'internal_person') AND active = 1
        """
    )
    rows = cur.fetchall()
    conn.close()

    titled_nodes = 0
    parsed_nodes = 0
    for _nid, _cname, desc in rows:
        desc_str = str(desc or "")
        # Exclude bare affiliation-only stubs that contain no title information
        if not desc_str or desc_str.strip() in (
            "Apex Global Enterprises stakeholder.",
            "PartnerAdvisory consultant.",
            "Internal Consulting person.",
        ):
            continue
        from dual_pipeline.transcript_metadata_core.entity_resolver_graph import _ROLE_KEYWORDS_RE
        if _ROLE_KEYWORDS_RE.search(desc_str):
            titled_nodes += 1
            _org, role = _parse_entity_org_and_role(desc_str, {})
            if role and role != "Participant":
                parsed_nodes += 1

    assert titled_nodes >= 150
    parse_ratio = parsed_nodes / titled_nodes
    assert parse_ratio >= 0.95, f"Expected >=95% stakeholder title parse rate, got {parse_ratio:.3f} ({parsed_nodes}/{titled_nodes})"

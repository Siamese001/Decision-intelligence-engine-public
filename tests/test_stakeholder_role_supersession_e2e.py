"""
tests/test_stakeholder_role_supersession_e2e.py

Class B E2E Receipt Test for Wave 2:
Verifies split-identity coalescence, description upsert, and cross-twin role supersession
via Universal Write Gateway role reconciliation.
"""
import types
import sqlite3
import pytest
from pathlib import Path
from dual_pipeline.uwg_core.universal_write_gateway_role import _supersede_competing_assertions
from dual_pipeline.identity_core.resolver import _deduplicate_exact_matches
from dual_pipeline.identity_core.records import EntityRecord


@pytest.fixture
def supersession_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "test_supersession.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            description TEXT,
            status TEXT DEFAULT 'canonical',
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT '2026-10-01',
            updated_at TEXT DEFAULT '2026-10-01'
        )
    """)
    cur.execute("""
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            canonical_status TEXT DEFAULT 'canonical',
            currentness_state TEXT DEFAULT 'current',
            retrieval_eligible INTEGER DEFAULT 1,
            superseded_by_assertion_id TEXT,
            superseded_at TEXT
        )
    """)

    # Seed twin nodes for Brandon Que (canonical entity vs legacy entity-hex)
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name, description) VALUES ('person:brandon-que', 'stakeholder', 'Brandon Que', 'Enterprise Architect / Solution Architect Lead')")
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name, description) VALUES ('entity-83d85ee4e07e75e23f5dd5b5d9fb', 'stakeholder', 'Brandon Que', 'Project Lead affiliated with Apex Global Enterprises')")
    
    # Role groups
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name) VALUES ('role_po', 'role_group', 'Project Beacon Product Owner')")
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name) VALUES ('role_sal', 'role_group', 'Solution Architect Lead')")

    # Competing old assertions on entity-83d85...
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id) VALUES ('a_old_role_sal', 'entity-83d85ee4e07e75e23f5dd5b5d9fb', 'HAS_ROLE_IN', 'role_sal')")
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal) VALUES ('a_old_claim_pl', 'entity-83d85ee4e07e75e23f5dd5b5d9fb', 'HAS_CLAIM_TEXT', 'Brandon Que: Project Lead affiliated with Apex Global Enterprises')")

    conn.commit()
    yield conn
    conn.close()


def test_split_identity_cross_twin_supersession_e2e(supersession_db: sqlite3.Connection):
    """Verifies that superseding roles on person:brandon-que also retires competing assertions on entity-83d85ee..."""
    now_iso = "2026-10-28T18:00:00Z"
    new_asrt_id = "a_new_po_role"
    
    # Add new authoritative role on person:brandon-que
    cur = supersession_db.cursor()
    cur.execute(
        "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, currentness_state) "
        "VALUES (?, 'person:brandon-que', 'HAS_ROLE_IN', 'role_po', 'current')",
        (new_asrt_id,),
    )
    supersession_db.commit()

    # Run supersession targeting person:brandon-que
    retired = _supersede_competing_assertions(
        supersession_db,
        subject_id="person:brandon-que",
        new_asrt_id=new_asrt_id,
        target_role_node_id="role_po",
        now_iso=now_iso,
    )

    # Both the HAS_ROLE_IN and the HAS_CLAIM_TEXT from the twin entity-83d85ee... must be retired
    assert "a_old_role_sal" in retired
    assert "a_old_claim_pl" in retired

    # Verify rows in DB
    rows = {
        r["assertion_id"]: r
        for r in cur.execute("SELECT assertion_id, currentness_state, superseded_by_assertion_id FROM assertion").fetchall()
    }
    assert rows["a_old_role_sal"]["currentness_state"] in ("historical", "superseded")
    assert rows["a_old_role_sal"]["superseded_by_assertion_id"] == new_asrt_id
    assert rows["a_old_claim_pl"]["currentness_state"] in ("historical", "superseded")
    assert rows["a_old_claim_pl"]["superseded_by_assertion_id"] == new_asrt_id


def test_deduplicate_exact_matches_for_stakeholders():
    """Verifies exact-match deduplication resolves stakeholder twins to canonical entity."""
    catalog = types.SimpleNamespace(entities={
        "person:brandon-que": EntityRecord(
            entity_id="person:brandon-que",
            canonical_name="Brandon Que",
            node_type="stakeholder",
            description="PO",
            normalized_name="brandon que",
            status="canonical",
        ),
        "entity-83d85ee4e07e75e23f5dd5b5d9fb": EntityRecord(
            entity_id="entity-83d85ee4e07e75e23f5dd5b5d9fb",
            canonical_name="Brandon Que",
            node_type="stakeholder",
            description="Legacy",
            normalized_name="brandon que",
            status="candidate",
        ),
    })

    matches, conflict = _deduplicate_exact_matches(
        catalog=catalog,
        exact_matches=["entity-83d85ee4e07e75e23f5dd5b5d9fb", "person:brandon-que"],
        declared_type="stakeholder",
    )
    assert matches == ["person:brandon-que"]
    assert not conflict

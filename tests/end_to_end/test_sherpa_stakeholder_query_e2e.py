"""
tests/end_to_end/test_sherpa_stakeholder_query_e2e.py

Class B E2E Receipt Test for Wave 3:
End-to-end query verification and production ground-truth harness.
Verifies that all seven Project Beacon stakeholders resolve with 100% correct roles
and reporting hierarchy:
1. Marcus Balk (Enterprise Architecture Lead)
2. Speaker_Alpha Muschett (Enterprise Architect, reports to Marcus Balk)
3. Brandon Que (Product Owner)
4. Egle Tamasevice (Technical Lead)
5. Jeffrey Glade (Product Manager, reports to Marcus Balk)
6. David Childers (Scrum Master, reports to Marcus Balk)
7. Catalina Orostegui (Senior Business Analyst)
"""
import sqlite3
import pytest
from pathlib import Path
from dual_pipeline.query_core.traversal_helper import traverse_project_resources
from dual_pipeline.query_core.graph_first_engine import GraphFirstQueryEngine


@pytest.fixture
def sherpa_e2e_db(tmp_path: Path) -> Path:
    """Provisions a hermetic SQLite DB with complete Project Beacon nodes and assertions."""
    db_path = tmp_path / "sherpa_e2e.sqlite"
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
            status TEXT DEFAULT 'canonical'
        )
    """)
    cur.execute("""
        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT NOT NULL,
            to_node_id TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            description TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            confidence_score REAL DEFAULT 1.0,
            currentness_state TEXT DEFAULT 'current',
            canonical_status TEXT DEFAULT 'canonical',
            canonical_eligible INTEGER DEFAULT 1,
            retrieval_eligible INTEGER DEFAULT 1,
            superseded_by_assertion_id TEXT,
            primary_source_id TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_name TEXT NOT NULL
        )
    """)

    # 1. Project node
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name, display_name) VALUES ('proj_sherpa', 'project', 'Project Beacon', 'Project Beacon Project')")
    cur.execute("INSERT INTO source (source_id, source_type, source_name) VALUES ('src_sherpa_meeting', 'meeting', 'Project Beacon Architecture and Sprint Alignment')")

    # 2. Stakeholders
    stakeholders = [
        ("person:marcus-balk", "Marcus Balk", "Enterprise Architecture Lead at PartnerAdvisory"),
        ("person:brien-muschett", "Speaker_Alpha Muschett", "Enterprise Architect"),
        ("person:brandon-que", "Brandon Que", "Product Owner for Sherpa"),
        ("person:egle-tamasevice", "Egle Tamasevice", "Sherpa Technical Lead"),
        ("person:jeffrey-glade", "Jeffrey Glade", "Product Manager"),
        ("person:david-childers", "David Childers", "Scrum Master NEE IT"),
        ("person:catalina-orostegui", "Catalina Orostegui", "Senior Business Analyst"),
    ]
    for nid, cname, desc in stakeholders:
        cur.execute("INSERT INTO node (node_id, node_type, canonical_name, display_name, description) VALUES (?, 'stakeholder', ?, ?, ?)", (nid, cname, cname, desc))

    # 3. Roles as role_group nodes
    roles = [
        ("role_ea_lead", "Enterprise Architecture Lead"),
        ("role_ea", "Enterprise Architect"),
        ("role_po", "Product Owner"),
        ("role_tech_lead", "Technical Lead"),
        ("role_pm", "Product Manager"),
        ("role_scrum_master", "Scrum Master"),
        ("role_sr_ba", "Senior Business Analyst"),
    ]
    for rid, rname in roles:
        cur.execute("INSERT INTO node (node_id, node_type, canonical_name, display_name) VALUES (?, 'role_group', ?, ?)", (rid, rname, rname))

    # 4. Project edges & co-occurring source assertions
    # Brandon, Egle, Catalina have direct project workstream edges
    cur.execute("INSERT INTO edge (edge_id, from_node_id, to_node_id, edge_type, description) VALUES ('e1', 'person:brandon-que', 'proj_sherpa', 'PART_OF_PROJECT', 'Sherpa PO')")
    cur.execute("INSERT INTO edge (edge_id, from_node_id, to_node_id, edge_type, description) VALUES ('e2', 'person:egle-tamasevice', 'proj_sherpa', 'PART_OF_PROJECT', 'Sherpa Tech Lead')")
    cur.execute("INSERT INTO edge (edge_id, from_node_id, to_node_id, edge_type, description) VALUES ('e3', 'person:catalina-orostegui', 'proj_sherpa', 'PART_OF_PROJECT', 'Sherpa Senior BA')")

    # Marcus, Speaker_Alpha, Jeffrey, David are co-occurring in the Sherpa meeting source
    for i, (nid, _, _) in enumerate(stakeholders):
        cur.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, primary_source_id) "
            "VALUES (?, ?, 'ATTENDED', 'Attended Sherpa Alignment', 'src_sherpa_meeting')",
            (f"asrt_att_{i}", nid),
        )

    # 5. HAS_ROLE_IN assertions (relational, object_node_id -> role_group)
    role_mappings = [
        ("asrt_role_marcus", "person:marcus-balk", "role_ea_lead"),
        ("asrt_role_brien", "person:brien-muschett", "role_ea"),
        ("asrt_role_brandon", "person:brandon-que", "role_po"),
        ("asrt_role_egle", "person:egle-tamasevice", "role_tech_lead"),
        ("asrt_role_jeffrey", "person:jeffrey-glade", "role_pm"),
        ("asrt_role_david", "person:david-childers", "role_scrum_master"),
        ("asrt_role_catalina", "person:catalina-orostegui", "role_sr_ba"),
    ]
    for aid, sub_id, obj_role_id in role_mappings:
        cur.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, primary_source_id) "
            "VALUES (?, ?, 'HAS_ROLE_IN', ?, 'src_sherpa_meeting')",
            (aid, sub_id, obj_role_id),
        )

    # 6. REPORTS_TO assertions (Speaker_Alpha, Jeffrey, David report to Marcus Balk)
    reporting_lines = [
        ("asrt_rep_brien", "person:brien-muschett", "person:marcus-balk"),
        ("asrt_rep_jeffrey", "person:jeffrey-glade", "person:marcus-balk"),
        ("asrt_rep_david", "person:david-childers", "person:marcus-balk"),
    ]
    for aid, sub_id, mgr_id in reporting_lines:
        cur.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, primary_source_id) "
            "VALUES (?, ?, 'REPORTS_TO', ?, 'src_sherpa_meeting')",
            (aid, sub_id, mgr_id),
        )

    conn.commit()
    conn.close()
    return db_path


def test_traverse_project_resources_recovers_all_seven_stakeholders(sherpa_e2e_db: Path):
    """Verifies that traverse_project_resources recovers all 7 Project Beacon stakeholders with exact roles."""
    conn = sqlite3.connect(str(sherpa_e2e_db))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    tables = {"node", "edge", "assertion", "source"}
    entities, terms = traverse_project_resources(
        cursor=cur,
        project_node_ids=["proj_sherpa"],
        has_quarantine=False,
        tables=tables,
    )
    conn.close()

    entity_map = {e.canonical_name: e for e in entities}
    assert len(entity_map) == 7

    # 1. Marcus Balk: Enterprise Architecture Lead
    assert "Marcus Balk" in entity_map
    assert "Enterprise Architecture Lead" in entity_map["Marcus Balk"].role

    # 2. Speaker_Alpha Muschett: Enterprise Architect, reports to Marcus Balk
    assert "Speaker_Alpha Muschett" in entity_map
    assert "Enterprise Architect" in entity_map["Speaker_Alpha Muschett"].role
    assert "Reports to Marcus Balk" in entity_map["Speaker_Alpha Muschett"].role

    # 3. Brandon Que: Product Owner
    assert "Brandon Que" in entity_map
    assert "Product Owner" in entity_map["Brandon Que"].role

    # 4. Egle Tamasevice: Technical Lead
    assert "Egle Tamasevice" in entity_map
    assert "Technical Lead" in entity_map["Egle Tamasevice"].role

    # 5. Jeffrey Glade: Product Manager, reports to Marcus Balk
    assert "Jeffrey Glade" in entity_map
    assert "Product Manager" in entity_map["Jeffrey Glade"].role
    assert "Reports to Marcus Balk" in entity_map["Jeffrey Glade"].role

    # 6. David Childers: Scrum Master, reports to Marcus Balk
    assert "David Childers" in entity_map
    assert "Scrum Master" in entity_map["David Childers"].role
    assert "Reports to Marcus Balk" in entity_map["David Childers"].role

    # 7. Catalina Orostegui: Senior Business Analyst
    assert "Catalina Orostegui" in entity_map
    assert "Senior Business Analyst" in entity_map["Catalina Orostegui"].role


def test_graph_first_engine_resolves_relational_roles_e2e(sherpa_e2e_db: Path):
    """Verifies GraphFirstQueryEngine queries resolve relational roles without dropping object_node_id names."""
    engine = GraphFirstQueryEngine(
        repo_root=sherpa_e2e_db.parent,
        graph_path=sherpa_e2e_db,
    )

    res = engine.execute_query("What is the role of Marcus Balk in Project Beacon?")
    assert res.success is True
    assert res.resolved_tier == 1
    assert len(res.assertions) > 0

    # Ensure Marcus Balk HAS_ROLE_IN has object_name == 'Enterprise Architecture Lead'
    role_asrt = next(
        a for a in res.assertions
        if a["subject_name"] == "Marcus Balk" and a["predicate"] == "HAS_ROLE_IN"
    )
    assert role_asrt["object_name"] == "Enterprise Architecture Lead"
    assert "Enterprise Architecture Lead" in res.formatted_markdown

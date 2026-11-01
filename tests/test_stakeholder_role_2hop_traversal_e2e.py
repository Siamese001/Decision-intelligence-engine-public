"""
tests/test_stakeholder_role_2hop_traversal_e2e.py

Class B E2E Receipt Test for Wave 1:
Verifies 2-hop relational role hydration (HAS_ROLE_IN, REPORTS_TO) and cross-source
stakeholder discovery in project resource traversal and graph-first sidecar resolution.
"""
import sqlite3
import pytest
from pathlib import Path
from dual_pipeline.query_core.traversal_helper import traverse_project_resources
from dual_pipeline.query_core.graph_first_engine import GraphFirstQueryEngine


@pytest.fixture
def isolated_test_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "test_graph.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Create minimal schema conforming to canonical graph structure
    cur.execute("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            description TEXT,
            status TEXT DEFAULT 'canonical',
            active INTEGER DEFAULT 1,
            is_quarantined INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            source_type TEXT DEFAULT 'transcript'
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
            primary_source_id TEXT,
            canonical_status TEXT DEFAULT 'canonical',
            canonical_eligible INTEGER DEFAULT 1,
            currentness_state TEXT DEFAULT 'current',
            retrieval_eligible INTEGER DEFAULT 1,
            superseded_by_assertion_id TEXT,
            confidence_score REAL DEFAULT 1.0,
            is_quarantined INTEGER DEFAULT 0
        )
    """)

    # Seed Workstream and Role Group nodes
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name, display_name) VALUES ('workstream_project_beacon', 'workstream', 'Project Beacon', 'Project Beacon')")
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name, display_name) VALUES ('role_sba', 'role_group', 'Senior Business Analyst', 'Senior Business Analyst')")
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name, display_name) VALUES ('role_ea', 'role_group', 'Enterprise Architect', 'Enterprise Architect')")
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name, display_name) VALUES ('role_lead', 'role_group', 'Enterprise Architecture Lead', 'Enterprise Architecture Lead')")

    # Seed Stakeholder nodes
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name, display_name, description) VALUES ('person_marcus', 'stakeholder', 'Marcus Balk', 'Marcus Balk', 'Enterprise Architecture Leader')")
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name, display_name, description) VALUES ('person_brien', 'stakeholder', 'Speaker_Alpha Muschett', 'Speaker_Alpha Muschett', 'Exact title unresolved')")
    cur.execute("INSERT INTO node (node_id, node_type, canonical_name, display_name, description) VALUES ('person_catalina', 'stakeholder', 'Catalina Orostegui', 'Catalina Orostegui', 'Engineering team member')")

    # Seed Sources
    cur.execute("INSERT INTO source (source_id, source_name) VALUES ('src_sherpa_demo', '20261016 Project Beacon Sprint Demo')")
    cur.execute("INSERT INTO source (source_id, source_name) VALUES ('src_org_chart', 'Document 20261014 Apex Global Enterprises Organizational Hierarchy')")

    # 1-hop assertions
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, primary_source_id) VALUES ('a_brien_ws', 'person_brien', 'CONTRIBUTES_TO', 'workstream_project_beacon', 'src_sherpa_demo')")
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, primary_source_id) VALUES ('a_marcus_ws', 'person_marcus', 'OWNS_OR_LEADS', 'workstream_project_beacon', 'src_sherpa_demo')")
    
    # Catalina co-occurs in Sherpa demo source without direct 1-hop workstream edge
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, primary_source_id) VALUES ('a_cat_speak', 'person_catalina', 'AFFILIATED_WITH', 'workstream_project_beacon', 'src_sherpa_demo')")

    # 2-hop relational role assertions (object_literal is NULL, object_node_id set)
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, primary_source_id) VALUES ('a_cat_role', 'person_catalina', 'HAS_ROLE_IN', 'role_sba', 'src_sherpa_demo')")
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, primary_source_id) VALUES ('a_brien_role', 'person_brien', 'HAS_ROLE_IN', 'role_ea', 'src_sherpa_demo')")
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, primary_source_id) VALUES ('a_marcus_role', 'person_marcus', 'HAS_ROLE_IN', 'role_lead', 'src_org_chart')")

    # 2-hop reporting hierarchy assertion
    cur.execute("INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, primary_source_id) VALUES ('a_brien_reports', 'person_brien', 'REPORTS_TO', 'person_marcus', 'src_org_chart')")

    conn.commit()
    yield conn
    conn.close()


def test_2hop_relational_role_traversal_e2e(isolated_test_db: sqlite3.Connection):
    """Verifies traverse_project_resources resolves 2-hop roles and reporting lines."""
    cursor = isolated_test_db.cursor()
    tables = {"node", "source", "assertion"}
    
    entities, terms = traverse_project_resources(
        cursor=cursor,
        project_node_ids=["workstream_project_beacon"],
        has_quarantine=False,
        tables=tables,
    )
    
    entity_map = {e.canonical_name: e for e in entities}
    assert "Catalina Orostegui" in entity_map
    assert "Speaker_Alpha Muschett" in entity_map
    assert "Marcus Balk" in entity_map

    # Catalina: 2-hop HAS_ROLE_IN must resolve to Senior Business Analyst (not "Engineering team member")
    assert entity_map["Catalina Orostegui"].role == "Senior Business Analyst"

    # Speaker_Alpha: 2-hop HAS_ROLE_IN and REPORTS_TO must resolve to Enterprise Architect (Reports to Marcus Balk)
    assert entity_map["Speaker_Alpha Muschett"].role == "Enterprise Architect (Reports to Marcus Balk)"

    # Marcus: Enterprise Architecture Lead
    assert entity_map["Marcus Balk"].role == "Enterprise Architecture Lead"


def test_graph_first_engine_relational_hydration(isolated_test_db: sqlite3.Connection, tmp_path: Path):
    """Verifies GraphFirstQueryEngine hydrates object_name for relational assertions where object_literal is NULL."""
    db_path = tmp_path / "test_graph.sqlite"
    engine = GraphFirstQueryEngine(repo_root=tmp_path, graph_path=db_path)
    
    nodes, assertions = engine._tier1_relational_traversal(
        question="Catalina Orostegui",
        confidence_threshold=0.5,
    )
    
    role_asrt = next((a for a in assertions if a["predicate"] == "HAS_ROLE_IN"), None)
    assert role_asrt is not None
    assert role_asrt["object_name"] == "Senior Business Analyst"
    
    # Render markdown check
    md = engine._render_assertion_markdown(nodes, assertions, [])
    assert "Senior Business Analyst" in md

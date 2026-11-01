"""
Tests for the Knowledge Graph Hygiene Agent orchestration panel.
Validates the structural flow and output of the 5 adversarial agents.
"""

import sqlite3

import pytest

from dual_pipeline.graph_maintenance_core.hygiene_agent import (
    Finding,
    GraphHygienePanel,
    RemediationPackage,
)


@pytest.fixture
def dummy_graph(tmp_path):
    graph_db = tmp_path / "test_graph.db"
    with sqlite3.connect(graph_db) as conn:
        conn.execute("CREATE TABLE node (id TEXT, type TEXT)")
        conn.execute("CREATE TABLE edge (source TEXT, target TEXT)")
    return graph_db


def test_hygiene_panel_initialization():
    panel = GraphHygienePanel()
    assert panel.detective_1 is not None
    assert panel.detective_2 is not None
    assert panel.detective_3 is not None
    assert panel.detective_4 is not None
    assert panel.judge is not None


def test_hygiene_panel_execution_empty_graph(dummy_graph, monkeypatch):
    class MockGraphScout:
        def __init__(self, *args, **kwargs):
            pass

        def scan_orphan_and_clutter(self, conn):
            return []

        def scan_duplicate_entities(self, conn):
            return []

        def scan_temporal_contradictions(self, conn):
            return []

    monkeypatch.setattr(
        "dual_pipeline.graph_maintenance_core.hygiene_agent.GraphScout", MockGraphScout
    )

    panel = GraphHygienePanel()
    pkg = panel.run_panel(dummy_graph)

    assert isinstance(pkg, RemediationPackage)
    assert pkg.status == "proposed"
    assert pkg.approval_required is True
    assert pkg.auto_apply is False
    assert isinstance(pkg.findings, list)


def test_hygiene_panel_synthesis_logic():
    panel = GraphHygienePanel()
    mock_findings = [
        Finding(
            detective="TemporalAuditor",
            severity="HIGH",
            description="Contradictory stale_after date detected.",
            entity_references=["node-123"],
            proposed_fix="archive_node",
        ),
        Finding(
            detective="ProvenanceInspector",
            severity="LOW",
            description="Missing secondary source metadata.",
            entity_references=["node-456"],
            proposed_fix=None,
        ),
    ]
    pkg = panel.judge.synthesize(mock_findings)
    assert len(pkg.findings) == 2
    assert len(pkg.remediation_directives) == 1
    assert pkg.remediation_directives[0]["action"] == "remediate"
    assert pkg.remediation_directives[0]["target"] == ["node-123"]
    assert pkg.remediation_directives[0]["fix"] == "archive_node"


def test_hygiene_panel_row_factory_and_connection(dummy_graph):
    class MockDetective:
        def audit(self, path, conn):
            assert conn.row_factory is not None
            cursor = conn.cursor()
            cursor.execute("SELECT id, type FROM node")
            return []

    panel = GraphHygienePanel()
    panel.detective_1 = MockDetective()
    panel.detective_2 = MockDetective()
    panel.detective_3 = MockDetective()
    panel.detective_4 = MockDetective()

    pkg = panel.run_panel(dummy_graph)
    assert isinstance(pkg, RemediationPackage)


def test_promotable_candidates_filters_historical_and_archive_respects_supersession(tmp_path):
    from tools.archive_stale_candidates import archive_stale_candidates

    graph_db = tmp_path / "hygiene_supersession.sqlite"
    with sqlite3.connect(graph_db) as conn:
        conn.execute(
            """CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT,
                canonical_name TEXT,
                active INTEGER DEFAULT 1
            )"""
        )
        conn.execute(
            """CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                canonical_status TEXT,
                currentness_state TEXT,
                validation_state TEXT,
                confidence_score REAL,
                superseded_by_assertion_id TEXT,
                scope_text TEXT,
                created_at TEXT,
                updated_at TEXT,
                canonical_eligible INTEGER DEFAULT 1
            )"""
        )
        conn.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT)")
        conn.execute("CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY, direct_quote TEXT)")
        conn.execute("INSERT INTO node VALUES ('person:brett', 'person', 'Brett Gavagni', 1)")
        conn.execute("INSERT INTO evidence_item VALUES ('ev-brett', 'Direct quote from Brett')")
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-grounded-old', 'ev-brett')")
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-historical-cand', 'ev-brett')")

        # Grounded 45-day-old candidate (>30 days, <90 days, not superseded) -> should NOT be archived or escalated, SHOULD be promotable
        conn.execute(
            """INSERT INTO assertion VALUES (
                'asrt-grounded-old', 'person:brett', 'LEADS_INITIATIVE', NULL, 'Cloud Migration',
                'candidate', 'current', 'needs_review', 0.90, NULL, 'Brett leads Cloud Migration',
                datetime('now', '-45 days'), datetime('now', '-45 days'), 1
            )"""
        )
        # Historical candidate with high confidence -> should NOT be flagged as promotable or stale
        conn.execute(
            """INSERT INTO assertion VALUES (
                'asrt-historical-cand', 'person:brett', 'LEADS_INITIATIVE', NULL, 'Old Project',
                'candidate', 'historical', 'needs_review', 0.92, NULL, 'Old project',
                datetime('now', '-45 days'), datetime('now', '-45 days'), 0
            )"""
        )
        conn.commit()

    panel = GraphHygienePanel()
    pkg = panel.run_panel(graph_db)
    archive_findings = [f for f in pkg.findings if f.proposed_fix == "archive_candidate_assertion"]
    assert len(archive_findings) == 0
    reval_findings = [f for f in pkg.findings if f.proposed_fix == "escalate_90d_revalidation"]
    assert len(reval_findings) == 0

    promo_findings = [f for f in pkg.findings if f.proposed_fix == "batch_promote_candidates"]
    assert len(promo_findings) == 1
    assert promo_findings[0].entity_references == ["asrt-grounded-old"]

    # Also verify archive_stale_candidates tool skips grounded unsuperseded candidates
    res = archive_stale_candidates(graph_db, max_age_days=30, disposition="archive")
    assert res["candidates_found"] == 0
    assert res["status"] == "clean"


def test_90_day_uncorroborated_escalation_vs_corroborated_retention(tmp_path):
    graph_db = tmp_path / "hygiene_90d_escalation.sqlite"
    with sqlite3.connect(graph_db) as conn:
        conn.execute(
            """CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT,
                canonical_name TEXT,
                active INTEGER DEFAULT 1
            )"""
        )
        conn.execute(
            """CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                canonical_status TEXT,
                currentness_state TEXT,
                validation_state TEXT,
                confidence_score REAL,
                superseded_by_assertion_id TEXT,
                scope_text TEXT,
                created_at TEXT,
                updated_at TEXT,
                canonical_eligible INTEGER DEFAULT 1
            )"""
        )
        conn.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT)")
        conn.execute("CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY, direct_quote TEXT, created_at TEXT)")
        conn.execute("INSERT INTO node VALUES ('person:alice', 'person', 'Alice Vance', 1)")
        conn.execute("INSERT INTO evidence_item VALUES ('ev-old', 'Initial quote 100 days ago', datetime('now', '-100 days'))")
        conn.execute("INSERT INTO evidence_item VALUES ('ev-fresh', 'Fresh corroborating quote 10 days ago', datetime('now', '-10 days'))")
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-95d-uncorroborated', 'ev-old')")
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-95d-corroborated', 'ev-old')")
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-95d-corroborated', 'ev-fresh')")

        # 1. 95-day-old grounded assertion with NO new corroborating evidence -> escalated as escalate_90d_revalidation
        conn.execute(
            """INSERT INTO assertion VALUES (
                'asrt-95d-uncorroborated', 'person:alice', 'OWNS_SCOPE', NULL, 'Legacy Billing',
                'candidate', 'current', 'needs_review', 0.88, NULL, 'Alice owns Legacy Billing',
                datetime('now', '-95 days'), datetime('now', '-95 days'), 1
            )"""
        )
        # 2. 95-day-old grounded assertion WITH fresh corroborating evidence from 10 days ago -> NOT escalated
        conn.execute(
            """INSERT INTO assertion VALUES (
                'asrt-95d-corroborated', 'person:alice', 'OWNS_SCOPE', NULL, 'Core Telemetry',
                'candidate', 'current', 'needs_review', 0.88, NULL, 'Alice owns Core Telemetry',
                datetime('now', '-95 days'), datetime('now', '-95 days'), 1
            )"""
        )
        conn.commit()

    panel = GraphHygienePanel()
    pkg = panel.run_panel(graph_db)

    archive_findings = [f for f in pkg.findings if f.proposed_fix == "archive_candidate_assertion"]
    assert len(archive_findings) == 0

    reval_findings = [f for f in pkg.findings if f.proposed_fix == "escalate_90d_revalidation"]
    assert len(reval_findings) == 1
    assert reval_findings[0].entity_references == ["asrt-95d-uncorroborated"]
    assert reval_findings[0].raw_data["recommended_option"] == "CONFIRM_OR_REMOVE"
    assert reval_findings[0].raw_data["hitl_options"] == ["CONFIRM", "REMOVE"]


def test_eight_agent_adversarial_council_clustering_and_hitl_validator():
    from dual_pipeline.hitl_governance_core.question_validator import (
        validate_question_payload,
    )

    panel = GraphHygienePanel()
    assert panel.agent_5_critic is not None
    assert panel.agent_6_lifecycle is not None
    assert panel.agent_7_parity is not None
    assert panel.agent_8_chair is not None

    mock_findings = [
        Finding(
            detective="StructuralValidator",
            severity="MEDIUM",
            description=(
                "Stale uncorroborated candidate assertion 'asrt-cand-1' (age 45.2 days) "
                "already superseded by active promoted record 'asrt-active-1'."
            ),
            entity_references=["asrt-cand-1"],
            proposed_fix="archive_candidate_assertion",
            raw_data={
                "assertion_id": "asrt-cand-1",
                "subject": "Brett Gavagni",
                "predicate": "LEADS_INITIATIVE",
                "object_val": "Cloud Migration",
                "age_days": 45.2,
                "superseded_by_assertion_id": "asrt-active-1",
            },
        ),
        Finding(
            detective="StructuralValidator",
            severity="HIGH",
            description=(
                "Near-duplicate entity candidates for type 'workstream': "
                "Workstream: Infrastructure Automation (workstream:ws-1) vs "
                "Infrastructure Automation (workstream:ws-2)."
            ),
            entity_references=["workstream:ws-1", "workstream:ws-2"],
            proposed_fix="merge_entities",
            raw_data={
                "node_id_a": "workstream:ws-1",
                "canonical_name_a": "Workstream: Infrastructure Automation",
                "node_id_b": "workstream:ws-2",
                "canonical_name_b": "Infrastructure Automation",
                "node_type_a": "workstream",
                "similarity": 0.91,
                "decision_margin": 0.62,
                "recommended_option": "MERGE_DUPLICATES",
            },
        ),
        Finding(
            detective="StructuralValidator",
            severity="HIGH",
            description=(
                "Near-duplicate entity candidates for type 'workstream': "
                "Infrastructure Automation (workstream:ws-2) vs "
                "Infrastructure Automation Proposal (workstream:ws-prop)."
            ),
            entity_references=["workstream:ws-2", "workstream:ws-prop"],
            proposed_fix="merge_entities",
            raw_data={
                "node_id_a": "workstream:ws-2",
                "canonical_name_a": "Infrastructure Automation",
                "node_id_b": "workstream:ws-prop",
                "canonical_name_b": "Infrastructure Automation Proposal",
                "node_type_a": "workstream",
                "similarity": 0.87,
                "decision_margin": 0.14,
                "recommended_option": "KEEP_SEPARATE",
            },
        ),
        Finding(
            detective="StructuralValidator",
            severity="HIGH",
            description=(
                "Near-duplicate entity candidates for type 'role': "
                "Technical lead (role:tech-lead) vs "
                "Atlas Technical Lead (role:atlas-tech-lead)."
            ),
            entity_references=["role:tech-lead", "role:atlas-tech-lead"],
            proposed_fix="merge_entities",
            raw_data={
                "node_id_a": "role:tech-lead",
                "canonical_name_a": "Technical lead",
                "node_id_b": "role:atlas-tech-lead",
                "canonical_name_b": "Atlas Technical Lead",
                "node_type_a": "role",
                "similarity": 0.86,
                "decision_margin": 0.30,
                "recommended_option": "KEEP_SEPARATE",
            },
        ),
    ]

    pkg = panel.judge.synthesize(mock_findings)
    assert pkg.model_tier == "FRONTIER_HIGH_REASONING"
    assert pkg.council_size == 13
    assert len(pkg.council_debates) == 4

    cluster_ids = [c["cluster_id"] for c in pkg.hitl_clusters]
    assert "CLUSTER-01-UNPROMOTED-STAGING-DRAFTS" in cluster_ids
    assert "CLUSTER-02C1-WORKSTREAM-PREFIXES" in cluster_ids
    assert "CLUSTER-02C2-PROPOSAL-LIFECYCLE-BOUNDARY" in cluster_ids
    assert "CLUSTER-02D2-ROLE-PROJECT-SCOPE" in cluster_ids

    for cluster in pkg.hitl_clusters:
        q_payload = cluster["ready_to_ask_question"]
        res = validate_question_payload(
            question=q_payload["question"],
            options=q_payload["options"],
        )
        assert res.is_valid, f"Cluster {cluster['cluster_id']} failed HITL validation: {res.errors}"





"""End-to-End and Adversarial Edge-Case Tests for Knowledge Graph Hygiene Intelligent Checks.

Covers:
1. Edge-to-assertion parity (auto-linkable matching assertions vs edges needing synthesized assertions).
2. Dangling edge endpoint detection (inactive or missing node endpoints).
3. Exact October unmapped-edge production regression.
4. Surfacing precomputed semantic duplicate assertions.
5. Candidate batch promotion triage for fully evidenced candidate assertions.
6. Retrieval sidecar drift detection (search_document missing active canonical assertions).
7. Adversarial edge cases: boundary payloads, malformed edge schemas, and circular/self-referential links.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dual_pipeline.graph_maintenance_core.hygiene_agent import GraphHygienePanel
from dual_pipeline.graph_scout_core import GraphScout
from dual_pipeline.graph_scout_core.scans import scan_edge_assertion_parity, scan_orphan_and_clutter


@pytest.fixture
def seeded_hygiene_graph(tmp_path: Path) -> Path:
    """Creates a hermetic SQLite graph database seeded with realistic topology, assertions, and edge parity issues."""
    graph_db = tmp_path / "test_hygiene_graph.sqlite"

    with sqlite3.connect(graph_db) as conn:
        conn.execute("PRAGMA journal_mode = WAL;")

        # Nodes
        conn.execute("""
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                node_type TEXT NOT NULL,
                active INTEGER DEFAULT 1
            )
        """)
        conn.execute("INSERT INTO node VALUES ('node-org-acme', 'Acme Corp', 'organization', 1)")
        conn.execute("INSERT INTO node VALUES ('node-person-alice', 'Alice Smith', 'person', 1)")
        conn.execute("INSERT INTO node VALUES ('node-person-bob', 'Bob Jones', 'person', 1)")
        conn.execute("INSERT INTO node VALUES ('node-art-brief', 'Executive Brief 10-14', 'artifact', 1)")
        conn.execute("INSERT INTO node VALUES ('node-inactive-charlie', 'Charlie Ghost', 'person', 0)")

        # Edges
        conn.execute("""
            CREATE TABLE edge (
                edge_id TEXT PRIMARY KEY,
                from_node_id TEXT NOT NULL,
                to_node_id TEXT NOT NULL,
                edge_type TEXT NOT NULL
            )
        """)
        # Edge 1: mapped in legacy_edge_assertion_map (healthy)
        conn.execute("INSERT INTO edge VALUES ('edge-101', 'node-person-alice', 'node-org-acme', 'EMPLOYED_BY')")
        # Edge 2: unmapped, but exact matching assertion exists in assertion table (auto-linkable)
        conn.execute("INSERT INTO edge VALUES ('edge-102', 'node-person-bob', 'node-org-acme', 'EMPLOYED_BY')")
        # Edge 3: unmapped, ANCHOR_FOR artifact edge with no backing assertion (needs synthesis - October regression)
        conn.execute("INSERT INTO edge VALUES ('edge-103', 'node-art-brief', 'node-org-acme', 'ANCHOR_FOR')")
        # Edge 4: dangling endpoint (to_node_id is inactive)
        conn.execute("INSERT INTO edge VALUES ('edge-104', 'node-person-alice', 'node-inactive-charlie', 'COLLABORATES_WITH')")
        # Edge 5: dangling endpoint (from_node_id does not exist)
        conn.execute("INSERT INTO edge VALUES ('edge-105', 'node-missing-dan', 'node-org-acme', 'AFFILIATED_WITH')")

        # Legacy edge assertion map
        conn.execute("""
            CREATE TABLE legacy_edge_assertion_map (
                edge_id TEXT PRIMARY KEY,
                assertion_id TEXT NOT NULL
            )
        """)
        conn.execute("INSERT INTO legacy_edge_assertion_map VALUES ('edge-101', 'asrt-101')")

        # Assertions
        conn.execute("""
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_node_id TEXT,
                object_literal TEXT,
                canonical_status TEXT DEFAULT 'canonical',
                validation_state TEXT DEFAULT 'accepted',
                currentness_state TEXT DEFAULT 'current',
                confidence_score REAL DEFAULT 0.95,
                retrieval_eligible INTEGER DEFAULT 1,
                created_at TEXT DEFAULT '2026-10-14T10:00:00Z'
            )
        """)
        # Backing assertion for edge-101
        conn.execute("INSERT INTO assertion VALUES ('asrt-101', 'node-person-alice', 'EMPLOYED_BY', 'node-org-acme', NULL, 'canonical', 'accepted', 'current', 0.95, 1, '2026-10-14T10:00:00Z')")
        # Matching assertion for edge-102 (so edge-102 is auto-linkable!)
        conn.execute("INSERT INTO assertion VALUES ('asrt-102', 'node-person-bob', 'EMPLOYED_BY', 'node-org-acme', NULL, 'canonical', 'accepted', 'current', 0.92, 1, '2026-10-14T10:00:00Z')")
        # Candidate assertion eligible for batch promotion
        conn.execute("INSERT INTO assertion VALUES ('asrt-cand-201', 'node-person-alice', 'HOLDS_TITLE', NULL, 'VP Engineering', 'candidate', 'accepted_legacy', 'current', 0.88, 1, '2026-10-14T10:00:00Z')")
        # Assertion missing evidence grounding
        conn.execute("INSERT INTO assertion VALUES ('asrt-ungrounded-301', 'node-person-bob', 'HAS_CLEARANCE', NULL, 'Level 5', 'canonical', 'accepted', 'current', 0.90, 1, '2026-10-14T10:00:00Z')")

        # Evidence table & items
        conn.execute("""
            CREATE TABLE assertion_evidence (
                assertion_id TEXT,
                evidence_id TEXT
            )
        """)
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-101', 'ev-1')")
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-102', 'ev-2')")
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-cand-201', 'ev-3')")
        # Notice: asrt-ungrounded-301 is missing from assertion_evidence

        conn.execute("""
            CREATE TABLE evidence_item (
                evidence_id TEXT PRIMARY KEY,
                direct_quote TEXT,
                source_id TEXT
            )
        """)
        conn.execute("INSERT INTO evidence_item VALUES ('ev-1', 'Alice is employed by Acme Corp', 'src-1')")
        conn.execute("INSERT INTO evidence_item VALUES ('ev-2', 'Bob works at Acme Corp', 'src-1')")
        conn.execute("INSERT INTO evidence_item VALUES ('ev-3', 'Alice holds title of VP Engineering', 'src-1')")

        # Search document table (retrieval sidecar)
        conn.execute("""
            CREATE TABLE search_document (
                search_document_id TEXT PRIMARY KEY,
                retrieval_eligible INTEGER DEFAULT 1,
                currentness_state TEXT DEFAULT 'current',
                content TEXT
            )
        """)
        conn.execute("INSERT INTO search_document VALUES ('asrt-101', 1, 'current', 'Alice Smith employed by Acme Corp')")
        # Notice: asrt-102 and asrt-ungrounded-301 are retrieval_eligible = 1 but missing from search_document!

        # Audit events
        conn.execute("""
            CREATE TABLE audit_event (
                audit_event_id TEXT PRIMARY KEY,
                action TEXT,
                actor_or_process TEXT,
                operator_signature TEXT,
                approval_reference TEXT
            )
        """)
        conn.execute("INSERT INTO audit_event VALUES ('evt-1', 'insert', 'pipeline', 'valid_sig_ed25519', 'APP-100')")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

    return graph_db


def test_edge_assertion_parity_scan_direct(seeded_hygiene_graph: Path):
    """Verifies that scan_edge_assertion_parity accurately categorizes all edge anomalies."""
    with sqlite3.connect(seeded_hygiene_graph) as conn:
        conn.row_factory = sqlite3.Row
        findings = scan_edge_assertion_parity(conn)

    assert len(findings) == 4

    issue_map = {f.edge_id: f for f in findings}
    assert "edge-102" in issue_map
    assert issue_map["edge-102"].issue_type == "unmapped_auto_linkable"
    assert issue_map["edge-102"].matching_assertion_id == "asrt-102"
    assert issue_map["edge-102"].action_recommended == "auto_link_edge_assertion"

    assert "edge-103" in issue_map
    assert issue_map["edge-103"].issue_type == "unmapped_needs_assertion"
    assert issue_map["edge-103"].matching_assertion_id is None
    assert issue_map["edge-103"].action_recommended == "synthesize_backing_assertion"

    assert "edge-104" in issue_map
    assert issue_map["edge-104"].issue_type == "dangling_endpoint"
    assert "to_node_id='node-inactive-charlie'" in issue_map["edge-104"].description

    assert "edge-105" in issue_map
    assert issue_map["edge-105"].issue_type == "dangling_endpoint"
    assert "from_node_id='node-missing-dan'" in issue_map["edge-105"].description


def test_orphan_node_computes_real_edge_count(seeded_hygiene_graph: Path):
    """Verifies that scan_orphan_and_clutter computes real non-zero edge counts for nodes."""
    with sqlite3.connect(seeded_hygiene_graph) as conn:
        # Add an orphan node that has an edge but no assertions
        conn.execute("INSERT INTO node VALUES ('node-orphan-with-edge', 'Orphan Edge Node', 'concept', 1)")
        conn.execute("INSERT INTO edge VALUES ('edge-orphan-1', 'node-orphan-with-edge', 'node-org-acme', 'RELATED_TO')")
        conn.row_factory = sqlite3.Row

        clutter = scan_orphan_and_clutter(conn)
        orphan_items = [c for c in clutter if c.finding_type == "orphan_node"]
        target_item = next((o for o in orphan_items if o.target_id == "node-orphan-with-edge"), None)

        assert target_item is not None
        assert target_item.details["edge_count"] == 1


def test_hygiene_panel_e2e_full_audit(seeded_hygiene_graph: Path):
    """End-to-end audit verifying that GraphHygienePanel detects edge parity, candidate promotion, and sidecar drift."""
    panel = GraphHygienePanel()
    pkg = panel.run_panel(seeded_hygiene_graph)

    assert pkg.status == "proposed"
    assert pkg.approval_required is True

    # 1. Check StructuralValidator findings
    structural_findings = [f for f in pkg.findings if f.detective == "StructuralValidator"]
    fixes = [f.proposed_fix for f in structural_findings]
    assert "auto_link_edge" in fixes
    assert "synthesize_edge_assertion" in fixes
    assert "archive_edge" in fixes

    # 2. Check ProvenanceInspector findings
    provenance_findings = [f for f in pkg.findings if f.detective == "ProvenanceInspector"]
    prov_fixes = [f.proposed_fix for f in provenance_findings]
    assert "reconcile_evidence" in prov_fixes
    assert "batch_promote_candidates" in prov_fixes
    assert "resync_search_sidecars" in prov_fixes

    # 3. Check synthesized remediation directives
    directive_actions = [d["fix"] for d in pkg.remediation_directives]
    assert "auto_link_edge" in directive_actions
    assert "synthesize_edge_assertion" in directive_actions
    assert "batch_promote_candidates" in directive_actions
    assert "resync_search_sidecars" in directive_actions


def test_adversarial_boundary_payloads(tmp_path: Path):
    """Adversarial test: empty databases, missing tables, zero-length entries, and corrupted columns."""
    empty_db = tmp_path / "empty_graph.sqlite"
    with sqlite3.connect(empty_db) as conn:
        pass  # 0 tables

    with sqlite3.connect(empty_db) as conn:
        findings = scan_edge_assertion_parity(conn)
        assert findings == []

    # Malformed table: edge table with missing to_node_id
    malformed_db = tmp_path / "malformed_graph.sqlite"
    with sqlite3.connect(malformed_db) as conn:
        conn.execute("CREATE TABLE edge (edge_id TEXT PRIMARY KEY, from_node_id TEXT)")
        conn.execute("INSERT INTO edge VALUES ('e1', 'n1')")

    with sqlite3.connect(malformed_db) as conn:
        findings = scan_edge_assertion_parity(conn)
        assert findings == []

    # Graph with self-loop edge pointing to missing node
    self_loop_db = tmp_path / "self_loop.sqlite"
    with sqlite3.connect(self_loop_db) as conn:
        conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, active INTEGER DEFAULT 1)")
        conn.execute("CREATE TABLE edge (edge_id TEXT PRIMARY KEY, from_node_id TEXT, to_node_id TEXT, edge_type TEXT)")
        conn.execute("INSERT INTO edge VALUES ('loop-1', 'ghost-node', 'ghost-node', 'SELF_REF')")

    with sqlite3.connect(self_loop_db) as conn:
        conn.row_factory = sqlite3.Row
        findings = scan_edge_assertion_parity(conn)
        assert len(findings) == 1
        assert findings[0].issue_type == "dangling_endpoint"


def test_run_knowledge_graph_hygiene_adapter_integration(tmp_path: Path, seeded_hygiene_graph: Path):
    """Verifies that run_knowledge_graph_hygiene adapter delegates to GraphHygienePanel."""
    import shutil
    from tools.scheduled_task_core.adapters_core.hygiene import run_knowledge_graph_hygiene
    from tools.scheduled_task_core.authority import AuthorityContext
    from tools.scheduled_task_core.contracts import ExecutionMode, TriggerOrigin

    fixture_root = tmp_path / "repo"
    fixture_root.mkdir(parents=True)
    shutil.copy2(seeded_hygiene_graph, fixture_root / "graph.db")
    run_dir = tmp_path / "run_001"
    run_dir.mkdir(parents=True)

    ctx = AuthorityContext(
        "knowledge_graph_hygiene",
        "run-kgh-test-01",
        TriggerOrigin.SCHEDULER_CRON,
        ExecutionMode.SCHEDULED,
        "scheduler",
    )
    result = run_knowledge_graph_hygiene(ctx, fixture_root, run_dir)

    assert result.terminal_outcome == "HITL_REQUIRED"
    assert result.findings_count > 0
    assert result.implementation_plan_path is not None
    assert result.implementation_plan_path.is_file()

    detectives = {f["detective"] for f in result.findings}
    assert "StructuralValidator" in detectives
    assert "ProvenanceInspector" in detectives

    proposed_fixes = {f["proposed_fix"] for f in result.findings}
    assert "auto_link_edge" in proposed_fixes
    assert "synthesize_edge_assertion" in proposed_fixes
    assert "batch_promote_candidates" in proposed_fixes
    assert "resync_search_sidecars" in proposed_fixes


def test_graph_hygiene_agent_specification_contract():
    """Verifies that automations/prompts/graph_hygiene_agent.md strictly enforces 16-table inventory and intelligent checks."""
    prompt_path = Path("automations/prompts/graph_hygiene_agent.md")
    assert prompt_path.is_file()
    content = prompt_path.read_text(encoding="utf-8")

    # Verify 16-table contract
    assert "across 16 tables" in content
    assert "legacy_edge_assertion_map" in content
    assert "edge" in content
    assert "search_document" in content
    assert "colbert_token" in content

    # Verify intelligent checks
    assert "auto-linkable vs needs-backing-assertion" in content
    assert "semantic duplicate assertions" in content
    assert "unpromoted high-confidence candidate assertions" in content
    assert "retrieval sidecar drift" in content


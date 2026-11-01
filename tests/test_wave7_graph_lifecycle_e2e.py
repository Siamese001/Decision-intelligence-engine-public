"""
tests/test_wave7_graph_lifecycle_e2e.py

End-to-end tests for Wave 7: Graph Lifecycle & Maintenance.
Covers:
- W7-01: Graph compaction (purging completed work queue items, WAL truncation, VACUUM)
- W7-02: Stale assertion retirement (AssertionTTLSweeper, status -> SUPERSEDED)
- W7-03: Entity merge operations (edge relinking, assertion relinking, property merging, alias tracking, FTS sync)
- W7-04: Graph export (Turtle RDF, JSON-LD, CSV formats, file output)
- W7-05: Graph versioning & time-travel querying (history triggers, as_of_timestamp evaluation)
- W7-06: Graph statistics (topological metrics, degree percentiles, orphan count, storage breakdown)
- W7-07: Orphan node cleanup (quarantine/archive, deletion, proposal emission via emit_proposals)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from dual_pipeline.graph_scout_core.models import ClutterFinding
from dual_pipeline.graph_scout_core.proposals import emit_proposals
from dual_pipeline.storage.governed_context import GovernedStorageContext
from storage.connection import create_sqlite_conn
from storage.database import (
    AssertionRecord,
    CanonicalGraphNode,
    DatabaseManager,
    GraphEdge,
)


@pytest.fixture
def temp_db_manager() -> tuple[DatabaseManager, Path]:
    tmp_dir = Path(tempfile.mkdtemp(prefix="test_w7_lifecycle_"))
    mgr = DatabaseManager(
        base_dir=tmp_dir,
        allow_unencrypted_dev_fallback=True,
    )
    return mgr, tmp_dir


class TestWave7GraphCompactionE2E:
    """W7-01: Database compaction and work queue purging."""

    def test_compaction_purges_completed_items_and_vacuums(self, temp_db_manager: tuple[DatabaseManager, Path]) -> None:
        mgr, tmp_dir = temp_db_manager

        # Insert 100 evidence and completed queue items
        with mgr.get_evidence_conn() as conn:
            with mgr.transaction(conn, db_key="evidence"):
                for i in range(100):
                    ev_id = f"ev_compaction_{i}"
                    conn.execute(
                        "INSERT INTO immutable_evidence_log (evidence_id, meeting_id, text, start_timestamp) VALUES (?, ?, ?, ?)",
                        (ev_id, "meeting_1", f"Transcript item {i}", float(i)),
                    )
                    conn.execute(
                        "INSERT INTO durable_work_queue (event_id, evidence_id, state, updated_at) VALUES (?, ?, 'COMPLETED', datetime('now', '-40 days'))",
                        (f"evt_compaction_{i}", ev_id),
                    )
                # Also insert 5 pending items that must NOT be purged
                for i in range(5):
                    ev_id = f"ev_pending_{i}"
                    conn.execute(
                        "INSERT INTO immutable_evidence_log (evidence_id, meeting_id, text, start_timestamp) VALUES (?, ?, ?, ?)",
                        (ev_id, "meeting_1", f"Pending item {i}", 1000.0 + i),
                    )
                    conn.execute(
                        "INSERT INTO durable_work_queue (event_id, evidence_id, state) VALUES (?, ?, 'PENDING')",
                        (f"evt_pending_{i}", ev_id),
                    )

        # Verify initial counts
        with mgr.get_evidence_conn(readonly=True) as conn:
            comp_count = conn.execute("SELECT count(*) as cnt FROM durable_work_queue WHERE state = 'COMPLETED'").fetchone()["cnt"]
            pend_count = conn.execute("SELECT count(*) as cnt FROM durable_work_queue WHERE state = 'PENDING'").fetchone()["cnt"]
            assert comp_count == 100
            assert pend_count == 5

        # Execute compaction with max_queue_age_days=30
        res = mgr.compact_database(vacuum=True, max_queue_age_days=30)
        assert res["status"] == "PASS"
        assert res["purged_queue_items"] == 100

        # Verify completed items purged, pending remain
        with mgr.get_evidence_conn(readonly=True) as conn:
            comp_count = conn.execute("SELECT count(*) as cnt FROM durable_work_queue WHERE state = 'COMPLETED'").fetchone()["cnt"]
            pend_count = conn.execute("SELECT count(*) as cnt FROM durable_work_queue WHERE state = 'PENDING'").fetchone()["cnt"]
            assert comp_count == 0
            assert pend_count == 5

    def test_governed_storage_context_compact(self, tmp_path: Path) -> None:
        db_file = tmp_path / "test_gov_comp.db"
        with create_sqlite_conn(db_file) as conn:
            conn.execute("CREATE TABLE t (id INT, data TEXT);")
            conn.execute("INSERT INTO t VALUES (1, 'hello');")
            conn.commit()

        res = GovernedStorageContext.compact(db_file, vacuum=True)
        assert res["status"] == "PASS"
        assert res["checkpoint"] == "TRUNCATE"
        assert res["vacuum"] is True


class TestWave7StaleAssertionRetirementE2E:
    """W7-02: Stale assertion retirement."""

    def test_stale_assertions_retired_to_superseded(self, temp_db_manager: tuple[DatabaseManager, Path]) -> None:
        mgr, _ = temp_db_manager

        # Insert a subject node first
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="node_service_x",
            evidence_id="ev_01",
            type="SYSTEM",
            name="Service X",
            strategic_value=0.8,
            confidence=1.0,
        ))

        # Insert 3 assertions: past, present, future
        mgr.upsert_assertion(AssertionRecord(
            assertion_id="assert_stale_past",
            subject_node_id="node_service_x",
            predicate="USES_FRAMEWORK",
            literal_value="Django 2.2",
            assertion_type="TECHNICAL",
            status="CONFIRMED",
            stale_after="2026-01-01",
        ))
        mgr.upsert_assertion(AssertionRecord(
            assertion_id="assert_active_future",
            subject_node_id="node_service_x",
            predicate="USES_FRAMEWORK",
            literal_value="FastAPI 0.115",
            assertion_type="TECHNICAL",
            status="CONFIRMED",
            stale_after="2027-12-31",
        ))
        mgr.upsert_assertion(AssertionRecord(
            assertion_id="assert_no_stale",
            subject_node_id="node_service_x",
            predicate="DEPLOYED_ON",
            literal_value="AWS EKS",
            assertion_type="TECHNICAL",
            status="CONFIRMED",
            stale_after=None,
        ))

        # Sweep assertions with as_of_date="2026-06-01"
        res = mgr.retire_stale_assertions(as_of_date="2026-06-01")
        assert res["status"] == "PASS"
        assert res["swept_count"] == 1
        assert res["swept_assertions"][0]["assertion_id"] == "assert_stale_past"

        # Verify in DB
        assertions = {a.assertion_id: a.status for a in mgr.query_assertions(subject_node_id="node_service_x")}
        assert assertions["assert_stale_past"] == "SUPERSEDED"
        assert assertions["assert_active_future"] == "CONFIRMED"
        assert assertions["assert_no_stale"] == "CONFIRMED"

    def test_assertion_ttl_sweeper_dry_run(self, temp_db_manager: tuple[DatabaseManager, Path]) -> None:
        mgr, _ = temp_db_manager
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="node_sub_dry",
            evidence_id="ev_dry",
            type="SYSTEM",
            name="System Dry",
        ))
        mgr.upsert_assertion(AssertionRecord(
            assertion_id="assert_dry",
            subject_node_id="node_sub_dry",
            predicate="RUNS_ON",
            literal_value="Python 3.8",
            assertion_type="TECHNICAL",
            status="CONFIRMED",
            stale_after="2025-01-01",
        ))
        mgr.upsert_assertion(AssertionRecord(
            assertion_id="assert_dry_replacement",
            subject_node_id="node_sub_dry",
            predicate="RUNS_ON",
            literal_value="Python 3.12",
            assertion_type="TECHNICAL",
            status="CONFIRMED",
            stale_after="2027-01-01",
        ))

        res = mgr.retire_stale_assertions(as_of_date="2026-01-01", dry_run=True)
        assert res["status"] == "PASS"
        assert res["swept_count"] == 1

        # In dry run, DB status must remain CONFIRMED
        assertions = {a.assertion_id: a.status for a in mgr.query_assertions(subject_node_id="node_sub_dry")}
        assert assertions["assert_dry"] == "CONFIRMED"

    def test_assertion_ttl_sweeper_retains_unsuperseded_confirmed_assertion(
        self, temp_db_manager: tuple[DatabaseManager, Path]
    ) -> None:
        mgr, _ = temp_db_manager
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="node_unsuperseded",
            evidence_id="ev_unsup",
            type="SYSTEM",
            name="System Unsuperseded",
        ))
        mgr.upsert_assertion(AssertionRecord(
            assertion_id="assert_still_only_truth",
            subject_node_id="node_unsuperseded",
            predicate="DEPLOYED_IN_REGION",
            literal_value="us-east-1",
            assertion_type="TECHNICAL",
            status="CONFIRMED",
            stale_after="2025-01-01",
        ))

        res = mgr.retire_stale_assertions(as_of_date="2026-06-01", dry_run=False)
        assert res["status"] == "PASS"
        assert res["swept_count"] == 0
        assert "assert_still_only_truth" in res["retained_unsuperseded_ids"]

        assertions = {a.assertion_id: a.status for a in mgr.query_assertions(subject_node_id="node_unsuperseded")}
        assert assertions["assert_still_only_truth"] == "CONFIRMED"


class TestWave7EntityMergeE2E:
    """W7-03: Entity merge operations."""

    def test_merge_nodes_relinks_edges_assertions_and_merges_properties(self, temp_db_manager: tuple[DatabaseManager, Path]) -> None:
        mgr, _ = temp_db_manager

        # Create 4 nodes: surviving, absorbed, target_c, source_d
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="node_surviving",
            evidence_id="ev_1",
            type="SERVICE",
            name="Payment Service",
            properties={"tier": "tier_1", "language": "Go"},
            strategic_value=0.9,
        ))
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="node_absorbed",
            evidence_id="ev_2",
            type="SERVICE",
            name="Payment Gateway V1",
            properties={"legacy_id": "pg-001", "language": "Go", "region": "us-east-1"},
            strategic_value=0.7,
        ))
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="node_c",
            evidence_id="ev_3",
            type="DATABASE",
            name="Postgres DB",
        ))
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="node_d",
            evidence_id="ev_4",
            type="CLIENT",
            name="Mobile App",
        ))

        # Create edges:
        # absorbed -> node_c
        mgr.upsert_canonical_edge(GraphEdge(
            edge_id="edge_abs_to_c",
            from_node_id="node_absorbed",
            predicate="CONNECTS_TO",
            to_node_id="node_c",
        ))
        # node_d -> absorbed
        mgr.upsert_canonical_edge(GraphEdge(
            edge_id="edge_d_to_abs",
            from_node_id="node_d",
            predicate="CALLS",
            to_node_id="node_absorbed",
        ))
        # Self-edge between surviving and absorbed
        mgr.upsert_canonical_edge(GraphEdge(
            edge_id="edge_surv_abs",
            from_node_id="node_surviving",
            predicate="SUPERSEDES",
            to_node_id="node_absorbed",
        ))

        # Create assertions referencing absorbed node
        mgr.upsert_assertion(AssertionRecord(
            assertion_id="assert_abs_sub",
            subject_node_id="node_absorbed",
            predicate="SUPPORTS_SLA",
            literal_value="99.9%",
            assertion_type="TECHNICAL",
        ))
        mgr.upsert_assertion(AssertionRecord(
            assertion_id="assert_abs_obj",
            subject_node_id="node_d",
            predicate="DEPENDS_ON",
            object_node_id="node_absorbed",
            assertion_type="DEPENDENCY",
        ))

        # Perform merge
        merge_res = mgr.merge_nodes("node_surviving", "node_absorbed", reason="Service consolidation")
        assert merge_res["status"] == "PASS"
        assert merge_res["surviving_id"] == "node_surviving"
        assert merge_res["absorbed_id"] == "node_absorbed"
        assert merge_res["edges_relinked"] == 2
        assert merge_res["assertions_relinked"] == 2

        # 1. Absorbed node must no longer exist in canonical_graph_nodes
        assert mgr.get_canonical_node("node_absorbed") is None

        # 2. Surviving node properties merged and alias recorded
        surv = mgr.get_canonical_node("node_surviving")
        assert surv is not None
        assert surv.properties["tier"] == "tier_1"
        assert surv.properties["legacy_id"] == "pg-001"
        assert surv.properties["region"] == "us-east-1"
        assert "Payment Gateway V1" in surv.properties["aliases"]
        assert len(surv.properties["merged_nodes"]) == 1
        assert surv.properties["merged_nodes"][0]["absorbed_id"] == "node_absorbed"

        # 3. Edges re-linked
        edges = mgr.query_canonical_edges(from_node_id="node_surviving")
        edge_targets = [e.to_node_id for e in edges]
        assert "node_c" in edge_targets

        edges_in = mgr.query_canonical_edges(to_node_id="node_surviving")
        edge_sources = [e.from_node_id for e in edges_in]
        assert "node_d" in edge_sources

        # 4. Assertions re-linked
        assert_sub = mgr.query_assertions(subject_node_id="node_surviving")
        assert any(a.assertion_id == "assert_abs_sub" for a in assert_sub)

        assert_obj = [a for a in mgr.query_assertions(subject_node_id="node_d") if a.object_node_id == "node_surviving"]
        assert len(assert_obj) == 1
        assert assert_obj[0].assertion_id == "assert_abs_obj"

        # 5. FTS5 index returns surviving node when searching for absorbed name
        fts_hits = mgr.search_canonical_nodes_fts("Gateway")
        assert any(h.node_id == "node_surviving" for h in fts_hits)


class TestWave7GraphExportE2E:
    """W7-04: Graph export in Turtle, JSON-LD, and CSV."""

    def test_export_turtle_jsonld_csv(self, temp_db_manager: tuple[DatabaseManager, Path]) -> None:
        mgr, tmp_dir = temp_db_manager

        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="sys_order",
            evidence_id="ev_export_1",
            type="SYSTEM",
            name="Order Management",
            strategic_value=0.85,
            confidence=0.95,
        ))
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="sys_inventory",
            evidence_id="ev_export_2",
            type="SYSTEM",
            name="Inventory Service",
            strategic_value=0.75,
            confidence=0.90,
        ))
        mgr.upsert_canonical_edge(GraphEdge(
            edge_id="edge_order_inv",
            from_node_id="sys_order",
            predicate="QUERIES",
            to_node_id="sys_inventory",
            weight=1.0,
            confidence=0.95,
        ))
        mgr.upsert_assertion(AssertionRecord(
            assertion_id="assert_order_sla",
            subject_node_id="sys_order",
            predicate="COMPLIES_WITH",
            literal_value="SOC2 Type II",
            assertion_type="STRATEGIC",
            status="CONFIRMED",
        ))

        # 1. Turtle export
        ttl_path = tmp_dir / "export.ttl"
        ttl_content = mgr.export_graph(format="turtle", output_path=ttl_path)
        assert ttl_path.is_file()
        assert "@prefix nee: <urn:nee:graph:> ." in ttl_content
        assert "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> ." in ttl_content
        assert "<urn:nee:node:sys_order> a nee:SYSTEM ;" in ttl_content
        assert 'rdfs:label "Order Management" ;' in ttl_content
        assert "<urn:nee:node:sys_order> nee:QUERIES <urn:nee:node:sys_inventory> ." in ttl_content

        # 2. JSON-LD export
        jsonld_path = tmp_dir / "export.jsonld"
        jsonld_content = mgr.export_graph(format="json-ld", output_path=jsonld_path)
        assert jsonld_path.is_file()
        doc = json.loads(jsonld_content)
        assert "@context" in doc
        assert "@graph" in doc
        graph_ids = [item["@id"] for item in doc["@graph"]]
        assert "urn:nee:node:sys_order" in graph_ids
        assert "urn:nee:edge:edge_order_inv" in graph_ids
        assert "urn:nee:assertion:assert_order_sla" in graph_ids

        # 3. CSV export
        csv_path = tmp_dir / "export.csv"
        csv_content = mgr.export_graph(format="csv", output_path=csv_path)
        assert csv_path.is_file()
        assert "# SECTION: NODES" in csv_content
        assert "sys_order,SYSTEM,Order Management" in csv_content
        assert "# SECTION: EDGES" in csv_content
        assert "edge_order_inv,sys_order,QUERIES,sys_inventory" in csv_content
        assert "# SECTION: ASSERTIONS" in csv_content
        assert "assert_order_sla,sys_order,COMPLIES_WITH" in csv_content


class TestWave7GraphVersioningTimeTravelE2E:
    """W7-05: Graph versioning and time-travel querying."""

    def test_time_travel_evaluates_pre_update_and_post_update_state(self, temp_db_manager: tuple[DatabaseManager, Path]) -> None:
        mgr, _ = temp_db_manager

        # Insert node at T0
        node = CanonicalGraphNode(
            node_id="node_versioned",
            evidence_id="ev_t0",
            type="SERVICE",
            name="Version 1 Service",
            properties={"v": 1},
            strategic_value=0.5,
            created_at="2026-01-01 10:00:00",
            updated_at="2026-01-01 10:00:00",
        )
        mgr.upsert_canonical_node(node)

        # Update node at T2
        node_v2 = CanonicalGraphNode(
            node_id="node_versioned",
            evidence_id="ev_t2",
            type="SERVICE",
            name="Version 2 Service",
            properties={"v": 2},
            strategic_value=0.95,
            created_at="2026-01-01 10:00:00",
            updated_at="2026-02-01 10:00:00",
        )
        mgr.upsert_canonical_node(node_v2)

        with mgr.get_canonical_conn() as conn:
            # History trigger recorded V1 in canonical_graph_nodes_history with operation='UPDATE'
            # Set recorded_at of history to match update time T2 (2026-02-01)
            conn.execute(
                "UPDATE canonical_graph_nodes_history SET recorded_at = '2026-02-01 10:00:00' WHERE node_id = 'node_versioned'"
            )
            conn.commit()

        # 1. Query as of T_before_creation (2025-12-31) -> None
        assert mgr.get_canonical_node("node_versioned", as_of_timestamp="2025-12-31 23:59:59") is None

        # 2. Query as of T1 (2026-01-15) -> returns V1 state
        n_t1 = mgr.get_canonical_node("node_versioned", as_of_timestamp="2026-01-15 12:00:00")
        assert n_t1 is not None
        assert n_t1.name == "Version 1 Service"
        assert n_t1.properties["v"] == 1
        assert n_t1.strategic_value == 0.5

        # 3. Query as of T3 (2026-02-15) -> returns V2 state
        n_t3 = mgr.get_canonical_node("node_versioned", as_of_timestamp="2026-02-15 12:00:00")
        assert n_t3 is not None
        assert n_t3.name == "Version 2 Service"
        assert n_t3.properties["v"] == 2
        assert n_t3.strategic_value == 0.95

        # 4. Query current (no timestamp) -> returns V2 state
        n_curr = mgr.get_canonical_node("node_versioned")
        assert n_curr is not None
        assert n_curr.name == "Version 2 Service"

        # 5. Test query_canonical_nodes with time-travel
        nodes_t1 = mgr.query_canonical_nodes(as_of_timestamp="2026-01-15 12:00:00")
        assert len(nodes_t1) == 1
        assert nodes_t1[0].name == "Version 1 Service"


class TestWave7GraphStatisticsE2E:
    """W7-06: Graph statistics CLI & engine."""

    def test_get_graph_statistics(self, temp_db_manager: tuple[DatabaseManager, Path]) -> None:
        mgr, _ = temp_db_manager

        # Insert 3 nodes: 2 connected, 1 orphan
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="n1", evidence_id="e1", type="PERSON", name="Alice", strategic_value=0.8
        ))
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="n2", evidence_id="e2", type="PERSON", name="Bob", strategic_value=0.7
        ))
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="n_orphan", evidence_id="e3", type="SYSTEM", name="Orphan System", strategic_value=0.2
        ))

        # Edge between n1 and n2
        mgr.upsert_canonical_edge(GraphEdge(
            edge_id="e_12", from_node_id="n1", predicate="COLLABORATES_WITH", to_node_id="n2"
        ))

        # Assertion for n1
        mgr.upsert_assertion(AssertionRecord(
            assertion_id="a1", subject_node_id="n1", predicate="ROLE", literal_value="Lead Architect", assertion_type="STRATEGIC"
        ))

        stats = mgr.get_graph_statistics()
        assert stats["status"] == "PASS"
        assert stats["total_nodes"] == 3
        assert stats["nodes_by_type"] == {"PERSON": 2, "SYSTEM": 1}
        assert stats["total_edges"] == 1
        assert stats["edges_by_predicate"] == {"COLLABORATES_WITH": 1}
        assert stats["total_assertions"] == 1
        assert stats["orphan_count"] == 1
        assert stats["degree_percentiles"]["max"] == 1
        assert stats["degree_percentiles"]["min"] == 0
        assert "canonical_graph_db" in stats["storage_breakdown_bytes"]


class TestWave7OrphanCleanupE2E:
    """W7-07: Orphan node detection, proposal generation, and cleanup."""

    def test_cleanup_orphan_nodes_and_proposals(self, temp_db_manager: tuple[DatabaseManager, Path], tmp_path: Path) -> None:
        mgr, _ = temp_db_manager

        # Create connected nodes and orphan node
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="connected_1", evidence_id="ev_c1", type="SERVICE", name="Service 1"
        ))
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="connected_2", evidence_id="ev_c2", type="SERVICE", name="Service 2"
        ))
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="orphan_target", evidence_id="ev_o", type="DOCUMENT", name="Dangling Doc"
        ))

        mgr.upsert_canonical_edge(GraphEdge(
            edge_id="edge_c1_c2", from_node_id="connected_1", predicate="DEPENDS_ON", to_node_id="connected_2"
        ))

        # 1. Archive orphan node
        archived = mgr.cleanup_orphan_nodes(archive=True)
        assert archived == ["orphan_target"]

        orphan_node = mgr.get_canonical_node("orphan_target")
        assert orphan_node is not None
        assert orphan_node.is_quarantined is True
        assert orphan_node.quarantine_reason == "ORPHAN_CLEANUP_ARCHIVED"

        # 2. Hard delete orphan node
        deleted = mgr.cleanup_orphan_nodes(archive=False)
        assert deleted == ["orphan_target"]
        assert mgr.get_canonical_node("orphan_target") is None

        # 3. Test emit_proposals generates valid proposal for orphan clutter finding
        clutter = [
            ClutterFinding(
                finding_type="orphan_node",
                target_id="orphan_node_42",
                label="Unlinked Specification",
                details={"node_id": "orphan_node_42", "node_type": "DOCUMENT"},
                action_recommended="graph_gc_archive_or_link",
            )
        ]
        proposals = emit_proposals(
            duplicate_findings=[],
            contradiction_findings=[],
            output_dir=tmp_path,
            run_id="run_orphan_test_01",
            clutter_findings=clutter,
        )
        assert len(proposals) == 1
        prop = proposals[0]
        assert prop["proposal_type"] == "workflow"
        assert prop["status"] == "proposed"
        assert prop["extensions"]["com.partner_advisory.nee.calibration"]["target_parameter"] == "archive_orphan:orphan_node_42"
        assert prop["extensions"]["com.partner_advisory.nee.calibration"]["proposed_value"] == "QUARANTINED_ORPHAN"


class TestWave7CLIE2E:
    """CLI subcommand tests for graph-export and graph-stats."""

    def test_cli_graph_stats_json_and_markdown(self, temp_db_manager: tuple[DatabaseManager, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        mgr, tmp_dir = temp_db_manager
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="cli_node_1", evidence_id="ev_cli", type="SYSTEM", name="CLI Node", strategic_value=0.8
        ))

        from dual_pipeline.cli.engine import execute_command
        from dual_pipeline.cli.parser import build_parser

        parser = build_parser()

        # 1. Test json format
        args_json = parser.parse_args(["graph-stats", "--runtime-root", str(tmp_dir), "--format", "json"])
        res_json = execute_command(args_json)
        assert res_json["status"] == "PASS"
        assert res_json["command"] == "graph-stats"
        assert res_json["statistics"]["total_nodes"] == 1

        # 2. Test markdown format
        args_md = parser.parse_args(["graph-stats", "--runtime-root", str(tmp_dir), "--format", "markdown"])
        res_md = execute_command(args_md)
        assert res_md["status"] == "PASS"
        captured = capsys.readouterr().out
        assert "# Canonical Knowledge Graph Statistics" in captured

    def test_cli_graph_export_turtle_and_csv(self, temp_db_manager: tuple[DatabaseManager, Path], tmp_path: Path) -> None:
        mgr, tmp_dir = temp_db_manager
        mgr.upsert_canonical_node(CanonicalGraphNode(
            node_id="cli_exp_1", evidence_id="ev_exp", type="SYSTEM", name="Export Node", strategic_value=0.7
        ))

        from dual_pipeline.cli.engine import execute_command
        from dual_pipeline.cli.parser import build_parser

        parser = build_parser()

        # Export to file
        out_file = tmp_path / "cli_exported.ttl"
        args = parser.parse_args(["graph-export", "--runtime-root", str(tmp_dir), "--format", "turtle", "--output", str(out_file)])
        res = execute_command(args)
        assert res["status"] == "PASS"
        assert res["command"] == "graph-export"
        assert out_file.is_file()
        assert "@prefix nee:" in out_file.read_text(encoding="utf-8")

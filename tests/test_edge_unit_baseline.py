"""
tests/test_edge_unit_baseline.py

Unit test suite for the edge & semantic-unit forensic baseline audit tool.
Verifies metrics calculation, PIN_DRIFT detection, and zero-mutation guarantees.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import (
    sha256_file,
    write_json,
)
from tools.audit_edge_unit_baseline import run_edge_unit_baseline_audit

ROOT = Path(__file__).resolve().parent.parent


def create_mock_runtime(
    base_dir: Path,
    *,
    drift_graph: bool = False,
) -> dict[str, str]:
    """Create a minimal mock dual_pipeline runtime environment."""
    runtime_root = base_dir / ".local" / "dual_pipeline"
    gen_id = "generation-mock-001"
    gen_dir = runtime_root / "generations" / gen_id
    gen_dir.mkdir(parents=True)

    graph_path = gen_dir / "graph.sqlite"
    sidecar_path = gen_dir / "sidecar.sqlite"

    # Create mock graph
    g_conn = sqlite3.connect(graph_path)
    g_conn.execute("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT,
            canonical_name TEXT,
            display_name TEXT,
            description TEXT,
            status TEXT,
            active INTEGER
        )
    """)
    g_conn.execute("""
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            assertion_type TEXT,
            currentness_state TEXT,
            origin_type TEXT,
            extraction_method TEXT,
            validation_state TEXT,
            canonical_status TEXT
        )
    """)
    g_conn.execute("""
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            direct_quote TEXT
        )
    """)
    g_conn.execute("""
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT,
            evidence_relationship TEXT
        )
    """)

    # Populate mock data
    g_conn.execute("INSERT INTO node VALUES ('node_1', 'stakeholder', 'Alice', 'Alice', 'Primary engineer', 'active', 1)")
    g_conn.execute("INSERT INTO node VALUES ('node_2', 'org', 'Acme', 'Acme', 'Parent org', 'active', 1)")

    # 1 edge assertion with quote
    g_conn.execute("INSERT INTO assertion VALUES ('a1', 'node_1', 'AFFILIATED_WITH', 'node_2', NULL, 'confirmed_canonical_fact', 'current', 'legacy_migration', 'deterministic_legacy_edge_backfill', 'accepted_legacy', 'canonical')")
    # 1 literal assertion with quote
    g_conn.execute("INSERT INTO assertion VALUES ('a2', 'node_1', 'HAS_CLAIM_TEXT', NULL, 'Alice designs systems', 'extracted_claim', 'current', 'human_source', 'dual_pipeline_adapter', 'approved', 'canonical')")

    g_conn.execute("INSERT INTO evidence_item VALUES ('e1', 'Alice is affiliated with Acme')")
    g_conn.execute("INSERT INTO evidence_item VALUES ('e2', 'Alice designs systems reliably')")
    g_conn.execute("INSERT INTO assertion_evidence VALUES ('a1', 'e1', 'supports')")
    g_conn.execute("INSERT INTO assertion_evidence VALUES ('a2', 'e2', 'supports')")

    g_conn.commit()
    g_conn.close()

    # Create mock sidecar
    s_conn = sqlite3.connect(sidecar_path)
    s_conn.execute("""
        CREATE TABLE retrieval_record (
            record_id TEXT PRIMARY KEY,
            lane TEXT,
            record_type TEXT,
            retrieval_kind TEXT,
            artifact_json TEXT
        )
    """)
    s_conn.execute("INSERT INTO retrieval_record VALUES ('r1', 'approved', 'edge', 'relationship_assertion', '{}')")
    s_conn.execute("INSERT INTO retrieval_record VALUES ('r2', 'approved', 'entity_facet', 'entity_facet', '{}')")
    s_conn.commit()
    s_conn.close()

    real_graph_sha = sha256_file(graph_path)
    real_sidecar_sha = sha256_file(sidecar_path)

    pinned_graph_sha = "0" * 64 if drift_graph else real_graph_sha
    active_pair = {
        "generation_id": gen_id,
        "graph_path": f"generations/{gen_id}/graph.sqlite",
        "sidecar_path": f"generations/{gen_id}/sidecar.sqlite",
        "graph_sha256": pinned_graph_sha,
        "sidecar_sha256": real_sidecar_sha,
    }
    write_json(runtime_root / "active_pair.json", active_pair)

    return {
        "runtime_root": str(runtime_root),
        "graph_path": str(graph_path),
        "sidecar_path": str(sidecar_path),
        "pinned_graph_sha": pinned_graph_sha,
        "real_graph_sha": real_graph_sha,
    }


class TestEdgeUnitBaselineAudit(unittest.TestCase):
    """Unit tests for edge unit baseline audit execution."""

    def test_mock_audit_pass_when_hashes_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            info = create_mock_runtime(Path(tmp_dir), drift_graph=False)
            receipt = run_edge_unit_baseline_audit(
                Path(info["runtime_root"]),
                show_progress=False,
            )

            self.assertEqual(receipt["status"], "PASS")
            self.assertTrue(receipt["hash_verification"]["graph_matches_pin"])
            self.assertTrue(receipt["hash_verification"]["sidecar_matches_pin"])
            self.assertTrue(receipt["hash_verification"]["zero_mutation_verified"])
            self.assertIn("canonical_digest", receipt)

            # Assertion metrics check
            am = receipt["assertion_metrics"]
            self.assertEqual(am["total_assertions"], 2)
            self.assertEqual(am["current_unknown_assertions"], 2)
            self.assertEqual(am["edge_shaped_assertions"], 1)
            self.assertEqual(am["literal_shaped_assertions"], 1)
            self.assertEqual(am["legacy_edge_backfill_count"], 1)
            self.assertEqual(am["edge_shaped_with_quote"], 1)

            # Node metrics check
            nm = receipt["node_metrics"]
            self.assertEqual(nm["total_nodes"], 2)
            self.assertEqual(nm["empty_description_count"], 0)

            # Sidecar metrics check
            sm = receipt["sidecar_metrics"]
            self.assertEqual(sm["total_records"], 2)
            self.assertEqual(sm["edge_like_sidecar_count"], 1)

    def test_mock_audit_detects_pin_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            info = create_mock_runtime(Path(tmp_dir), drift_graph=True)
            receipt = run_edge_unit_baseline_audit(
                Path(info["runtime_root"]),
                show_progress=False,
            )

            self.assertEqual(receipt["status"], "PIN_DRIFT")
            self.assertFalse(receipt["hash_verification"]["graph_matches_pin"])
            self.assertTrue(receipt["hash_verification"]["sidecar_matches_pin"])
            self.assertEqual(
                receipt["hash_verification"]["pinned_graph_sha256"],
                "0" * 64,
            )
            self.assertEqual(
                receipt["hash_verification"]["live_graph_sha256"],
                info["real_graph_sha"],
            )

    def test_live_audit_reproduces_baseline_and_detects_drift(self) -> None:
        live_runtime = ROOT / ".local" / "dual_pipeline"
        if not (live_runtime / "active_pair.json").is_file():
            self.skipTest(".local/dual_pipeline/active_pair.json not present")

        receipt = run_edge_unit_baseline_audit(live_runtime, show_progress=False)

        # Baseline ground truth assertions
        self.assertIn(receipt["status"], ("PASS", "PIN_DRIFT"))
        self.assertTrue(receipt["hash_verification"]["sidecar_matches_pin"])
        self.assertTrue(receipt["hash_verification"]["zero_mutation_verified"])

        am = receipt["assertion_metrics"]
        self.assertEqual(am["total_assertions"], 2579)
        self.assertEqual(am["current_unknown_assertions"], 2252)
        self.assertEqual(am["edge_shaped_assertions"], 1078)
        self.assertEqual(am["literal_shaped_assertions"], 1174)
        self.assertEqual(am["legacy_edge_backfill_count"], 907)
        self.assertEqual(am["edge_shaped_with_quote"], 937)

        nm = receipt["node_metrics"]
        self.assertEqual(nm["total_nodes"], 799)
        self.assertEqual(nm["empty_description_count"], 0)

        sm = receipt["sidecar_metrics"]
        self.assertEqual(sm["total_records"], 1282)
        self.assertEqual(sm["edge_like_sidecar_count"], 640)


if __name__ == "__main__":
    unittest.main()

"""
tests/end_to_end/test_batch_offloading_and_sidecar_delta_e2e.py

End-to-end integration and lifecycle verification for Wave 4 cost reduction:
1. Asynchronous OpenAI Batch API offloading lifecycle (request packaging, JSONL serialization,
   pending batch recording, mock collection, artifact materialization, 50% discount accounting).
2. Incremental BGE-M3 sidecar delta reuse (cold generation, zero-re-encoding incremental inheritance,
   partial mutation delta handling, zero-copy SQLite vector copying, schema integrity verification).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

from dual_pipeline.batch_core.adapter import (
    create_batch_request,
    load_pending_batch,
    parse_batch_jsonl,
    record_pending_batch,
    serialize_synthesis_prompts,
)
from dual_pipeline.batch_core.collector import (
    collect_and_materialize_batch,
)
from dual_pipeline.cli.batch_cli import _backtest_collect
from dual_pipeline.cost_tracker import RunCostTracker
from dual_pipeline.index_core.constants import DENSE_DIMENSIONS, SIDECAR_SCHEMA
from dual_pipeline.index_core.differential import (
    populate_sidecar_vectors,
)


class _DeterministicTrackingBackend:
    """Mock embedding backend producing valid 1024-dim dense and ColBERT vectors for unit/e2e testing."""

    def __init__(self) -> None:
        self.backend_id = "mock-bge-m3-e2e"
        self.model_id = "BAAI/bge-m3"
        self.model_revision = "e2e-rev-40"
        self.model_manifest_digest = "e2e-digest-64"
        self.production_qualified = False
        self.assurance_tier = "development"
        self.supports_batch_encoding = False
        self.encode_calls = 0

    def encode(self, text: str) -> Dict[str, Any]:
        self.encode_calls += 1
        # Generate deterministic float vector of length DENSE_DIMENSIONS
        dense = [0.001 * ((i % 100) + 1) for i in range(DENSE_DIMENSIONS)]
        sparse = {"token_0": 0.85, "token_1": 0.42}
        colbert_vec = [0.01 * (j + 1) for j in range(128)]
        return {
            "dense": dense,
            "sparse": sparse,
            "colbert": [colbert_vec],
            "colbert_tokens": ["token_0"],
        }


class TestBatchOffloadingLifecycleE2E(unittest.TestCase):
    """E2E verification of the asynchronous Batch API workflow."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="nee-batch-e2e-")
        self.tmp_path = Path(self.temp_dir.name)
        self.meeting_dir = self.tmp_path / "output" / "project_nexus" / "10-03-2026" / "Batch_Run"
        self.meeting_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_batch_lifecycle_serialize_record_and_collect(self) -> None:
        """Full lifecycle: create requests -> serialize JSONL -> record metadata -> collect results."""
        req1 = create_batch_request(
            custom_id="recap",
            body={
                "model": "gpt-6.1-sol",
                "messages": [{"role": "user", "content": "Generate executive recap from transcript."}],
            },
        )
        req2 = create_batch_request(
            custom_id="graph",
            body={
                "model": "gpt-6.1-sol",
                "messages": [{"role": "user", "content": "Extract entity and relationship graph signals."}],
            },
        )
        jsonl_path = serialize_synthesis_prompts(self.meeting_dir, [req1, req2])
        self.assertTrue(jsonl_path.is_file())

        parsed_requests = parse_batch_jsonl(jsonl_path.read_text(encoding="utf-8"))
        self.assertEqual(len(parsed_requests), 2)
        custom_ids = {r["custom_id"] for r in parsed_requests}
        self.assertEqual(custom_ids, {"recap", "graph"})

        # Record pending batch metadata in meeting directory
        batch_meta_file = record_pending_batch(
            meeting_dir=self.meeting_dir,
            batch_info={
                "batch_id": "batch_mock_e2e_12345",
                "meeting": "Batch_Run",
                "project": "project_nexus",
                "model": "gpt-6.1-sol",
            },
        )
        self.assertTrue(batch_meta_file.is_file())

        loaded_meta = load_pending_batch(self.meeting_dir)
        self.assertIsNotNone(loaded_meta)
        self.assertEqual(loaded_meta.get("latest_batch_id"), "batch_mock_e2e_12345")

        # Prepare batch_results.jsonl in meeting directory
        mock_output_content = (
            json.dumps({
                "id": "batch_req_01",
                "custom_id": "02_recap",
                "response": {
                    "status_code": 200,
                    "body": {
                        "choices": [{
                            "message": {
                                "content": "# Executive Recap\n\n- Key decision made.\n- Next steps assigned."
                            }
                        }],
                        "usage": {"prompt_tokens": 1200, "completion_tokens": 300},
                    },
                },
            }) + "\n" +
            json.dumps({
                "id": "batch_req_02",
                "custom_id": "03_graph",
                "response": {
                    "status_code": 200,
                    "body": {
                        "choices": [{
                            "message": {
                                "content": json.dumps({
                                    "project": "project_nexus",
                                    "entities": [{"id": "person:alice", "kind": "person", "label": "Alice"}],
                                    "signals": [],
                                })
                            }
                        }],
                        "usage": {"prompt_tokens": 1800, "completion_tokens": 400},
                    },
                },
            }) + "\n"
        )
        (self.meeting_dir / "batch_results.jsonl").write_text(mock_output_content, encoding="utf-8")

        # Collect and materialize outputs into the meeting directory
        results = collect_and_materialize_batch(
            meeting_dir=self.meeting_dir,
        )
        self.assertEqual(results.get("status"), "PASS")
        self.assertTrue(results.get("deliverables_found", {}).get("output_2"))
        self.assertTrue(results.get("deliverables_found", {}).get("output_3"))

        # Check materialized files on disk
        recap_files = list(self.meeting_dir.glob("02_*_recap*.md"))
        self.assertTrue(len(recap_files) > 0, "Recap markdown must be materialized")
        recap_text = recap_files[0].read_text(encoding="utf-8")
        self.assertIn("Executive Recap", recap_text)

        graph_files = list(self.meeting_dir.glob("03_*_canonical_signal_projection.json"))
        self.assertTrue(len(graph_files) > 0, "Graph JSON must be materialized")
        graph_text = graph_files[0].read_text(encoding="utf-8")
        self.assertIn("person:alice", graph_text)

    def test_cost_tracker_batch_discount_accounting(self) -> None:
        """Verify RunCostTracker applies the exact 50% discount on batch calls."""
        tracker_sync = RunCostTracker(max_cost_usd=50.0)
        tracker_sync.record_call(
            model="gpt-6.1-sol",
            prompt_tokens=10000,
            completion_tokens=2000,
            is_batch=False,
        )
        sync_cost = tracker_sync.total_cost_usd

        tracker_batch = RunCostTracker(max_cost_usd=50.0)
        tracker_batch.record_call(
            model="gpt-6.1-sol",
            prompt_tokens=10000,
            completion_tokens=2000,
            is_batch=True,
        )
        batch_cost = tracker_batch.total_cost_usd

        self.assertGreater(sync_cost, 0.0)
        self.assertAlmostEqual(batch_cost, sync_cost * 0.5, places=5)

    def test_backtest_collect_cli_handler(self) -> None:
        """Verify _backtest_collect handler runs in dry-run mode without crashing."""
        record_pending_batch(
            meeting_dir=self.meeting_dir,
            batch_info={"batch_id": "batch_test_cli_456", "source": "test"},
        )
        mock_output = json.dumps({
            "id": "b1",
            "custom_id": "02_recap",
            "response": {
                "status_code": 200,
                "body": {
                    "choices": [{"message": {"content": "Sample Recap"}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50},
                },
            },
        }) + "\n"
        (self.meeting_dir / "batch_results.jsonl").write_text(mock_output, encoding="utf-8")

        args = argparse.Namespace(
            meeting_dir=self.meeting_dir,
            dry_run=True,
        )
        res = _backtest_collect(args)
        self.assertIsInstance(res, dict)
        self.assertEqual(res.get("status"), "PASS")
        self.assertTrue(res.get("dry_run"))


class TestSidecarDeltaReuseE2E(unittest.TestCase):
    """E2E verification of incremental BGE-M3 sidecar inheritance and vector reuse."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="nee-sidecar-e2e-")
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _init_sidecar_db(self, path: Path, backend: Any, records: List[Dict[str, Any]]) -> sqlite3.Connection:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.executescript(SIDECAR_SCHEMA)
        # Populate metadata table
        conn.execute("INSERT INTO metadata VALUES ('embedding_backend', ?)", (backend.backend_id,))
        conn.execute("INSERT INTO metadata VALUES ('model_id', ?)", (backend.model_id,))
        conn.execute("INSERT INTO metadata VALUES ('model_revision', ?)", (backend.model_revision,))
        conn.execute("INSERT INTO metadata VALUES ('colbert_dimensions', '128')")
        conn.execute("INSERT INTO metadata VALUES ('dense_dimensions', '1024')")
        conn.execute("INSERT INTO metadata VALUES ('generation_id', 'gen-test-01')")
        conn.execute("INSERT INTO metadata VALUES ('graph_snapshot', 'snap-test-01')")

        # Populate retrieval_record table with valid approved lane and entity_facet kind
        conn.executemany(
            """INSERT INTO retrieval_record
               (record_id, lane, record_type, namespace, retrieval_text_hash,
                retrieval_kind, epistemic_status, provenance_kind, answer_use, artifact_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    r["record_id"],
                    r.get("lane", "approved"),
                    r.get("record_type", "entity"),
                    r.get("namespace", "project_nexus"),
                    r["retrieval_text_hash"],
                    "entity_facet",
                    "approved",
                    "source_bound",
                    "factual_answer",
                    json.dumps(r),
                )
                for r in records
            ],
        )
        conn.commit()
        return conn

    def test_sidecar_differential_inheritance_and_delta_isolation(self) -> None:
        """
        Verify:
        1. Cold sidecar: encodes all records (reused=0, delta=N).
        2. Incremental identical sidecar: 100% vector reuse, zero new encode calls.
        3. Incremental modified sidecar: only net-new record triggers inference.
        """
        backend_1 = _DeterministicTrackingBackend()
        records_base = [
            {"record_id": f"rec_{i}", "retrieval_text": f"Entity content text for {i}", "retrieval_text_hash": f"hash_{i}"}
            for i in range(5)
        ]

        # 1. Build initial sidecar (cold)
        sidecar_1_path = self.tmp_path / "gen1" / "sidecar.sqlite"
        conn1 = self._init_sidecar_db(sidecar_1_path, backend_1, records_base)

        total_1, colbert_dim_1, reused_1, delta_1 = populate_sidecar_vectors(
            connection=conn1,
            records=records_base,
            backend=backend_1,
            prior_sidecar_path=None,
        )
        conn1.commit()
        conn1.close()

        self.assertEqual(total_1, 5)
        self.assertEqual(reused_1, 0)
        self.assertEqual(delta_1, 5)
        self.assertEqual(backend_1.encode_calls, 5)

        # 2. Build incremental sidecar against identical records
        backend_2 = _DeterministicTrackingBackend()
        sidecar_2_path = self.tmp_path / "gen2" / "sidecar.sqlite"
        conn2 = self._init_sidecar_db(sidecar_2_path, backend_2, records_base)

        total_2, colbert_dim_2, reused_2, delta_2 = populate_sidecar_vectors(
            connection=conn2,
            records=records_base,
            backend=backend_2,
            prior_sidecar_path=sidecar_1_path,
        )
        conn2.commit()
        conn2.close()

        self.assertEqual(total_2, 5)
        self.assertEqual(reused_2, 5)
        self.assertEqual(delta_2, 0)
        # ZERO encode calls: All 5 vectors were copied zero-copy from sidecar_1!
        self.assertEqual(backend_2.encode_calls, 0)

        # Verify vectors in sidecar_1 and sidecar_2 are bitwise identical
        c1 = sqlite3.connect(sidecar_1_path)
        c2 = sqlite3.connect(sidecar_2_path)
        v1 = c1.execute("SELECT record_id, vector FROM dense_vector ORDER BY record_id").fetchall()
        v2 = c2.execute("SELECT record_id, vector FROM dense_vector ORDER BY record_id").fetchall()
        self.assertEqual(v1, v2)
        c1.close()
        c2.close()

        # 3. Incremental sidecar with 1 added delta record
        backend_3 = _DeterministicTrackingBackend()
        records_extended = list(records_base) + [
            {"record_id": "rec_new_99", "retrieval_text": "New delta entity content", "retrieval_text_hash": "hash_new_99"}
        ]
        sidecar_3_path = self.tmp_path / "gen3" / "sidecar.sqlite"
        conn3 = self._init_sidecar_db(sidecar_3_path, backend_3, records_extended)

        total_3, colbert_dim_3, reused_3, delta_3 = populate_sidecar_vectors(
            connection=conn3,
            records=records_extended,
            backend=backend_3,
            prior_sidecar_path=sidecar_2_path,
        )
        conn3.commit()
        conn3.close()

        self.assertEqual(total_3, 6)
        self.assertEqual(reused_3, 5)
        self.assertEqual(delta_3, 1)
        # Exactly 1 new encode call for the single delta record!
        self.assertEqual(backend_3.encode_calls, 1)


if __name__ == "__main__":
    unittest.main()

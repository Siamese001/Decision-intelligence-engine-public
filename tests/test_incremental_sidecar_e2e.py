#!/usr/bin/env python3
"""End-to-end pipeline test for differential sidecar vector inheritance and acceleration.

Exercises:
1. Cold generation: build initial sidecar with BgeM3TestBackend (reused_count = 0).
2. Incremental generation: build second sidecar referencing prior sidecar.
3. Verify that unchanged records bypass neural encoding and copy zero-copy SQLite vectors.
4. Verify exact match between inherited vectors and full re-encoded vectors.
5. Verify sidecar integrity, lane counts, and schema validation.
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.index import (  # noqa: E402
    build_sidecar,
)
from storage.connection import sqlite3  # noqa: E402
from tests.embedding_fixture import BgeM3TestBackend  # noqa: E402


class _TrackingBackend(BgeM3TestBackend):
    """BgeM3TestBackend that counts the number of encode calls."""

    def __init__(self) -> None:
        super().__init__()
        self.encode_calls = 0

    def encode(self, text: str) -> dict[str, object]:
        self.encode_calls += 1
        return super().encode(text)


class TestIncrementalSidecarE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-sidecar-e2e-")
        self.root = Path(self.temp.name)
        self.graph = ROOT / "graph" / "nee_graph.sqlite"
        if not self.graph.exists():
            self.skipTest("Base graph not found at graph/nee_graph.sqlite")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_incremental_sidecar_inheritance_e2e(self) -> None:
        # Step 1: Initial cold generation (reused_count = 0)
        sidecar_1 = self.root / "gen1" / "sidecar.sqlite"
        backend_1 = _TrackingBackend()

        t0 = time.perf_counter()
        receipt_1 = build_sidecar(
            graph_path=self.graph,
            sidecar_path=sidecar_1,
            generation_id="generation-e2e-cold",
            graph_snapshot="graph-snapshot-e2e-cold",
            backend=backend_1,
            prior_sidecar_path=None,
        )
        time.perf_counter() - t0

        self.assertTrue(sidecar_1.is_file())
        self.assertEqual(receipt_1["validation"]["status"], "PASS")
        cold_record_count = int(receipt_1["record_count"])
        self.assertGreater(cold_record_count, 0)
        self.assertEqual(backend_1.encode_calls, cold_record_count)

        # Step 2: Incremental generation against identical graph (all records unchanged)
        sidecar_2 = self.root / "gen2" / "sidecar.sqlite"
        backend_2 = _TrackingBackend()

        t1 = time.perf_counter()
        receipt_2 = build_sidecar(
            graph_path=self.graph,
            sidecar_path=sidecar_2,
            generation_id="generation-e2e-incremental",
            graph_snapshot="graph-snapshot-e2e-incremental",
            backend=backend_2,
            prior_sidecar_path=sidecar_1,
        )
        time.perf_counter() - t1

        self.assertTrue(sidecar_2.is_file())
        self.assertEqual(receipt_2["validation"]["status"], "PASS")
        self.assertEqual(int(receipt_2["record_count"]), cold_record_count)

        # 100% of unchanged records must bypass backend.encode()!
        self.assertEqual(
            backend_2.encode_calls,
            0,
            f"Expected 0 encode calls on identical replay, got {backend_2.encode_calls}",
        )

        # Verify vectors match exactly between sidecar_1 and sidecar_2
        conn1 = sqlite3.connect(sidecar_1)
        conn2 = sqlite3.connect(sidecar_2)

        rows1 = conn1.execute(
            "SELECT record_id, vector FROM dense_vector ORDER BY record_id"
        ).fetchall()
        rows2 = conn2.execute(
            "SELECT record_id, vector FROM dense_vector ORDER BY record_id"
        ).fetchall()

        self.assertEqual(len(rows1), len(rows2))
        for r1, r2 in zip(rows1, rows2, strict=False):
            self.assertEqual(r1[0], r2[0])
            self.assertEqual(r1[1], r2[1])

        # Verify sparse weights match exactly
        sp1 = conn1.execute(
            "SELECT record_id, token, weight FROM sparse_weight ORDER BY record_id, token"
        ).fetchall()
        sp2 = conn2.execute(
            "SELECT record_id, token, weight FROM sparse_weight ORDER BY record_id, token"
        ).fetchall()
        self.assertEqual(sp1, sp2)

        # Verify ColBERT tokens match exactly
        col1 = conn1.execute(
            "SELECT record_id, position, token, vector FROM colbert_token ORDER BY record_id, position"
        ).fetchall()
        col2 = conn2.execute(
            "SELECT record_id, position, token, vector FROM colbert_token ORDER BY record_id, position"
        ).fetchall()
        self.assertEqual(col1, col2)

        conn1.close()
        conn2.close()


if __name__ == "__main__":
    unittest.main()

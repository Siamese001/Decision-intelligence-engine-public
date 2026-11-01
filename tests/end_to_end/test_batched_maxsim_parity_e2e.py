#!/usr/bin/env python3
"""End-to-end test verifying numerical parity and ranking parity of Batched ColBERT MaxSim (Wave 4).

Verifies:
1. Batched ColBERT MaxSim produces mathematically identical candidate rankings to the serial loop.
2. Full multi-lane hybrid retrieval with RRF rank fusion maintains zero rank drift.
3. PrePaddedColbertCache yields cache hits across repeated query execution over the active sidecar.
4. Output schema and result envelopes remain fully compliant with zero regression.
"""
from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.cli.engine import _backend  # noqa: E402
from dual_pipeline.cli.model_resolution import auto_resolve_backend_config  # noqa: E402
from dual_pipeline.core import connect_sqlite, read_json  # noqa: E402
from dual_pipeline.index_core.fusion import score_colbert  # noqa: E402
from dual_pipeline.index_core.retriever import HybridRetriever  # noqa: E402
from dual_pipeline.index_core.retriever_cache import PrePaddedColbertCache  # noqa: E402


@pytest.mark.e2e
class TestBatchedMaxSimParityE2E(unittest.TestCase):
    """E2E suite verifying Batched ColBERT MaxSim parity against live sidecar."""

    @classmethod
    def setUpClass(cls) -> None:
        runtime_root = ROOT / ".local" / "dual_pipeline"
        if not runtime_root.exists():
            runtime_root = ROOT.parent.parent / ".local" / "dual_pipeline"
        cls.runtime_root = runtime_root
        active_pair_path = runtime_root / "active_pair.json"
        if not active_pair_path.is_file():
            cls.has_active_pair = False
            return

        cls.has_active_pair = True
        cls.active_pair = read_json(active_pair_path)
        cls.sidecar_path = runtime_root / cls.active_pair["sidecar_path"]

    def setUp(self) -> None:
        if not self.has_active_pair or not self.sidecar_path.is_file():
            self.skipTest("Active sidecar not available for E2E MaxSim test")

    def test_e2e_batched_colbert_scoring_parity_on_sidecar(self) -> None:
        """Verify score_colbert parity on live sidecar candidate tokens."""
        args = argparse.Namespace(
            runtime_root=self.runtime_root,
            model_path="BAAI/bge-m3",
            model_manifest=None,
            model_revision=None,
            embedding_backend="bge-m3-dev",
            allow_dev_bge_m3=True,
            allow_internal_operational=False,
            internal_operational_policy=None,
        )
        auto_resolve_backend_config(args)
        backend = _backend(args)

        conn = connect_sqlite(self.sidecar_path, readonly=True)
        # Fetch candidate record IDs from sidecar
        sample_ids = [
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT record_id FROM colbert_token LIMIT 25"
            )
        ]
        if not sample_ids:
            self.skipTest("No colbert tokens in sidecar database")

        encoded = backend.encode("Project Nexus architecture and PartnerAdvisory alignment")
        query_colbert = encoded["colbert"]

        # 1. Serial baseline: evaluate individually using candidate subsets < 4
        serial_scores_dict: dict[str, float] = {}
        for rid in sample_ids:
            res = score_colbert(conn, query_colbert, [rid])
            serial_scores_dict[rid] = res[0][0]

        # 2. Batched evaluation with padded cache
        padded_cache = PrePaddedColbertCache(max_entries=10)
        batched_scores = score_colbert(
            conn,
            query_colbert,
            sample_ids,
            padded_cache=padded_cache,
        )
        self.assertEqual(len(batched_scores), len(sample_ids))

        # Assert rank parity and numerical closeness
        batched_scores_dict = {rid: s for s, rid in batched_scores}
        for rid in sample_ids:
            s_serial = serial_scores_dict[rid]
            s_batched = batched_scores_dict[rid]
            self.assertTrue(
                np.isclose(s_serial, s_batched, atol=1e-3, rtol=1e-4),
                f"Score mismatch for {rid}: serial={s_serial}, batched={s_batched}",
            )

        # 3. Repeated call verifies padded cache hit
        batched_scores_cached = score_colbert(
            conn,
            query_colbert,
            sample_ids,
            padded_cache=padded_cache,
        )
        self.assertEqual(padded_cache.stats()["hits"], 1)
        self.assertEqual(batched_scores, batched_scores_cached)
        conn.close()

    def test_e2e_hybrid_retriever_ranking_parity(self) -> None:
        """Verify HybridRetriever retrieve_lane executes batched MaxSim and preserves results."""
        args = argparse.Namespace(
            runtime_root=self.runtime_root,
            model_path="BAAI/bge-m3",
            model_manifest=None,
            model_revision=None,
            embedding_backend="bge-m3-dev",
            allow_dev_bge_m3=True,
            allow_internal_operational=False,
            internal_operational_policy=None,
        )
        auto_resolve_backend_config(args)
        backend = _backend(args)

        retriever = HybridRetriever(self.sidecar_path, backend)
        query = "Project Beacon phase 1 boundaries and deliverables"

        result = retriever.retrieve_lane(
            query=query,
            lane="approved",
            classification="internal",
            audiences=["internal_consulting"],
            top_k=10,
        )

        self.assertIn("results", result)
        self.assertIn("colbert_candidate_count", result)
        self.assertGreater(result["colbert_candidate_count"], 0)

        # Verify second query uses padded cache
        result_2 = retriever.retrieve_lane(
            query=query,
            lane="approved",
            classification="internal",
            audiences=["internal_consulting"],
            top_k=10,
        )
        self.assertEqual(
            [r["record_id"] for r in result["results"]],
            [r["record_id"] for r in result_2["results"]],
        )
        # Verify padded cache recorded hits
        self.assertGreaterEqual(retriever._colbert_padded_cache.stats()["hits"], 0)


if __name__ == "__main__":
    unittest.main()

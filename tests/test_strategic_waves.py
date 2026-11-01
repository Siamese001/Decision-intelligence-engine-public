#!/usr/bin/env python3
"""Receipt-chain acceptance tests for all strategic-reasoning waves."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import read_json, sha256_file
from dual_pipeline.strategic_waves import WAVES, run_strategic_waves

ROOT = Path(__file__).resolve().parents[1]


class StrategicWaveReceiptTests(unittest.TestCase):
    def test_all_waves_emit_chained_receipts_without_mutation(self) -> None:
        graph = ROOT / "graph" / "nee_graph.sqlite"
        graph_before = sha256_file(graph)
        with tempfile.TemporaryDirectory(prefix="nee-strategic-waves-") as directory:
            output = Path(directory) / "run"
            summary = run_strategic_waves(
                output_root=output,
                graph_path=graph,
                root=ROOT,
                completed_at="2026-07-29T00:00:00Z",
            )
            self.assertEqual("PASS", summary["status"])
            self.assertEqual(len(WAVES), summary["receipt_count"])
            self.assertEqual("READY_NOT_ACTIVATED", summary["activation_state"])
            prior = None
            for wave in WAVES:
                receipt = read_json(output / "receipts" / f"wave-{wave.number:02d}.json")
                validate_artifact(receipt)
                self.assertEqual("PASS", receipt["status"])
                self.assertEqual(prior, receipt["prior_receipt_digest"])
                self.assertEqual(graph_before, receipt["graph_sha256_before"])
                self.assertEqual(graph_before, receipt["graph_sha256_after"])
                self.assertEqual(0, receipt["provider_call_count"])
                self.assertEqual(0, receipt["model_call_count"])
                self.assertEqual(0, receipt["graph_mutation_count"])
                self.assertEqual(0, receipt["index_mutation_count"])
                self.assertEqual(0, receipt["active_pair_mutation_count"])
                prior = receipt["canonical_digest"]["value"]
            self.assertEqual(
                "READY_NOT_ACTIVATED",
                read_json(output / "receipts" / "wave-08.json")["activation_state"],
            )
        self.assertEqual(graph_before, sha256_file(graph))


if __name__ == "__main__":
    unittest.main(verbosity=2)

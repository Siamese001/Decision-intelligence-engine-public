"""End-to-end integration and shadow evaluation test for anchor expansion & Index B."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import (
    ROOT,
    read_json,
    sha256_file,
)
from dual_pipeline.index_core.backends import get_backend
from dual_pipeline.index_core.shadow_eval import (
    build_index_b_sidecar,
    run_shadow_index_comparison,
    verify_edge_fact_reachability,
)


class TestAnchorExpansionE2E(unittest.TestCase):
    def setUp(self):
        self.active_pair_path = ROOT / ".local" / "dual_pipeline" / "active_pair.json"
        if not self.active_pair_path.is_file():
            self.skipTest("Active runtime pair not found, skipping live E2E")

        self.active_pair = read_json(self.active_pair_path)
        self.graph_path = ROOT / ".local" / "dual_pipeline" / self.active_pair["graph_path"]
        self.sidecar_path = ROOT / ".local" / "dual_pipeline" / self.active_pair["sidecar_path"]
        if not self.graph_path.is_file() or not self.sidecar_path.is_file():
            self.skipTest("Active graph or sidecar database not found, skipping live E2E")

        self.orig_sidecar_sha256 = sha256_file(self.sidecar_path)
        self.orig_active_pair = dict(self.active_pair)
        self.temp_shadow_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_shadow_dir.cleanup()
        # Verify strict zero in-place sidecar mutation invariant
        self.assertEqual(
            sha256_file(self.sidecar_path),
            self.orig_sidecar_sha256,
            "CRITICAL: Active sidecar was mutated in-place during shadow evaluation!",
        )
        self.assertEqual(
            read_json(self.active_pair_path),
            self.orig_active_pair,
            "CRITICAL: Active pair was mutated in-place during shadow evaluation!",
        )

    def test_e2e_anchor_expansion_and_shadow_index_comparison(self):
        shadow_dir = Path(self.temp_shadow_dir.name)
        backend = get_backend(
            "bge-m3-dev",
            allow_dev_backend=True,
            model_revision=self.active_pair["model_revision"],
        )
        generation_id = f"shadow-w4-{self.active_pair['generation_id']}"

        # Prior shadow sidecar for zero-copy vector reuse
        prior_shadow = ROOT / ".local" / "dual_pipeline" / "shadow" / "wave3_release_receipt" / "sidecar.sqlite"
        prior_path = prior_shadow if prior_shadow.is_file() else None

        # 1. Build Index B sidecar (units only, edges removed)
        b_meta = build_index_b_sidecar(
            graph_path=self.graph_path,
            base_sidecar_path=self.sidecar_path,
            output_dir=shadow_dir / "index_b",
            generation_id=generation_id,
            backend=backend,
            prior_sidecar_path=prior_path,
        )

        sidecar_b_path = b_meta["sidecar_path"]
        self.assertTrue(sidecar_b_path.is_file())

        # 2. Verify structural invariants of Index B:
        # Zero edge and architecture_relationship records
        counts = b_meta["record_type_counts"]
        self.assertEqual(counts.get("edge", 0), 0, "Index B must not contain raw edge records!")
        self.assertEqual(counts.get("architecture_relationship", 0), 0, "Index B must not contain architecture records!")
        self.assertGreaterEqual(counts.get("source_chunk", 0), 300, "Index B must contain evidence passages!")
        self.assertGreaterEqual(counts.get("assertion", 0), 150, "Index B must contain propositions!")
        self.assertGreaterEqual(counts.get("entity_facet", 0), 50, "Index B must contain entity profiles!")

        # 3. Verify 100% reachability of edge-like facts via traversal
        reachability = verify_edge_fact_reachability(self.graph_path, sidecar_b_path)
        self.assertTrue(
            reachability["target_100pct_met"],
            f"Edge reachability gap detected: {reachability['unreachable_count']} unreachable edge facts!",
        )
        self.assertEqual(reachability["reachability_percentage"], 100.0)

        # 4. Run full shadow index comparison
        runtime_root = ROOT / ".local" / "dual_pipeline"
        comp = run_shadow_index_comparison(
            graph_path=self.graph_path,
            sidecar_a_path=self.sidecar_path,
            sidecar_b_path=sidecar_b_path,
            backend=backend,
            runtime_root=runtime_root,
            generation_id=generation_id,
        )

        # 5. Verify all 11 BGE-M3 release evaluation gates
        self.assertEqual(comp["status"], "PASS")
        self.assertTrue(comp["all_11_gates_passed"])
        for gate_name, passed in comp["gates"].items():
            self.assertTrue(passed, f"Gate failure: {gate_name}")

        # 6. Verify sealed receipt was generated
        receipt_file = Path(comp["receipt_path"])
        self.assertTrue(receipt_file.is_file())
        receipt_data = read_json(receipt_file)
        self.assertEqual(receipt_data["status"], "PASS")
        self.assertIn("receipt_digest", receipt_data)
        self.assertEqual(receipt_data["receipt_digest"], comp["receipt_digest"])

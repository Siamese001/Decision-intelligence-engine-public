"""End-to-end integration and shadow index build test for semantic units."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import (
    ROOT,
    connect_sqlite,
    read_json,
    sha256_file,
)
from dual_pipeline.index_core.backends import get_backend
from dual_pipeline.index_core.constants import CLASSIFICATION_RANK
from dual_pipeline.index_core.shadow import (
    build_shadow_unit_index,
)
from dual_pipeline.index_core.sidecar import validate_sidecar


class TestSemanticUnitShadowBuildE2E(unittest.TestCase):
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
            "CRITICAL: Active sidecar was mutated in-place during shadow build!",
        )
        self.assertEqual(
            read_json(self.active_pair_path),
            self.orig_active_pair,
            "CRITICAL: Active pair was mutated in-place during shadow build!",
        )

    def test_e2e_shadow_unit_index_build_and_validation(self):
        shadow_dir = Path(self.temp_shadow_dir.name)
        backend = get_backend(
            "bge-m3-dev",
            allow_dev_backend=True,
            model_revision=self.active_pair["model_revision"],
        )
        shadow_gen_id = f"shadow-{self.active_pair['generation_id']}"

        # 1. Build scratch shadow unit index
        receipt = build_shadow_unit_index(
            graph_path=self.graph_path,
            base_sidecar_path=self.sidecar_path,
            shadow_dir=shadow_dir,
            generation_id=shadow_gen_id,
            backend=backend,
        )

        # 2. Verify structural completion and vector counts
        self.assertTrue(receipt["validation_passed"])
        self.assertGreater(receipt["base_record_count"], 0)
        self.assertGreaterEqual(receipt["passage_record_count"], 300)
        self.assertGreaterEqual(receipt["proposition_record_count"], 150)
        self.assertGreaterEqual(receipt["entity_profile_record_count"], 50)
        expected_total = (
            receipt["base_record_count"]
            + receipt["passage_record_count"]
            + receipt["proposition_record_count"]
            + receipt["entity_profile_record_count"]
        )
        self.assertEqual(receipt["total_record_count"], expected_total)

        # 3. Verify differential vector inheritance
        self.assertEqual(receipt["reused_vectors"], receipt["base_record_count"])
        self.assertEqual(
            receipt["delta_vectors"],
            receipt["passage_record_count"]
            + receipt["proposition_record_count"]
            + receipt["entity_profile_record_count"],
        )

        # 4. Verify shadow sidecar validation
        shadow_sidecar_path = Path(receipt["shadow_sidecar_path"])
        self.assertTrue(shadow_sidecar_path.is_file())
        val_res = validate_sidecar(
            shadow_sidecar_path,
            expected_generation_id=shadow_gen_id,
            expected_graph_snapshot=self.active_pair["graph_snapshot"],
            expected_graph_sha256=sha256_file(self.graph_path),
        )
        self.assertEqual(val_res["metadata"]["generation_id"], shadow_gen_id)

        # 5. Verify geometric targets:
        #    - Passage near-duplicate share <= 5.0%
        #    - Entity profile mean pairwise cosine < 0.553 (baseline)
        geom = receipt["geometry"]
        self.assertTrue(
            geom["passage_near_duplicate_target_met"],
            f"Passage near-duplicate share {geom['passage_near_duplicate_share']} > 0.05",
        )
        self.assertLessEqual(geom["passage_near_duplicate_share"], 0.05)
        self.assertTrue(
            geom["profile_mean_cosine_target_met"],
            f"Profile mean pairwise cosine {geom['profile_mean_pairwise_cosine']} >= 0.553",
        )
        self.assertLess(geom["profile_mean_pairwise_cosine"], 0.553)

        # 6. Verify security inheritance on passages
        conn = connect_sqlite(shadow_sidecar_path, readonly=True)
        try:
            passage_rows = conn.execute(
                """SELECT artifact_json FROM retrieval_record
                   WHERE record_type='source_chunk' AND retrieval_kind='evidence_span'"""
            ).fetchall()
            for r in passage_rows:
                art = json.loads(r[0])
                validate_artifact(art)
                self.assertIn(art["classification"], CLASSIFICATION_RANK)
                self.assertGreater(len(art["audiences"]), 0)
                self.assertTrue(all(isinstance(a, str) and a for a in art["audiences"]))
                if art["lane"] == "approved":
                    self.assertEqual(art["answer_use"], "citation_only")
                else:
                    self.assertEqual(art["answer_use"], "candidate_hypothesis")
        finally:
            conn.close()

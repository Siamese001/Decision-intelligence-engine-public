"""
tests/end_to_end/test_card_archival_and_sidecar_e2e.py

End-to-End lifecycle verification for Wave 3:
1. Card projection compilation and export with YAML frontmatter.
2. Archival of superseded card projections into cards/archive/<prior_gen>/ with [SUPERSEDED] banners and diff receipts.
3. Card freshness verification detecting generation drift vs active_pair.json.
4. Atomic sidecar binding verification (bound_graph_sha256 lockstep check).
5. Pre-filtered candidate vector scoring with lane and record filtering.
"""
from __future__ import annotations

import json
import math
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

import numpy as np

from dual_pipeline.card_archival import (
    archive_superseded_card,
    parse_card_frontmatter,
    verify_card_freshness,
)
from dual_pipeline.card_views import export_all_card_views
from dual_pipeline.core import sha256_file, write_json_exclusive
from dual_pipeline.index import (
    build_sidecar,
    score_prefiltered_candidates,
    verify_sidecar_binding,
)
from dual_pipeline.index_core.constants import DENSE_DIMENSIONS


class DeterministicMockBackend:
    """Mock embedding backend for fast, deterministic unit and E2E tests."""
    backend_id: str = "flagembedding-bge-m3-dev"
    model_id: str = "BAAI/bge-m3"
    model_revision: str = "5617a9f61b028005a4858fdac845db406aefb181"
    production_qualified: bool = False
    assurance_tier: str = "development"
    model_manifest_digest: str = "5617a9f61b028005a4858fdac845db406aefb1815617a9f61b028005a4858fda"
    colbert_dimensions: int = 1024
    retrieval_encoder: str = "test-mock"
    encoding_fingerprint_digest: str = "test-fingerprint-digest-123"

    @property
    def encoding_fingerprint(self) -> Dict[str, Any]:
        return {
            "backend": self.backend_id,
            "revision": self.model_revision,
            "digest": self.encoding_fingerprint_digest,
        }

    def encode(self, text: str) -> Dict[str, Any]:
        unit_val = 1.0 / math.sqrt(DENSE_DIMENSIONS)
        dense = [unit_val] * DENSE_DIMENSIONS
        sparse = {"mock": 1.0}
        colbert = [[unit_val] * 1024]
        colbert_tokens = ["[CLS]"]
        return {
            "dense": dense,
            "sparse": sparse,
            "colbert": colbert,
            "colbert_tokens": colbert_tokens,
        }


class TestCardArchivalAndSidecarE2E(unittest.TestCase):
    """E2E lifecycle suite for card archival and sidecar atomicity."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="nee_w3_e2e_")
        self.root = Path(self.temp_dir.name)
        self.graph_path = self.root / "nee_graph.sqlite"
        source_db = Path("graph/nee_graph.sqlite")
        shutil.copyfile(source_db, self.graph_path)

        self.auth_context = {
            "classification_clearance": "internal",
            "audiences": ["internal_consulting"],
            "candidate_review": False,
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_card_archival_on_generation_upgrade(self) -> None:
        """Verifies full lifecycle of card compilation, generation upgrade, and archival."""
        sha_gen1 = sha256_file(self.graph_path)
        active_pair_1 = {
            "generation_id": "gen_20261015_01",
            "graph_path": "nee_graph.sqlite",
            "graph_sha256": sha_gen1,
            "sidecar_path": "sidecar.sqlite",
            "sidecar_sha256": "dummy_sidecar_sha",
        }
        write_json_exclusive(self.root / "active_pair.json", active_pair_1)
        cards_dir = self.root / "cards"

        # 1. First export: Generation 1
        exported_1 = export_all_card_views(self.root, cards_dir, self.auth_context)
        self.assertGreater(len(exported_1), 0, "Expected card projections to be exported")
        self.assertEqual(len(exported_1), 19)

        # Verify cards have gen_1 frontmatter
        card_0 = cards_dir / "NEE_00.md"
        self.assertTrue(card_0.exists())
        meta_0 = parse_card_frontmatter(card_0.read_text(encoding="utf-8"))
        self.assertEqual(meta_0.get("generation_id"), "gen_20261015_01")
        self.assertEqual(meta_0.get("graph_sha256"), sha_gen1)

        # Freshness check gen 1: must be PASS
        freshness_1 = verify_card_freshness(cards_dir, active_pair_1)
        self.assertEqual(freshness_1["status"], "PASS")
        self.assertTrue(freshness_1["is_fresh"])
        self.assertEqual(freshness_1["drift_count"], 0)

        # 2. Mutate graph to create Generation 2
        conn = sqlite3.connect(self.graph_path)
        conn.execute("""
            INSERT INTO node (node_id, node_type, canonical_name, created_at, updated_at)
            VALUES ('node_gen2_marker', 'glossary_term', 'Gen 2 Glossary Marker', '2026-10-15T00:00:00Z', '2026-10-15T00:00:00Z')
        """)
        conn.commit()
        conn.close()

        sha_gen2 = sha256_file(self.graph_path)
        self.assertNotEqual(sha_gen1, sha_gen2)

        active_pair_2 = {
            "generation_id": "gen_20261015_02",
            "graph_path": "nee_graph.sqlite",
            "graph_sha256": sha_gen2,
            "sidecar_path": "sidecar.sqlite",
            "sidecar_sha256": "dummy_sidecar_sha_2",
        }

        # 3. Freshness check before re-export: MUST detect drift
        drift_check = verify_card_freshness(cards_dir, active_pair_2)
        self.assertEqual(drift_check["status"], "DRIFT_DETECTED")
        self.assertFalse(drift_check["is_fresh"])
        self.assertEqual(drift_check["drift_count"], 19)

        # Update active_pair.json to gen 2
        (self.root / "active_pair.json").unlink()
        write_json_exclusive(self.root / "active_pair.json", active_pair_2)

        # 4. Re-export for Generation 2: MUST archive generation 1 cards
        exported_2 = export_all_card_views(self.root, cards_dir, self.auth_context)
        self.assertEqual(len(exported_2), 19)

        # 5. Verify archive structure
        archive_dir = cards_dir / "archive" / "gen_20261015_01"
        self.assertTrue(archive_dir.is_dir(), "Archive directory for gen_20261015_01 must exist")

        archived_cards = list(archive_dir.glob("*.md"))
        diff_receipts = list(archive_dir.glob("*_diff_receipt.json"))
        self.assertEqual(len(archived_cards), 19, "All 19 superseded cards must be archived")
        self.assertEqual(len(diff_receipts), 19, "All 19 diff receipts must be written")

        # Inspect archived NEE_00.md
        archived_0 = archive_dir / "NEE_00.md"
        self.assertTrue(archived_0.exists())
        archived_0_text = archived_0.read_text(encoding="utf-8")
        self.assertIn("> **[SUPERSEDED]**:", archived_0_text)
        self.assertIn("gen_20261015_01", archived_0_text)
        self.assertIn("gen_20261015_02", archived_0_text)

        # Inspect diff receipt for NEE_00
        receipt_0_path = archive_dir / "NEE_00_diff_receipt.json"
        self.assertTrue(receipt_0_path.exists())
        receipt_0 = json.loads(receipt_0_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt_0["card_id"], "NEE_00")
        self.assertEqual(receipt_0["prior_generation_id"], "gen_20261015_01")
        self.assertEqual(receipt_0["new_generation_id"], "gen_20261015_02")
        self.assertEqual(receipt_0["prior_graph_sha256"], sha_gen1)
        self.assertEqual(receipt_0["new_graph_sha256"], sha_gen2)
        self.assertIsNotNone(receipt_0["prior_digest"])
        self.assertIsNotNone(receipt_0["new_digest"])

        # Inspect active NEE_00.md
        active_0_text = card_0.read_text(encoding="utf-8")
        self.assertNotIn("> **[SUPERSEDED]**:", active_0_text)
        meta_active = parse_card_frontmatter(active_0_text)
        self.assertEqual(meta_active.get("generation_id"), "gen_20261015_02")
        self.assertEqual(meta_active.get("graph_sha256"), sha_gen2)

        # 6. Freshness check gen 2: must be PASS
        freshness_2 = verify_card_freshness(cards_dir, active_pair_2)
        self.assertEqual(freshness_2["status"], "PASS")
        self.assertTrue(freshness_2["is_fresh"])
        self.assertEqual(freshness_2["drift_count"], 0)

    def test_idempotent_reexport_does_not_rearchive(self) -> None:
        """Verifies that re-exporting the exact same generation does not produce duplicate archives."""
        sha = sha256_file(self.graph_path)
        active_pair = {
            "generation_id": "gen_idempotent",
            "graph_path": "nee_graph.sqlite",
            "graph_sha256": sha,
            "sidecar_path": "sidecar.sqlite",
            "sidecar_sha256": "dummy",
        }
        write_json_exclusive(self.root / "active_pair.json", active_pair)
        cards_dir = self.root / "cards"

        export_all_card_views(self.root, cards_dir, self.auth_context)
        archive_parent = cards_dir / "archive"
        if archive_parent.exists():
            self.assertEqual(len(list(archive_parent.iterdir())), 0)

        # Second export of identical state
        export_all_card_views(self.root, cards_dir, self.auth_context)
        if archive_parent.exists():
            self.assertEqual(len(list(archive_parent.iterdir())), 0)

    def test_sidecar_binding_and_prefiltered_scoring(self) -> None:
        """Verifies sidecar atomic binding lockstep and candidate vector scoring."""
        sidecar_path = self.root / "sidecar.sqlite"
        backend = DeterministicMockBackend()

        receipt = build_sidecar(
            graph_path=self.graph_path,
            sidecar_path=sidecar_path,
            generation_id="gen_sidecar_test",
            graph_snapshot="snap_sidecar_test",
            backend=backend,
        )
        self.assertEqual(receipt["publication_state"], "PUBLISHED")

        # 1. Valid binding verification
        binding = verify_sidecar_binding(sidecar_path, self.graph_path)
        self.assertEqual(binding["status"], "PASS")
        self.assertEqual(binding["bound_graph_sha256"], sha256_file(self.graph_path))
        self.assertGreater(binding["record_count"], 0)

        # 2. Score prefiltered candidates with valid binding
        q_vec = [1.0 / math.sqrt(DENSE_DIMENSIONS)] * DENSE_DIMENSIONS
        scores = score_prefiltered_candidates(
            sidecar_path,
            q_vec,
            top_k=10,
            graph_path=self.graph_path,
        )
        self.assertGreater(len(scores), 0)
        self.assertAlmostEqual(scores[0]["score"], 1.0, places=3)
        self.assertIn("record_id", scores[0])
        self.assertIn("lane", scores[0])

        # 3. Lane filtering
        lane_target = scores[0]["lane"]
        filtered_scores = score_prefiltered_candidates(
            sidecar_path,
            q_vec,
            lane=lane_target,
            top_k=10,
        )
        for s in filtered_scores:
            self.assertEqual(s["lane"], lane_target)

        # 4. Allowed record ID filtering
        allowed_id = scores[0]["record_id"]
        id_scores = score_prefiltered_candidates(
            sidecar_path,
            q_vec,
            allowed_record_ids=[allowed_id],
            top_k=10,
        )
        self.assertEqual(len(id_scores), 1)
        self.assertEqual(id_scores[0]["record_id"], allowed_id)

        # 5. Tamper graph: lockstep verification must fail closed
        with open(self.graph_path, "ab") as f:
            f.write(b"tamper_bytes_lockstep_violation")

        with self.assertRaises(RuntimeError) as ctx:
            verify_sidecar_binding(sidecar_path, self.graph_path)
        self.assertIn("SIDECAR_GRAPH_HASH_MISMATCH", str(ctx.exception))

        with self.assertRaises(RuntimeError) as ctx:
            score_prefiltered_candidates(
                sidecar_path,
                q_vec,
                graph_path=self.graph_path,
            )
        self.assertIn("SIDECAR_GRAPH_HASH_MISMATCH", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

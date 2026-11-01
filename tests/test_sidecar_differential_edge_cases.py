#!/usr/bin/env python3
"""Adversarial stress testing and edge-case defenses for differential sidecar inheritance.

Covers:
1. Changed retrieval text hash: Modified record is re-encoded, not copied from stale prior.
2. Model revision mismatch: Prior sidecar with different model_id / revision falls back to full encoding.
3. Corrupted prior database: Malformed, truncated, or non-SQLite prior file falls back gracefully.
4. Missing schema table: Prior sidecar lacking dense_vector or metadata falls back safely.
5. Invariant preservation: validate_sidecar passes unconditionally across all fallback paths.
"""
from __future__ import annotations

import sys
import tempfile
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
    def __init__(self, model_id: str | None = None, model_revision: str | None = None) -> None:
        super().__init__()
        if model_id:
            self.model_id = model_id
        if model_revision:
            self.model_revision = model_revision
        self.encoded_texts: list[str] = []

    def encode(self, text: str) -> dict[str, object]:
        self.encoded_texts.append(text)
        return super().encode(text)


class TestSidecarDifferentialEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-sidecar-edge-")
        self.root = Path(self.temp.name)
        self.graph = ROOT / "graph" / "nee_graph.sqlite"
        if not self.graph.exists():
            self.skipTest("Base graph not found at graph/nee_graph.sqlite")

        # Create a valid baseline prior sidecar
        self.prior_sidecar = self.root / "prior" / "sidecar.sqlite"
        backend = _TrackingBackend()
        build_sidecar(
            graph_path=self.graph,
            sidecar_path=self.prior_sidecar,
            generation_id="generation-edge-prior",
            graph_snapshot="graph-snapshot-edge-prior",
            backend=backend,
            prior_sidecar_path=None,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_case_1_corrupted_prior_file_falls_back_gracefully(self) -> None:
        """Adversarial check: Truncated or corrupt prior database safely triggers fallback to full encoding."""
        corrupt_prior = self.root / "corrupt_prior.sqlite"
        corrupt_prior.write_bytes(b"not a valid sqlite file contents at all garbage bytes" * 10)

        backend = _TrackingBackend()
        target = self.root / "gen_corrupt_fallback" / "sidecar.sqlite"

        receipt = build_sidecar(
            graph_path=self.graph,
            sidecar_path=target,
            generation_id="generation-corrupt-fallback",
            graph_snapshot="graph-snapshot-corrupt-fallback",
            backend=backend,
            prior_sidecar_path=corrupt_prior,
        )

        self.assertTrue(target.is_file())
        self.assertEqual(receipt["validation"]["status"], "PASS")
        # Since prior was corrupt, all records were safely encoded from scratch
        self.assertEqual(len(backend.encoded_texts), int(receipt["record_count"]))

    def test_case_2_model_revision_mismatch_triggers_safe_reencode(self) -> None:
        """Adversarial check: Prior sidecar with different model revision rejects reuse and re-encodes."""
        backend_new_rev = _TrackingBackend(model_revision="new_experimental_model_revision_xyz")
        target = self.root / "gen_rev_mismatch" / "sidecar.sqlite"

        receipt = build_sidecar(
            graph_path=self.graph,
            sidecar_path=target,
            generation_id="generation-rev-mismatch",
            graph_snapshot="graph-snapshot-rev-mismatch",
            backend=backend_new_rev,
            prior_sidecar_path=self.prior_sidecar,
        )

        self.assertTrue(target.is_file())
        self.assertEqual(receipt["validation"]["status"], "PASS")
        # Must re-encode all records due to model revision mismatch
        self.assertEqual(len(backend_new_rev.encoded_texts), int(receipt["record_count"]))

    def test_case_3_missing_metadata_table_triggers_fallback(self) -> None:
        """Adversarial check: Prior sidecar missing metadata table falls back gracefully."""
        broken_prior = self.root / "broken_prior.sqlite"
        conn = sqlite3.connect(broken_prior)
        conn.execute("CREATE TABLE dummy_table (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        backend = _TrackingBackend()
        target = self.root / "gen_broken_fallback" / "sidecar.sqlite"

        receipt = build_sidecar(
            graph_path=self.graph,
            sidecar_path=target,
            generation_id="generation-broken-fallback",
            graph_snapshot="graph-snapshot-broken-fallback",
            backend=backend,
            prior_sidecar_path=broken_prior,
        )

        self.assertTrue(target.is_file())
        self.assertEqual(receipt["validation"]["status"], "PASS")
        self.assertEqual(len(backend.encoded_texts), int(receipt["record_count"]))

    def test_case_4_tampered_text_hash_forces_reencode(self) -> None:
        """Adversarial check: Record whose retrieval_text_hash changed is not copied from stale prior."""
        # Mutate one hash in prior sidecar so it no longer matches the graph record's hash
        conn = sqlite3.connect(self.prior_sidecar)
        target_rec = conn.execute("SELECT record_id FROM retrieval_record LIMIT 1").fetchone()[0]
        conn.execute(
            "UPDATE retrieval_record SET retrieval_text_hash='tampered_hash_00000000000000000000' WHERE record_id=?",
            (target_rec,),
        )
        conn.commit()
        conn.close()

        backend = _TrackingBackend()
        target = self.root / "gen_tampered_test" / "sidecar.sqlite"

        receipt = build_sidecar(
            graph_path=self.graph,
            sidecar_path=target,
            generation_id="generation-tampered-test",
            graph_snapshot="graph-snapshot-tampered-test",
            backend=backend,
            prior_sidecar_path=self.prior_sidecar,
        )

        self.assertTrue(target.is_file())
        self.assertEqual(receipt["validation"]["status"], "PASS")
        # Exactly 1 record (the tampered one) must have been re-encoded!
        self.assertEqual(
            len(backend.encoded_texts),
            1,
            f"Expected exactly 1 re-encoded record for tampered hash, got {len(backend.encoded_texts)}",
        )


if __name__ == "__main__":
    unittest.main()

"""Tests for Wave 3: Delta-Only Sidecar & Fast-Track Release Activation."""
from __future__ import annotations

import hashlib
import math
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.index import build_sidecar
from dual_pipeline.index_core.constants import DENSE_DIMENSIONS
from dual_pipeline.index_core.differential import copy_reusable_vectors
from storage.database import sqlite3

ROOT = Path(__file__).resolve().parents[1]


class _CountingBackend:
    supports_batch_encoding = False
    backend_id = "flagembedding-bge-m3-dev"
    model_id = "BAAI/bge-m3"
    model_revision = "5617a9f61b028005a4858fdac845db406aefb181"
    production_qualified = False
    assurance_tier = "development"
    retrieval_encoder = "real-bge-m3"
    model_manifest_digest = "not-applicable"

    def __init__(self, model_revision: str | None = None) -> None:
        if model_revision:
            self.model_revision = model_revision
        self.encoded_texts: list[str] = []

    def encode(self, text: str) -> dict[str, object]:
        self.encoded_texts.append(text)
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [((seed[i % len(seed)] / 127.5) - 1.0) or 0.1 for i in range(DENSE_DIMENSIONS)]
        norm = math.sqrt(sum(v * v for v in raw))
        dense = [v / norm for v in raw]
        tokens = [tok.lower() for tok in text.split()[:4] if tok.strip()] or ["token"]
        return {
            "dense": dense,
            "sparse": {tok: 1.0 / (idx + 1) for idx, tok in enumerate(tokens)},
            "colbert_tokens": tokens,
            "colbert": [dense for _ in tokens],
        }


class TestAcceleratedSidecarRelease(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-accel-sidecar-")
        self.root = Path(self.temp.name)
        self.base_graph = ROOT / "graph" / "nee_graph.sqlite"
        self.graph_path = self.root / "graph.sqlite"
        shutil.copy2(self.base_graph, self.graph_path)

        self.prior_sidecar = self.root / "prior" / "sidecar.sqlite"
        backend = _CountingBackend()
        self.prior_receipt = build_sidecar(
            graph_path=self.graph_path,
            sidecar_path=self.prior_sidecar,
            generation_id="generation-wave3-prior",
            graph_snapshot="graph-snapshot-wave3-prior",
            backend=backend,
            prior_sidecar_path=None,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_zero_delta_and_one_delta_sidecar_skip_reused_validation(self) -> None:
        """Boundary: 0-delta reuses 100% and skips validate_artifact on reused rows; 1-delta encodes 1 row."""
        backend_zero = _CountingBackend()
        target_zero = self.root / "gen_zero_delta" / "sidecar.sqlite"

        with patch(
            "dual_pipeline.index_core.differential.validate_artifact"
        ) as mock_validate:
            receipt_zero = build_sidecar(
                graph_path=self.graph_path,
                sidecar_path=target_zero,
                generation_id="generation-wave3-zero",
                graph_snapshot="graph-snapshot-wave3-zero",
                backend=backend_zero,
                prior_sidecar_path=self.prior_sidecar,
            )
            self.assertEqual(receipt_zero["validation"]["status"], "PASS")
            self.assertEqual(receipt_zero["delta_records"], 0)
            self.assertEqual(receipt_zero["reused_records"], receipt_zero["record_count"])
            self.assertEqual(len(backend_zero.encoded_texts), 0)
            # Zero-delta build reuses 100% of records, so validate_artifact is called 0 times
            self.assertEqual(mock_validate.call_count, 0)

        # Now mutate 1 record's retrieval_text_hash in prior sidecar to simulate a 1-delta change
        conn = sqlite3.connect(self.prior_sidecar)
        target_rec = conn.execute("SELECT record_id FROM retrieval_record LIMIT 1").fetchone()[0]
        conn.execute(
            "UPDATE retrieval_record SET retrieval_text_hash = 'delta_changed_hash_123' WHERE record_id = ?",
            (target_rec,),
        )
        conn.commit()
        conn.close()

        backend_one = _CountingBackend()
        target_one = self.root / "gen_one_delta" / "sidecar.sqlite"
        with patch(
            "dual_pipeline.index_core.differential.validate_artifact"
        ) as mock_validate_one:
            receipt_one = build_sidecar(
                graph_path=self.graph_path,
                sidecar_path=target_one,
                generation_id="generation-wave3-one",
                graph_snapshot="graph-snapshot-wave3-one",
                backend=backend_one,
                prior_sidecar_path=self.prior_sidecar,
            )
            self.assertEqual(receipt_one["validation"]["status"], "PASS")
            self.assertEqual(receipt_one["delta_records"], 1)
            self.assertEqual(
                receipt_one["reused_records"], receipt_one["record_count"] - 1
            )
            self.assertEqual(len(backend_one.encoded_texts), 1)
            # Only the 1 delta record underwent full JSON schema validation
            self.assertEqual(mock_validate_one.call_count, 1)

    def test_corrupted_prior_sidecar_falls_back_to_full_encode(self) -> None:
        """Adversarial: Corrupted prior sidecar file falls back to full re-encoding safely."""
        corrupt_prior = self.root / "corrupt_prior.sqlite"
        corrupt_prior.write_bytes(b"corrupted-header-bytes" * 32)
        backend = _CountingBackend()
        target = self.root / "gen_corrupt" / "sidecar.sqlite"
        receipt = build_sidecar(
            graph_path=self.graph_path,
            sidecar_path=target,
            generation_id="generation-wave3-corrupt",
            graph_snapshot="graph-snapshot-wave3-corrupt",
            backend=backend,
            prior_sidecar_path=corrupt_prior,
        )
        self.assertEqual(receipt["validation"]["status"], "PASS")
        self.assertEqual(receipt["reused_records"], 0)
        self.assertEqual(receipt["delta_records"], receipt["record_count"])

    def test_copy_reusable_vectors_fast_sql_dimension_check_and_full_norm_check(self) -> None:
        """Adversarial: Fast SQL check catches vector length mismatch; full check catches norm violation."""
        bad_prior = self.root / "bad_dim_prior.sqlite"
        shutil.copy2(self.prior_sidecar, bad_prior)
        conn_prior = sqlite3.connect(bad_prior)
        conn_prior.execute("PRAGMA ignore_check_constraints = ON")
        rec_id = conn_prior.execute("SELECT record_id FROM dense_vector LIMIT 1").fetchone()[0]
        conn_prior.execute(
            "UPDATE dense_vector SET vector = ? WHERE record_id = ?", (b"\x00" * 16, rec_id)
        )
        conn_prior.commit()
        conn_prior.close()

        dest_db = self.root / "dest_check.sqlite"
        shutil.copy2(self.prior_sidecar, dest_db)
        conn_dest = sqlite3.connect(dest_db)
        conn_dest.execute("PRAGMA ignore_check_constraints = ON")
        conn_dest.execute("DELETE FROM dense_vector")
        conn_dest.execute("DELETE FROM sparse_weight")
        conn_dest.execute("DELETE FROM colbert_token")
        conn_dest.commit()

        with self.assertRaisesRegex(RuntimeError, "dimension mismatch"):
            copy_reusable_vectors(
                conn_dest,
                bad_prior,
                [rec_id],
                skip_reused_norm_recheck=True,
            )
        conn_dest.close()

        # Also verify skip_reused_norm_recheck=False catches zero-norm 1024-dim vector
        zero_norm_prior = self.root / "zero_norm_prior.sqlite"
        shutil.copy2(self.prior_sidecar, zero_norm_prior)
        conn_zero = sqlite3.connect(zero_norm_prior)
        conn_zero.execute(
            "UPDATE dense_vector SET vector = ? WHERE record_id = ?",
            (b"\x00" * (DENSE_DIMENSIONS * 4), rec_id),
        )
        conn_zero.commit()
        conn_zero.close()

        dest_db_2 = self.root / "dest_check_2.sqlite"
        shutil.copy2(self.prior_sidecar, dest_db_2)
        conn_dest_2 = sqlite3.connect(dest_db_2)
        conn_dest_2.execute("DELETE FROM dense_vector")
        conn_dest_2.execute("DELETE FROM sparse_weight")
        conn_dest_2.execute("DELETE FROM colbert_token")
        conn_dest_2.commit()
        with self.assertRaisesRegex(RuntimeError, "violates norm bounds"):
            copy_reusable_vectors(
                conn_dest_2,
                zero_norm_prior,
                [rec_id],
                skip_reused_norm_recheck=False,
            )
        conn_dest_2.close()

    def test_interrupted_build_cleans_up_partial_companions(self) -> None:
        """Adversarial: Exception during build_sidecar leaves no .partial or WAL files behind."""
        target = self.root / "gen_interrupted" / "sidecar.sqlite"
        backend = _CountingBackend()
        with patch(
            "dual_pipeline.index_core.sidecar.populate_sidecar_vectors",
            side_effect=RuntimeError("simulated interruption"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
                build_sidecar(
                    graph_path=self.graph_path,
                    sidecar_path=target,
                    generation_id="generation-wave3-interrupted",
                    graph_snapshot="graph-snapshot-wave3-interrupted",
                    backend=backend,
                    prior_sidecar_path=self.prior_sidecar,
                )
        self.assertFalse(target.exists())
        partial = target.with_name(f".{target.name}.partial")
        self.assertFalse(partial.exists())
        self.assertEqual(list(target.parent.glob("*.partial*")), [])


if __name__ == "__main__":
    unittest.main()

"""Adversarial and edge-case tests for Wave 3 vector health diagnostics."""

from __future__ import annotations

import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_delta import inspect_vector_health


def _init_base_sidecar(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dense_vector (
            record_id TEXT PRIMARY KEY,
            dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sparse_weight (
            record_id TEXT NOT NULL,
            token TEXT NOT NULL,
            weight REAL,
            PRIMARY KEY (record_id, token)
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS colbert_token (
            record_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            token TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL,
            PRIMARY KEY (record_id, position)
        );
    """)
    return conn


class Wave3VectorHealthAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="wave3-adv-")
        self.sidecar_path = Path(self.temp_dir.name) / "sidecar.sqlite"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_nan_dense_vector_adversarial(self) -> None:
        conn = _init_base_sidecar(self.sidecar_path)
        nan_vec = [1.0] * 1023 + [float("nan")]
        blob = struct.pack("<1024f", *nan_vec)
        conn.execute("INSERT INTO dense_vector VALUES (?, 1024, ?)", ("rec:nan", blob))
        conn.commit()
        conn.close()

        health = inspect_vector_health(self.sidecar_path)
        self.assertEqual(health["status"], "UNHEALTHY")
        self.assertFalse(health["is_healthy"])
        self.assertEqual(health["tables"]["dense_vector"]["nan_count"], 1)
        self.assertTrue(any("NaN" in issue for issue in health["issues"]))

    def test_inf_dense_vector_adversarial(self) -> None:
        conn = _init_base_sidecar(self.sidecar_path)
        inf_vec = [float("inf")] + [0.5] * 1023
        blob = struct.pack("<1024f", *inf_vec)
        conn.execute("INSERT INTO dense_vector VALUES (?, 1024, ?)", ("rec:inf", blob))
        conn.commit()
        conn.close()

        health = inspect_vector_health(self.sidecar_path)
        self.assertEqual(health["status"], "UNHEALTHY")
        self.assertFalse(health["is_healthy"])
        self.assertEqual(health["tables"]["dense_vector"]["inf_count"], 1)
        self.assertTrue(any("infinite" in issue for issue in health["issues"]))

    def test_zero_vector_adversarial(self) -> None:
        conn = _init_base_sidecar(self.sidecar_path)
        zero_vec = [0.0] * 1024
        blob = struct.pack("<1024f", *zero_vec)
        conn.execute("INSERT INTO dense_vector VALUES (?, 1024, ?)", ("rec:zero", blob))
        conn.commit()
        conn.close()

        health = inspect_vector_health(self.sidecar_path)
        self.assertEqual(health["status"], "UNHEALTHY")
        self.assertFalse(health["is_healthy"])
        self.assertEqual(health["tables"]["dense_vector"]["zero_vector_count"], 1)
        self.assertTrue(any("all-zero" in issue for issue in health["issues"]))

    def test_dimension_mismatch_adversarial(self) -> None:
        conn = _init_base_sidecar(self.sidecar_path)
        # 512 floats instead of 1024
        short_vec = [0.1] * 512
        blob = struct.pack("<512f", *short_vec)
        conn.execute("INSERT INTO dense_vector VALUES (?, 512, ?)", ("rec:short", blob))
        conn.commit()
        conn.close()

        health = inspect_vector_health(self.sidecar_path)
        self.assertEqual(health["status"], "UNHEALTHY")
        self.assertFalse(health["is_healthy"])
        self.assertEqual(health["tables"]["dense_vector"]["dimension_mismatch_count"], 1)
        self.assertTrue(any("dimension mismatch" in issue for issue in health["issues"]))

    def test_corrupt_blob_length_adversarial(self) -> None:
        conn = _init_base_sidecar(self.sidecar_path)
        # 17 bytes: not divisible by 4 (corrupt float32 array)
        corrupt_blob = b"corrupt_raw_bytes"
        conn.execute("INSERT INTO dense_vector VALUES (?, 1024, ?)", ("rec:corrupt", corrupt_blob))
        conn.commit()
        conn.close()

        health = inspect_vector_health(self.sidecar_path)
        self.assertEqual(health["status"], "UNHEALTHY")
        self.assertEqual(health["tables"]["dense_vector"]["corrupt_blob_count"], 1)

    def test_sparse_weight_adversarial(self) -> None:
        conn = _init_base_sidecar(self.sidecar_path)
        conn.execute(
            "INSERT INTO sparse_weight VALUES (?, ?, ?)", ("rec:1", "token_nan", float("nan"))
        )
        conn.execute(
            "INSERT INTO sparse_weight VALUES (?, ?, ?)", ("rec:2", "token_inf", float("inf"))
        )
        conn.execute("INSERT INTO sparse_weight VALUES (?, ?, ?)", ("rec:3", "token_neg", -2.5))
        conn.commit()
        conn.close()

        health = inspect_vector_health(self.sidecar_path)
        self.assertEqual(health["status"], "UNHEALTHY")
        sparse = health["tables"]["sparse_weight"]
        self.assertEqual(sparse["nan_count"], 1)
        self.assertEqual(sparse["inf_count"], 1)
        self.assertEqual(sparse["negative_count"], 1)

    def test_colbert_token_adversarial(self) -> None:
        conn = _init_base_sidecar(self.sidecar_path)
        nan_vec = [float("nan")] * 128
        blob_nan = struct.pack("<128f", *nan_vec)
        zero_vec = [0.0] * 128
        blob_zero = struct.pack("<128f", *zero_vec)

        conn.execute(
            "INSERT INTO colbert_token VALUES (?, 0, 'tok_nan', 128, ?)", ("rec:1", blob_nan)
        )
        conn.execute(
            "INSERT INTO colbert_token VALUES (?, 1, 'tok_zero', 128, ?)", ("rec:1", blob_zero)
        )
        conn.commit()
        conn.close()

        health = inspect_vector_health(self.sidecar_path)
        self.assertEqual(health["status"], "UNHEALTHY")
        colbert = health["tables"]["colbert_token"]
        self.assertEqual(colbert["nan_count"], 1)
        self.assertEqual(colbert["zero_vector_count"], 1)

    def test_healthy_vector_sidecar(self) -> None:
        conn = _init_base_sidecar(self.sidecar_path)
        val = 1.0 / (1024**0.5)
        clean_blob = struct.pack("<1024f", *([val] * 1024))
        conn.execute("INSERT INTO dense_vector VALUES (?, 1024, ?)", ("rec:clean", clean_blob))
        conn.execute("INSERT INTO sparse_weight VALUES (?, 'token', 1.25)", ("rec:clean",))
        conn.execute(
            "INSERT INTO colbert_token VALUES (?, 0, 'token', 1024, ?)", ("rec:clean", clean_blob)
        )
        conn.commit()
        conn.close()

        health = inspect_vector_health(self.sidecar_path)
        self.assertEqual(health["status"], "HEALTHY")
        self.assertTrue(health["is_healthy"])
        self.assertEqual(len(health["issues"]), 0)
        self.assertEqual(health["tables"]["dense_vector"]["nan_count"], 0)
        self.assertEqual(health["tables"]["dense_vector"]["zero_vector_count"], 0)
        self.assertEqual(health["tables"]["dense_vector"]["dimension_mismatch_count"], 0)
        self.assertAlmostEqual(health["tables"]["dense_vector"]["min_norm"], 1.0, places=2)

    def test_unnormalized_vector_magnitude_adversarial(self) -> None:
        conn = _init_base_sidecar(self.sidecar_path)
        # Vector with norm = 50.0
        val = 50.0 / (1024**0.5)
        large_norm_blob = struct.pack("<1024f", *([val] * 1024))
        conn.execute("INSERT INTO dense_vector VALUES (?, 1024, ?)", ("rec:large", large_norm_blob))
        conn.commit()
        conn.close()

        health = inspect_vector_health(self.sidecar_path)
        self.assertEqual(health["status"], "UNHEALTHY")
        self.assertFalse(health["is_healthy"])
        self.assertEqual(health["tables"]["dense_vector"]["norm_out_of_bounds_count"], 1)
        self.assertTrue(any("norm outside" in issue for issue in health["issues"]))

    def test_sub_unit_vector_magnitude_adversarial(self) -> None:
        conn = _init_base_sidecar(self.sidecar_path)
        # Vector with norm = 0.1
        val = 0.1 / (1024**0.5)
        small_norm_blob = struct.pack("<1024f", *([val] * 1024))
        conn.execute("INSERT INTO dense_vector VALUES (?, 1024, ?)", ("rec:small", small_norm_blob))
        conn.commit()
        conn.close()

        health = inspect_vector_health(self.sidecar_path)
        self.assertEqual(health["status"], "UNHEALTHY")
        self.assertFalse(health["is_healthy"])
        self.assertEqual(health["tables"]["dense_vector"]["norm_out_of_bounds_count"], 1)
        self.assertTrue(any("norm outside" in issue for issue in health["issues"]))

    def test_missing_sidecar_file_handled_gracefully(self) -> None:
        missing_path = Path("/tmp/non_existent_sidecar_998877.sqlite")
        health = inspect_vector_health(missing_path)
        self.assertEqual(health["status"], "ERROR")
        self.assertIn("Sidecar file not found", health["reason"])


if __name__ == "__main__":
    unittest.main()

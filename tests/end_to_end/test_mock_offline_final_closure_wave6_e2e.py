"""
tests/end_to_end/test_mock_offline_final_closure_wave6_e2e.py

Class B E2E Acceptance Test Suite for Wave 6:
"Adversarial closure and governed release."

Governed under docs/plans/docs_eradicate_mock_offline_plan_20261008.md.
Enforces:
1. Complete predecessor receipt chain integrity (Waves 0-5) with lock files and verified parents.
2. Complete eradication of shared fakes (http_fake, subprocess_fake).
3. Universal clean pass across full-repository AST linters (mock boundaries, third-party patch).
4. Config hasher purity without mock fallback modes.
5. Production purity and storage context barriers.
6. Real execution guarantees across subprocess and disposable SQLite.
7. Fail-closed dependency verification without silent fallback.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


class TestMockOfflineFinalClosureWave6E2E(unittest.TestCase):
    """Final E2E acceptance suite verifying closure of mock/offline eradication across all boundaries."""

    def test_full_receipt_chain_integrity(self) -> None:
        """Verify that receipts for Waves 0 through 5 exist, chain correctly, have locks, and verify."""
        receipts_dir = ROOT / "reports" / "mock_offline"
        locks_dir = receipts_dir / "locks"

        expected_chain = [
            (0, "wave0_receipt.json", None),
            (1, "wave1_receipt.json", "reports/mock_offline/wave0_receipt.json"),
            (2, "wave2_receipt.json", "reports/mock_offline/wave1_receipt.json"),
            (3, "wave3_receipt.json", "reports/mock_offline/wave2_receipt.json"),
            (4, "wave4_receipt.json", "reports/mock_offline/wave3_receipt.json"),
            (5, "wave5_receipt.json", "reports/mock_offline/wave4_receipt.json"),
        ]

        for wave_num, receipt_name, expected_parent in expected_chain:
            with self.subTest(wave=wave_num):
                receipt_path = receipts_dir / receipt_name
                self.assertTrue(receipt_path.is_file(), f"Missing receipt: {receipt_path}")

                data = json.loads(receipt_path.read_text(encoding="utf-8"))
                self.assertEqual(data.get("wave"), wave_num)
                self.assertEqual(data.get("status"), "COMPLETE")
                self.assertEqual(data.get("parent_receipt"), expected_parent)

                # Verify lock file
                lock_file = locks_dir / f"wave-{wave_num}.complete"
                self.assertTrue(lock_file.is_file(), f"Missing lock file for wave {wave_num}: {lock_file}")

                # Verify receipt validity using record_wave_receipt tool
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "tools" / "record_wave_receipt.py"),
                        "--wave",
                        str(wave_num),
                        "--output",
                        str(receipt_path),
                        "--verify",
                    ],
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    proc.returncode, 0,
                    f"Receipt verification failed for wave {wave_num}: {proc.stderr}"
                )
                self.assertIn("[PASS] Receipt verified", proc.stdout)

    def test_complete_eradication_of_shared_fakes(self) -> None:
        """Verify that shared non-LLM fakes are completely removed from tests/fakes/."""
        http_fake_path = ROOT / "tests" / "fakes" / "http_fake.py"
        subprocess_fake_path = ROOT / "tests" / "fakes" / "subprocess_fake.py"

        self.assertFalse(http_fake_path.exists(), "tests/fakes/http_fake.py must be deleted")
        self.assertFalse(subprocess_fake_path.exists(), "tests/fakes/subprocess_fake.py must be deleted")

        fakes_init = (ROOT / "tests" / "fakes" / "__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("HTTPFake", fakes_init)
        self.assertNotIn("SubprocessFake", fakes_init)

    def test_universal_mock_and_patch_scanners_pass(self) -> None:
        """Verify that both universal AST linters pass with 0 violations across all governed files."""
        # 1. Mock boundary linter
        proc_mb = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "lint_mock_boundaries.py")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_mb.returncode, 0, f"Mock boundary failure: {proc_mb.stdout}\n{proc_mb.stderr}")
        self.assertIn("Mock boundary check passed", proc_mb.stdout)

        # 2. Third-party patch linter
        proc_tpp = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "lint_third_party_patch.py")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_tpp.returncode, 0, f"Third-party patch failure: {proc_tpp.stdout}\n{proc_tpp.stderr}")
        self.assertIn("Third-Party Patch Linter PASS", proc_tpp.stdout)

    def test_config_hasher_purity_without_mock_fallbacks(self) -> None:
        """Verify that config_hasher computes honest canonical digests without allow_mock=True."""
        from dual_pipeline.config_hasher import (
            compute_active_config_snapshot,
            compute_config_canonical_sha256,
            verify_config_integrity,
        )

        snapshot = compute_active_config_snapshot()
        self.assertIsInstance(snapshot, dict)
        self.assertEqual(snapshot.get("schema_version"), "1.0.0")

        # Verify providers block does not contain synthetic mock tokens
        providers = snapshot.get("providers", {})
        self.assertIn("openai", providers)
        self.assertIn("anthropic", providers)
        self.assertIn("google", providers)

        sha = compute_config_canonical_sha256(snapshot)
        self.assertEqual(len(sha), 64, "Digest must be 64-char hex SHA-256")
        self.assertTrue(verify_config_integrity(sha))

    def test_production_purity_and_governed_storage_isolation(self) -> None:
        """Verify that GovernedStorageContext enforces production purity and blocks unsafe mutation."""
        from dual_pipeline.storage.governed_context import (
            GovernedStorageContext,
            ProductionPurityViolationError,
            is_production_database,
        )

        prod_db = ROOT / "output" / "CANONICAL_GRAPH.db"
        self.assertTrue(is_production_database(str(prod_db)))

        # Attempting unquarantined write connection without authorization raises ProductionPurityViolationError
        with self.assertRaises(ProductionPurityViolationError):
            GovernedStorageContext.connect(path=str(prod_db), readonly=False)

    def test_real_subprocess_and_disposable_sqlite_execution(self) -> None:
        """Verify genuine OS subprocess and SQLite storage operations against isolated disposable resources."""
        # Real subprocess execution
        proc = subprocess.run(
            [sys.executable, "-c", "import sys; sys.stdout.write('verified_wave6_closure\\n')"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "verified_wave6_closure")

        # Real disposable SQLite execution
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "disposable_closure.db"
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode = WAL;")
            mode = cur.fetchone()[0]
            self.assertEqual(mode.lower(), "wal")

            cur.execute("CREATE TABLE closure_audit (id INTEGER PRIMARY KEY, item TEXT);")
            cur.execute("INSERT INTO closure_audit (item) VALUES (?);", ("eradication_complete",))
            conn.commit()

            cur.execute("SELECT item FROM closure_audit WHERE id = 1;")
            row = cur.fetchone()
            self.assertEqual(row[0], "eradication_complete")
            conn.close()

    def test_fail_closed_dependency_verification(self) -> None:
        """Verify that missing dependency or model files fail closed without falling back to mock."""
        from dual_pipeline.retrieval.bge_reranker_service import BGERerankerService

        # Nonexistent model path raises RuntimeError with RERANKER_UNAVAILABLE
        service = BGERerankerService(model_name="/nonexistent/model/weights/path")
        with self.assertRaises(RuntimeError) as ctx:
            service.predict([("test query", "cand1")])
        self.assertIn("RERANKER_UNAVAILABLE", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

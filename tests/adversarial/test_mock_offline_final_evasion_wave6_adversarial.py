"""
tests/adversarial/test_mock_offline_final_evasion_wave6_adversarial.py

Class B Adversarial & Edge-Case Acceptance Test Suite for Wave 6:
"Adversarial closure and governed release."

Governed under docs/plans/docs_eradicate_mock_offline_plan_20261008.md.
Covers 4 mandatory edge-case categories:
1. Boundary payload: 0-byte manifests, empty deliverable sets, boundary timestamps.
2. Malformed input: Corrupted receipt JSON, tampered hashes, invalid parent paths.
3. Invariant bypass attempt: Hash tampering detection, unratified decision gates, production write barriers.
4. Rollback & recovery: Lock isolation on sealing failure, clean SQLite lock release.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


class TestMockOfflineFinalEvasionWave6Adversarial(unittest.TestCase):
    """Adversarial and boundary test suite verifying tamper-proof receipts and evasion barriers."""

    # ── Category 1: Boundary Payloads ──────────────────────────────────────────

    def test_zero_byte_manifest_fails_closed(self) -> None:
        """Boundary: 0-byte manifest file fails closed when passed to receipt recorder."""
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            f.write("")
            f.flush()
            temp_manifest = Path(f.name)

        out_receipt = temp_manifest.parent / "test_out_receipt.json"
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "record_wave_receipt.py"),
                    "--wave", "6",
                    "--manifest", str(temp_manifest),
                    "--output", str(out_receipt),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(out_receipt.exists(), "Receipt must not be created from empty manifest")
        finally:
            temp_manifest.unlink(missing_ok=True)
            out_receipt.unlink(missing_ok=True)

    def test_empty_deliverables_manifest_fails_validation(self) -> None:
        """Boundary: Manifest with empty deliverables list fails closed."""
        manifest_data = {
            "wave": 6,
            "deliverables": [],
        }
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            f.write(json.dumps(manifest_data))
            f.flush()
            temp_manifest = Path(f.name)

        out_receipt = temp_manifest.parent / "empty_deliv_receipt.json"
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "record_wave_receipt.py"),
                    "--wave", "6",
                    "--manifest", str(temp_manifest),
                    "--output", str(out_receipt),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(out_receipt.exists())
        finally:
            temp_manifest.unlink(missing_ok=True)
            out_receipt.unlink(missing_ok=True)

    # ── Category 2: Malformed Input ────────────────────────────────────────────

    def test_corrupted_receipt_json_fails_verification_closed(self) -> None:
        """Malformed: Syntax corruption in receipt JSON fails verification closed."""
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            f.write("{\"wave\": 6, \"status\": \"COMPLETE\", invalid json content...")
            f.flush()
            corrupt_receipt = Path(f.name)

        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "record_wave_receipt.py"),
                    "--wave", "6",
                    "--output", str(corrupt_receipt),
                    "--verify",
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
        finally:
            corrupt_receipt.unlink(missing_ok=True)

    def test_nonexistent_parent_receipt_fails_closed(self) -> None:
        """Malformed: Pointing parent receipt to nonexistent file fails closed."""
        manifest_data = {
            "wave": 6,
            "deliverables": [{"path": "tools/lint_mock_boundaries.py", "type": "TOOL"}],
        }
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            f.write(json.dumps(manifest_data))
            f.flush()
            temp_manifest = Path(f.name)

        out_receipt = temp_manifest.parent / "nonexistent_parent_receipt.json"
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "record_wave_receipt.py"),
                    "--wave", "6",
                    "--manifest", str(temp_manifest),
                    "--parent", "/nonexistent/parent/wave_receipt_missing.json",
                    "--output", str(out_receipt),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse(out_receipt.exists())
        finally:
            temp_manifest.unlink(missing_ok=True)
            out_receipt.unlink(missing_ok=True)

    # ── Category 3: Invariant Bypass Attempts ──────────────────────────────────

    def test_receipt_content_tampering_fails_verification(self) -> None:
        """Invariant bypass: Modifying single field in sealed receipt invalidates cryptographic digest."""
        wave5_receipt_path = ROOT / "reports" / "mock_offline" / "wave5_receipt.json"
        receipt_data = json.loads(wave5_receipt_path.read_text(encoding="utf-8"))

        # Tamper with an internal field
        receipt_data["verified_by"] = "unauthorized_forged_signer.py"

        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            f.write(json.dumps(receipt_data, indent=2))
            f.flush()
            tampered_receipt = Path(f.name)

        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "record_wave_receipt.py"),
                    "--wave", "5",
                    "--output", str(tampered_receipt),
                    "--verify",
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Digest mismatch", proc.stdout + proc.stderr)
        finally:
            tampered_receipt.unlink(missing_ok=True)

    def test_unratified_decision_blocks_receipt_sealing(self) -> None:
        """Invariant bypass: Attempting to seal wave with unratified deferred decision fails closed."""
        plan_with_unresolved_decision = """
## Wave 99: Test Unratified Decision
```json
[
  {
    "decision_id": "DECISION-UNRATIFIED-FORGERY-TEST",
    "status": "DEFERRED",
    "question": "Unratified decision question?",
    "options": [{"label": "OptA"}, {"label": "OptB"}]
  }
]
```
"""
        with tempfile.NamedTemporaryFile("w+", suffix=".md", delete=False) as f:
            f.write(plan_with_unresolved_decision)
            f.flush()
            temp_plan = Path(f.name)

        manifest_data = {
            "wave": 99,
            "deliverables": [{"path": "tools/lint_mock_boundaries.py", "type": "TOOL"}],
        }
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            f.write(json.dumps(manifest_data))
            f.flush()
            temp_manifest = Path(f.name)

        out_receipt = temp_manifest.parent / "unratified_receipt.json"
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "record_wave_receipt.py"),
                    "--wave", "99",
                    "--manifest", str(temp_manifest),
                    "--plan-path", str(temp_plan),
                    "--output", str(out_receipt),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("WAVE_RECEIPT_BLOCKED", proc.stderr)
            self.assertFalse(out_receipt.exists(), "Receipt must not be emitted when decision gate blocks")
        finally:
            temp_plan.unlink(missing_ok=True)
            temp_manifest.unlink(missing_ok=True)
            out_receipt.unlink(missing_ok=True)

    def test_production_write_barrier_bypasses_blocked(self) -> None:
        """Invariant bypass: Production write barrier blocks opening output/CANONICAL_GRAPH.db."""
        from dual_pipeline.storage.governed_context import (
            GovernedStorageContext,
            ProductionPurityViolationError,
        )

        prod_db = ROOT / "output" / "CANONICAL_GRAPH.db"
        with self.assertRaises(ProductionPurityViolationError):
            GovernedStorageContext.connect(path=str(prod_db), readonly=False)

    # ── Category 4: Rollback & Recovery ────────────────────────────────────────

    def test_lock_file_not_created_on_sealing_failure(self) -> None:
        """Rollback & Recovery: Failed receipt sealing does not leave lock files in lock-dir."""
        with tempfile.TemporaryDirectory() as tmp_lock_dir:
            lock_dir_path = Path(tmp_lock_dir)

            # Invoke record_wave_receipt with an invalid manifest to trigger failure
            proc = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "tools" / "record_wave_receipt.py"),
                    "--wave", "6",
                    "--manifest", "/nonexistent/manifest/will_fail.json",
                    "--output", str(lock_dir_path / "never_created.json"),
                    "--lock-dir", str(lock_dir_path),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            locks_present = list(lock_dir_path.glob("*.complete"))
            self.assertEqual(locks_present, [], "No lock file should be created on sealing failure")

    def test_disposable_database_cleanly_released(self) -> None:
        """Rollback & Recovery: Isolated disposable SQLite database releases locks without hanging."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_cleanup.db"
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode = WAL;")
            cur.execute("CREATE TABLE cleanup_test (id INT);")
            cur.execute("INSERT INTO cleanup_test VALUES (1);")
            conn.commit()
            conn.close()

            # Deletion must succeed cleanly without PermissionError/lock contention
            db_path.unlink()
            self.assertFalse(db_path.exists())


if __name__ == "__main__":
    unittest.main()

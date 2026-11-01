#!/usr/bin/env python3
"""End-to-end integration test for Decision Record Writer, Reconciler, and Verifier."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.hitl_governance_core.decision_record_writer import record_operator_answer


class TestDecisionRecordWriterE2E(unittest.TestCase):
    """End-to-end verification of decision persistence, reconciliation, and verifier tool."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parents[2]
        cls.python_bin = cls.repo_root / ".venv" / "bin" / "python"
        if not cls.python_bin.exists():
            cls.python_bin = Path(sys.executable)

    def test_e2e_01_repo_ledger_verifies_strict(self) -> None:
        """Verify the actual repository ledger passes strict verification."""
        cmd = [str(self.python_bin), "tools/verify_decision_ledger.py", "--strict"]
        res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True)
        self.assertEqual(
            res.returncode,
            0,
            f"Repository ledger verification failed: {res.stderr}\n{res.stdout}",
        )
        self.assertIn("Decision ledger verified completely", res.stdout)
        self.assertIn("D1_copilot_precompute_hops", res.stdout)
        self.assertIn("D2_ambiguity_threshold_30pct", res.stdout)
        self.assertIn("D4_ledger_store_of_record", res.stdout)

    def test_e2e_02_reconciler_dry_run_passes(self) -> None:
        """Verify reconcile_decision_ledger.py dry run completes cleanly."""
        cmd = [str(self.python_bin), "tools/reconcile_decision_ledger.py"]
        res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True)
        self.assertEqual(
            res.returncode, 0, f"Reconciler dry run failed: {res.stderr}\n{res.stdout}"
        )
        self.assertIn("DRY_RUN_PASSED", res.stdout)

    def test_e2e_03_record_and_immediate_verification_cycle(self) -> None:
        """Acceptance Criterion 2: After any answer, verify_decision_ledger finds it in same run."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            s_path = tmp_path / "test_ledger.sqlite"
            j_path = tmp_path / "test_ledger.json"

            human_origin = {"source": "USER_INPUT", "text": "Confirmed by operator"}
            dec_id = "e2e_cycle_test_decision_001"

            # 1. Record answer
            record_operator_answer(
                decision_id=dec_id,
                question="E2E question for immediate verification",
                options=["Option 1", "Option 2"],
                selected_option="Option 1",
                operator_origin=human_origin,
                category="test_e2e",
                sqlite_path=s_path,
                json_path=j_path,
            )

            # 2. Run verifier tool targeting this isolated store
            cmd = [
                str(self.python_bin),
                "tools/verify_decision_ledger.py",
                "--sqlite",
                str(s_path),
                "--json",
                str(j_path),
                "--decision-id",
                dec_id,
                "--strict",
            ]
            res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True)
            self.assertEqual(
                res.returncode,
                0,
                f"Immediate verification failed: {res.stderr}\n{res.stdout}",
            )
            self.assertIn(f"Decision '{dec_id}' verified", res.stdout)


if __name__ == "__main__":
    unittest.main()

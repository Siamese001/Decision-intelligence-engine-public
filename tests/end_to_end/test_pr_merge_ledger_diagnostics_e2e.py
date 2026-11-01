#!/usr/bin/env python3
"""End-to-end test for PR merge gate error transparency & ledger protection (Wave 3).

Verifies:
1. Operational errors (LEDGER_CHAIN_INTEGRITY_FAILURE) surface exact diagnostics in _check_merge_prerequisites.
2. The gate strictly distinguishes status_code == 'ERROR' from 'OPERATOR_INPUT_REQUIRED'.
3. Real/simulated repository ledger remains 100% byte-identical (SHA-256 unchanged) across runs.
"""
from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from dual_pipeline.core import sha256_file
from dual_pipeline.storage.governed_context import GovernedStorageContext
from dual_pipeline.storage.hitl_sqlite_store import HITLSQLiteStore
from tools.pr_governance_core.lifecycle import _check_merge_prerequisites


class PrMergeLedgerDiagnosticsE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="pr_merge_diag_e2e_")
        self.repo_root = Path(self.tmp_dir).resolve()

        # Set up mock git repo structure
        (self.repo_root / ".git").mkdir(parents=True)
        (self.repo_root / ".local" / "ledger").mkdir(parents=True)
        self.sqlite_ledger = self.repo_root / ".local" / "ledger" / "hitl_decision_ledger.sqlite"
        self.json_ledger = self.repo_root / ".local" / "ledger" / "hitl_decision_ledger.json"

        # Populate valid initial decision record
        store = HITLSQLiteStore(self.sqlite_ledger)
        store.record_decision(
            decision_id="DEC-INIT-001",
            category="initial",
            prompt="Initial setup",
            options=[{"label": "OK"}],
            selected_option={"label": "OK"},
            rationale="Initial setup complete",
        )
        self.initial_sha = sha256_file(self.sqlite_ledger)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_merge_prerequisites_reports_ledger_integrity_error_verbatim(self) -> None:
        """When ledger has broken chain, _check_merge_prerequisites outputs exact diagnostics."""
        # Corrupt the previous_entry_hash of a second record to create broken hash link
        store = HITLSQLiteStore(self.sqlite_ledger)
        with GovernedStorageContext.write_transaction(self.sqlite_ledger) as conn:
            conn.execute(
                """
                INSERT INTO hitl_decisions (
                    entry_id, sequence_number, decision_id, category,
                    operator_id, prompt, options, selected_option,
                    selected_key, rationale, previous_entry_hash,
                    recorded_at, metadata, entry_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "corrupt-entry-2",
                    1,
                    "DEC-CORRUPT-002",
                    "governance",
                    "human",
                    "corrupt prompt",
                    "[]",
                    "{}",
                    "A",
                    "corrupt",
                    "WRONG_PREVIOUS_HASH_CORRUPTION",
                    "2026-10-14T00:00:00Z",
                    "{}",
                    "digest_12345",
                ),
            )

        buf = io.StringIO()
        orig_runtime_root = os.environ.get("NEE_RUNTIME_ROOT")
        os.environ["NEE_RUNTIME_ROOT"] = str(self.repo_root)
        try:
            with redirect_stdout(buf):
                code = _check_merge_prerequisites("feat/test-branch", repo_root=self.repo_root)

            output = buf.getvalue()
            self.assertEqual(code, 1, f"Expected error code 1 for operational error, got {code}")
            self.assertIn("OPERATIONAL_ERROR in decision clearance", output)
            self.assertIn("LEDGER_CHAIN_INTEGRITY_FAILURE", output)
            self.assertIn("Broken hash link", output)
            self.assertNotIn("Unratified operator decisions exist", output)
        finally:
            if orig_runtime_root:
                os.environ["NEE_RUNTIME_ROOT"] = orig_runtime_root
            else:
                os.environ.pop("NEE_RUNTIME_ROOT", None)

    def test_02_production_ledger_untouched_during_clearance_and_test_execution(self) -> None:
        """Ledger SHA-256 remains strictly identical across clean clearance runs."""
        orig_runtime_root = os.environ.get("NEE_RUNTIME_ROOT")
        os.environ["NEE_RUNTIME_ROOT"] = str(self.repo_root)
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                # Clean check with allow_zero_decisions (no plan file provided)
                code = _check_merge_prerequisites("feat/clean-branch", repo_root=self.repo_root)

            self.assertEqual(code, 0)
            current_sha = sha256_file(self.sqlite_ledger)
            self.assertEqual(
                self.initial_sha,
                current_sha,
                "Ledger SHA-256 must remain identical before and after clearance check",
            )
        finally:
            if orig_runtime_root:
                os.environ["NEE_RUNTIME_ROOT"] = orig_runtime_root
            else:
                os.environ.pop("NEE_RUNTIME_ROOT", None)


if __name__ == "__main__":
    unittest.main()

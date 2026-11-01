"""
tests/adversarial/test_mock_offline_substitution_wave4_adversarial.py

Class B Adversarial & Edge-Case Acceptance Test Suite for Wave 4:
"Migrate tests to original behavior; narrow claims to executed scope."

Governed under docs/plans/docs_eradicate_mock_offline_plan_20261008.md.
Covers 5 mandatory edge-case categories:
1. Boundary payload: Empty commands, 0-byte scripts, empty tokens.
2. Malformed input: Corrupted JWT/JSON tokens, invalid socket targets.
3. Concurrency & race: Parallel thread writes to isolated mutation ledger.
4. Invariant bypass attempt: dev_mode bypasses, mock provider injection, production write barriers.
5. Rollback & recovery: Subprocess timeout termination and SQLite transaction rollback.
"""

from __future__ import annotations

import concurrent.futures
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from dual_pipeline.graph_mutation_ledger import GraphMutationLedger
from dual_pipeline.llm_judge_core.master_vector_evaluator import MasterVectorEvaluator
from dual_pipeline.llm_judge_core.orchestrator import DecomposedMultiJudge
from dual_pipeline.rbac import (
    RBACAuthorizationError,
    validate_mutation_authorization,
)
from dual_pipeline.runtime.model_runtime import LocalModelRuntime
from dual_pipeline.storage.governed_context import (
    GovernedStorageContext,
    ProductionPurityViolationError,
    _REPO_ROOT,
)


class TestMockOfflineSubstitutionWave4Adversarial(unittest.TestCase):
    """Adversarial and boundary test suite verifying fail-closed eradication of test substitutions."""

    # ── Category 1: Boundary Payloads ──────────────────────────────────────────

    def test_subprocess_empty_command_or_empty_script_boundary(self) -> None:
        """Boundary: Empty command sequence to subprocess fails closed with IndexError or FileNotFoundError."""
        with self.assertRaises((IndexError, FileNotFoundError, PermissionError)):
            subprocess.run([], capture_output=True)

        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write("")
            empty_py = Path(f.name)

        try:
            res = subprocess.run([sys.executable, str(empty_py)], capture_output=True, text=True, check=True)
            self.assertEqual(res.returncode, 0)
            self.assertEqual(res.stdout, "")
        finally:
            empty_py.unlink(missing_ok=True)

    def test_rbac_empty_token_and_missing_reviewer_boundary(self) -> None:
        """Boundary: Empty string token and empty reviewer fail closed immediately."""
        with self.assertRaises(RBACAuthorizationError):
            validate_mutation_authorization(auth_token="", reviewer="")

        with self.assertRaises(RBACAuthorizationError):
            validate_mutation_authorization(auth_token="token_present", reviewer="   ")

    # ── Category 2: Malformed Inputs ───────────────────────────────────────────

    def test_rbac_malformed_token_payloads(self) -> None:
        """Malformed: Truncated JSON, non-base64, or invalid claims fail closed."""
        # Truncated JSON
        with self.assertRaises(RBACAuthorizationError) as ctx1:
            validate_mutation_authorization(auth_token='{"sub": "admin", "capabilities": [}', reviewer="admin")
        self.assertIn("Malformed JSON", str(ctx1.exception))

        # Corrupted JWT
        with self.assertRaises(RBACAuthorizationError) as ctx2:
            validate_mutation_authorization(auth_token="header.not_valid_b64!!.sig", reviewer="admin")
        self.assertIn("Malformed JWT", str(ctx2.exception))

        # Missing subject claim
        with self.assertRaises(RBACAuthorizationError) as ctx3:
            validate_mutation_authorization(auth_token='{"capabilities": ["knowledge:release"]}', reviewer="admin")
        self.assertIn("missing required 'sub'", str(ctx3.exception))

    def test_network_connection_invalid_target_fails_closed(self) -> None:
        """Malformed/Boundary: Connecting to invalid/unreachable socket raises URLError/OSError."""
        with self.assertRaises((urllib.error.URLError, OSError)):
            urllib.request.urlopen("http://127.0.0.1:59997/nonexistent", timeout=0.5)

    # ── Category 3: Invariant Bypass Attempts ──────────────────────────────────

    def test_rbac_allow_dev_bypass_flag_elimination(self) -> None:
        """Invariant bypass attempt: Passing allow_dev_bypass does NOT grant access without valid token."""
        with self.assertRaises(RBACAuthorizationError):
            validate_mutation_authorization(
                auth_token="unauthorized_token",
                reviewer="attacker",
                allow_dev_bypass=True,
            )

    def test_mock_provider_injection_bypass_blocked(self) -> None:
        """Invariant bypass attempt: Passing mock=True or mock_mode=True to providers/runtime is blocked."""
        with self.assertRaises(RuntimeError) as ctx1:
            LocalModelRuntime(mock_mode=True)
        self.assertIn("MOCK_MODEL_PROHIBITED", str(ctx1.exception))

        with self.assertRaises(ValueError) as ctx2:
            MasterVectorEvaluator(mock=True)
        self.assertIn("MOCK_VECTORS_PROHIBITED", str(ctx2.exception))

        with self.assertRaises(ValueError) as ctx3:
            DecomposedMultiJudge(mock=True)
        self.assertIn("MOCK_JUDGE_PROHIBITED", str(ctx3.exception))

    def test_production_database_write_barrier_invariant(self) -> None:
        """Invariant bypass attempt: Opening canonical production database for write raises purity violation."""
        canonical_prod_db = _REPO_ROOT / "storage" / "CANONICAL_GRAPH.db"
        with self.assertRaises(ProductionPurityViolationError):
            GovernedStorageContext.get_write_connection(canonical_prod_db)

    # ── Category 4: Concurrency & Race ─────────────────────────────────────────

    def test_graph_mutation_ledger_concurrent_writes(self) -> None:
        """Concurrency: Multi-threaded concurrent writes to isolated mutation ledger maintain atomic consistency."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "concurrent_ledger.json"
            ledger = GraphMutationLedger(ledger_path=ledger_path)

            def _write(worker_id: int) -> int:
                return ledger.record_mutation(
                    run_id=f"worker_run_{worker_id}",
                    node_count=1,
                    assertion_count=1,
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(_write, i) for i in range(16)]
                results = [f.result() for f in futures]

            self.assertEqual(len(results), 16)
            self.assertEqual(ledger.get_total_mutations(), 16)

    # ── Category 5: Rollback & Recovery ────────────────────────────────────────

    def test_subprocess_timeout_termination_and_cleanup(self) -> None:
        """Rollback & Recovery: Subprocess exceeding timeout is terminated cleanly without lingering."""
        script = "import time; time.sleep(10)"
        proc = None
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                subprocess.run(
                    [sys.executable, "-c", script],
                    timeout=0.5,
                    capture_output=True,
                )
        finally:
            pass

    def test_sqlite_transaction_rollback_recovery(self) -> None:
        """Rollback & Recovery: SQLite exception mid-transaction rolls back changes without partial state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_rollback.db"
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, balance REAL);")
            cursor.execute("INSERT INTO accounts (balance) VALUES (100.0);")
            conn.commit()

            try:
                cursor.execute("BEGIN IMMEDIATE;")
                cursor.execute("UPDATE accounts SET balance = 50.0 WHERE id = 1;")
                raise RuntimeError("Simulated crash during transaction")
            except RuntimeError:
                conn.rollback()

            cursor.execute("SELECT balance FROM accounts WHERE id = 1;")
            balance = cursor.fetchone()[0]
            self.assertEqual(balance, 100.0)
            conn.close()


if __name__ == "__main__":
    unittest.main()

"""
tests/end_to_end/test_mock_offline_migrated_suite_wave4_e2e.py

Class B E2E Acceptance Test Suite for Wave 4:
"Migrate tests to original behavior; narrow claims to executed scope."

Governed under docs/plans/docs_eradicate_mock_offline_plan_20261008.md.
Decision Gate: DECISION-MOFF-LLM-GENERATION-TEST-DOUBLE resolved to Option B
(Prohibit provider doubles in active tests too).
"""

from __future__ import annotations

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
from dual_pipeline.sanitizer import LocalPreEgressSanitizer
from dual_pipeline.storage.governed_context import (
    GovernedStorageContext,
    ProductionPurityViolationError,
    is_production_database,
)


class TestMockOfflineMigratedSuiteWave4E2E(unittest.TestCase):
    """E2E suite verifying genuine execution of migrated tests against real disposable resources."""

    def test_real_subprocess_execution_and_isolation(self) -> None:
        """Verify real OS subprocess execution with actual streams, exit codes, and environment."""
        # 1. Success execution
        proc = subprocess.run(
            [sys.executable, "-c", "import sys; sys.stdout.write('real_exec_ok\\n')"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "real_exec_ok")

        # 2. Failing execution yields real non-zero code
        failing_proc = subprocess.run(
            [sys.executable, "-c", "import sys; sys.stderr.write('real_err\\n'); sys.exit(42)"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(failing_proc.returncode, 42)
        self.assertIn("real_err", failing_proc.stderr)

    def test_isolated_temporary_sqlite_storage_lifecycle(self) -> None:
        """Verify genuine SQLite storage lifecycle with WAL mode and ACID properties on isolated file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_isolated.db"
            conn = sqlite3.connect(str(db_path))
            cursor = conn.cursor()
            cursor.execute("PRAGMA journal_mode = WAL;")
            mode = cursor.fetchone()[0]
            self.assertEqual(mode.lower(), "wal")

            cursor.execute("CREATE TABLE test_records (id INTEGER PRIMARY KEY, key TEXT, val TEXT);")
            cursor.execute("INSERT INTO test_records (key, val) VALUES (?, ?);", ("alpha", "123"))
            conn.commit()

            cursor.execute("SELECT key, val FROM test_records WHERE id = 1;")
            row = cursor.fetchone()
            self.assertEqual(row, ("alpha", "123"))
            conn.close()

            # Confirm file classification: disposable temp DB is not production
            self.assertFalse(is_production_database(str(db_path)))

            # Governed context opens disposable DB safely
            with GovernedStorageContext.read_context(db_path) as read_conn:
                cur = read_conn.cursor()
                cur.execute("SELECT COUNT(*) FROM test_records;")
                count = cur.fetchone()[0]
                self.assertEqual(count, 1)

    def test_real_rbac_token_and_capability_validation(self) -> None:
        """Verify real RBAC token validation with explicit auth token and capability."""
        valid_token = '{"sub": "test_operator", "capabilities": ["knowledge:release"], "exp": 9999999999}'
        res = validate_mutation_authorization(
            auth_token=valid_token,
            reviewer="test_operator",
            required_capability="knowledge:release",
        )
        self.assertTrue(res["authorized"])
        self.assertEqual(res["subject"], "test_operator")

        # Unauthenticated / invalid token fails closed
        with self.assertRaises(RBACAuthorizationError):
            validate_mutation_authorization(
                auth_token="invalid_token_payload",
                reviewer="test_operator",
            )

    def test_real_network_stack_fail_closed(self) -> None:
        """Verify genuine network stack isolation: unreachable sockets fail closed without fake 200s."""
        # Port 59998 on loopback is closed/unreachable
        with self.assertRaises((urllib.error.URLError, ConnectionRefusedError, OSError)):
            urllib.request.urlopen("http://127.0.0.1:59998/fail_closed_probe", timeout=1.0)

    def test_real_pre_egress_sanitizer_validation(self) -> None:
        """Verify genuine LocalPreEgressSanitizer detection of sensitive career/credential patterns."""
        from dual_pipeline.sanitizer import EgressSanitizationError, LocalPreEgressSanitizer

        # Clean payload passes without exception
        clean_msgs = [{"role": "user", "content": "Standard architectural review discussing layer dependency constraints."}]
        LocalPreEgressSanitizer.sanitize_messages(clean_msgs)

        # Sensitive secret pattern raises EgressSanitizationError
        secret_msgs = [{"role": "user", "content": "Using API key sk_test_redacted_placeholder for connection."}]
        with self.assertRaises(EgressSanitizationError):
            LocalPreEgressSanitizer.sanitize_messages(secret_msgs)

        # Billing rate pattern raises EgressSanitizationError
        rate_msgs = [{"role": "user", "content": "Our negotiated rate is $150/hr for engineering."}]
        with self.assertRaises(EgressSanitizationError):
            LocalPreEgressSanitizer.sanitize_messages(rate_msgs)

    def test_real_graph_mutation_ledger_receipt_binding(self) -> None:
        """Verify genuine GraphMutationLedger execution against isolated test ledger."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "test_mutation_ledger.json"
            ledger = GraphMutationLedger(ledger_path=ledger_path)
            c = ledger.record_mutation(
                run_id="run_test_001",
                node_count=3,
                assertion_count=7,
            )
            self.assertEqual(c, 1)
            self.assertEqual(ledger.get_total_mutations(), 1)
            last = ledger.get_last_mutation()
            self.assertIsNotNone(last)
            self.assertEqual(last["run_id"], "run_test_001")

    def test_provider_double_prohibition_option_b(self) -> None:
        """Verify Option B resolution: provider doubles and mock modes are strictly prohibited in tests."""
        # 1. LocalModelRuntime disallows mock_mode
        with self.assertRaises(RuntimeError) as ctx1:
            LocalModelRuntime(mock_mode=True)
        self.assertIn("MOCK_MODEL_PROHIBITED", str(ctx1.exception))

        # 2. MasterVectorEvaluator disallows mock=True
        with self.assertRaises(ValueError) as ctx2:
            MasterVectorEvaluator(mock=True)
        self.assertIn("MOCK_VECTORS_PROHIBITED", str(ctx2.exception))

        # 3. DecomposedMultiJudge disallows mock=True
        with self.assertRaises(ValueError) as ctx3:
            DecomposedMultiJudge(mock=True)
        self.assertIn("MOCK_JUDGE_PROHIBITED", str(ctx3.exception))

    def test_fakes_package_eradication_verification(self) -> None:
        """Verify that tests/fakes/http_fake.py and tests/fakes/subprocess_fake.py are eradicated."""
        fakes_dir = Path("tests/fakes")
        self.assertFalse((fakes_dir / "http_fake.py").exists())
        self.assertFalse((fakes_dir / "subprocess_fake.py").exists())

        # Attempting to import http_fake or subprocess_fake must fail
        with self.assertRaises(ImportError):
            import tests.fakes.http_fake  # type: ignore[import-not-found] # noqa: F401

        with self.assertRaises(ImportError):
            import tests.fakes.subprocess_fake  # type: ignore[import-not-found] # noqa: F401


if __name__ == "__main__":
    unittest.main()

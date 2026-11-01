#!/usr/bin/env python3
"""Adversarial test suite for HITL SQLite store isolation & chain verification (Wave 3).

Categories covered:
1. Invariant bypass attempt: Attempting default-path writes under NEE_ALLOW_TEST_FIXTURES / PYTEST_CURRENT_TEST.
2. Exact production regression: _raw_record metadata serialization preserving Merkle digest.
3. Malformed input & tampering detection: Distinguishing synthetic wrapper keys from authentic tampering.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import digest, sha256_file
from dual_pipeline.hitl_governance_core.decision_record_writer import (
    verify_chain_integrity,
)
from dual_pipeline.storage.hitl_sqlite_store import (
    DEFAULT_HITL_SQLITE_PATH,
    HITLSQLiteStore,
    get_default_hitl_sqlite_path,
)


class HitlSqliteTestIsolationAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="hitl_isolation_adv_")
        self.temp_root = Path(self.tmp_dir).resolve()
        self.prod_mock_dir = self.temp_root / ".local" / "ledger"
        self.prod_mock_dir.mkdir(parents=True)
        self.prod_mock_sqlite = self.prod_mock_dir / "hitl_decision_ledger.sqlite"
        self.prod_mock_sqlite.write_bytes(b"INITIAL_PRODUCTION_LEDGER_BYTES")
        self.initial_sha = sha256_file(self.prod_mock_sqlite)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_unparameterized_store_routes_to_temp_under_test_flags(self) -> None:
        """Un-parameterized HITLSQLiteStore() never touches DEFAULT_HITL_SQLITE_PATH when test flags active."""
        orig_allow = os.environ.get("NEE_ALLOW_TEST_FIXTURES")
        orig_pytest = os.environ.get("PYTEST_CURRENT_TEST")
        try:
            # Case A: NEE_ALLOW_TEST_FIXTURES=1
            os.environ["NEE_ALLOW_TEST_FIXTURES"] = "1"
            os.environ.pop("PYTEST_CURRENT_TEST", None)
            path_a = get_default_hitl_sqlite_path()
            self.assertNotEqual(path_a, DEFAULT_HITL_SQLITE_PATH)
            self.assertTrue("nee_test_hitl_ledger_" in str(path_a))

            store = HITLSQLiteStore()
            self.assertNotEqual(store.db_path, DEFAULT_HITL_SQLITE_PATH.resolve())
            self.assertTrue(store.db_path.exists())

            # Case B: PYTEST_CURRENT_TEST active
            os.environ.pop("NEE_ALLOW_TEST_FIXTURES", None)
            os.environ["PYTEST_CURRENT_TEST"] = "tests/test_foo.py::test_case"
            path_b = get_default_hitl_sqlite_path()
            self.assertNotEqual(path_b, DEFAULT_HITL_SQLITE_PATH)
            self.assertTrue("nee_test_hitl_ledger_" in str(path_b))

            # Case C: Custom NEE_TEST_LEDGER_SQLITE override
            custom_temp = self.temp_root / "custom_test.sqlite"
            os.environ["NEE_TEST_LEDGER_SQLITE"] = str(custom_temp)
            os.environ["NEE_ALLOW_TEST_FIXTURES"] = "1"
            path_c = get_default_hitl_sqlite_path()
            self.assertEqual(path_c, custom_temp)
        finally:
            if orig_allow is not None:
                os.environ["NEE_ALLOW_TEST_FIXTURES"] = orig_allow
            else:
                os.environ.pop("NEE_ALLOW_TEST_FIXTURES", None)
            if orig_pytest is not None:
                os.environ["PYTEST_CURRENT_TEST"] = orig_pytest
            else:
                os.environ.pop("PYTEST_CURRENT_TEST", None)
            os.environ.pop("NEE_TEST_LEDGER_SQLITE", None)

    def test_02_regression_raw_record_metadata_preserves_chain_integrity(self) -> None:
        """Stored _raw_record wrapper does not cause false Tampered digest in verify_chain_integrity."""
        test_sqlite = self.temp_root / "ledger.sqlite"
        store = HITLSQLiteStore(test_sqlite)

        # Record decision via store directly with metadata
        rec = store.record_decision(
            decision_id="DEC-TEST-001",
            category="routing",
            prompt="Choose routing option",
            options=[{"label": "Option A", "confidence_score": 0.9}],
            selected_option={"label": "Option A"},
            rationale="High confidence match",
            operator_id="human_test",
            metadata={"operator_class": "lead_architect", "custom_flag": True},
        )
        self.assertIn("entry_digest", rec)

        # Second record to build a chain
        store.record_decision(
            decision_id="DEC-TEST-002",
            category="governance",
            prompt="Confirm merge",
            options=[{"label": "Approve", "confidence_score": 0.95}],
            selected_option={"label": "Approve"},
            rationale="All gates passed",
            operator_id="human_test",
            metadata={"operator_class": "release_manager"},
        )

        ok, msg = verify_chain_integrity(test_sqlite)
        self.assertTrue(ok, f"verify_chain_integrity failed with _raw_record in metadata: {msg}")
        self.assertIn("2 records sequentially linked and valid", msg)

    def test_03_authentic_tampering_is_still_detected(self) -> None:
        """Actual modification of prompt or payload without updating digest triggers tamper alert."""
        test_sqlite = self.temp_root / "tamper_ledger.sqlite"
        store = HITLSQLiteStore(test_sqlite)

        store.record_decision(
            decision_id="DEC-TAMPER-001",
            category="routing",
            prompt="Original prompt",
            options=[{"label": "A"}],
            selected_option={"label": "A"},
            rationale="Original rationale",
        )

        # Tamper prompt directly in SQLite
        from dual_pipeline.storage.governed_context import GovernedStorageContext
        with GovernedStorageContext.write_transaction(test_sqlite) as conn:
            conn.execute("UPDATE hitl_decisions SET prompt = 'TAMPERED PROMPT' WHERE decision_id = 'DEC-TAMPER-001'")

        ok, msg = verify_chain_integrity(test_sqlite)
        self.assertFalse(ok, "Tampered prompt must be detected by verify_chain_integrity")
        self.assertIn("Tampered digest", msg)


if __name__ == "__main__":
    unittest.main()

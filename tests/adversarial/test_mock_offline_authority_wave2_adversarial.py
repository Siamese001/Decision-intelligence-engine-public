"""tests/adversarial/test_mock_offline_authority_wave2_adversarial.py

Adversarial and edge-case test suite for Wave 2: Verified Isolated Roots & Authentic Authority Governance.
Mandatory edge-case categories covered:
1. Boundary payload: Empty tokens, whitespace strings, empty reviewer, None paths, in-memory databases.
2. Malformed input: Truncated JWT payloads, invalid JSON tokens, non-numeric expiration claims.
3. Invariant bypass attempts: Symlink evasions, relative path traversals, ALLOW_PRODUCTION_DB_WRITES environment bypass attempts, NEE_ALLOW_DEV_MUTATION attempts.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import pytest

from dual_pipeline.core import ROOT
from dual_pipeline.graph_mutation_ledger import (
    DEFAULT_LEDGER_PATH,
    GraphMutationLedger,
    ProductionPurityViolationError,
)
from dual_pipeline.rbac import (
    RBACAuthorizationError,
    REQUIRED_MUTATION_CAPABILITY,
    validate_mutation_authorization,
)
from dual_pipeline.storage.governed_context import (
    GovernedStorageContext,
    is_production_database,
)


@pytest.mark.e2e
class TestMockOfflineAuthorityWave2Adversarial(unittest.TestCase):
    """Adversarial and boundary test suite for Wave 2 authority and isolation boundaries."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="wave2_adv_")
        self.tmp_path = Path(self.tmp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # Category 1: Boundary payload
    def test_boundary_empty_token_and_reviewer_fail_closed(self) -> None:
        """Empty or whitespace token strings and reviewers fail closed with RBACAuthorizationError."""
        with self.assertRaises(RBACAuthorizationError):
            validate_mutation_authorization("", "Amit Ayer")

        with self.assertRaises(RBACAuthorizationError):
            validate_mutation_authorization("   ", "Amit Ayer")

        with self.assertRaises(RBACAuthorizationError):
            validate_mutation_authorization(None, "")

        with self.assertRaises(RBACAuthorizationError):
            validate_mutation_authorization(None, "   ")

    def test_boundary_in_memory_database_is_not_production(self) -> None:
        """:memory: database target is permitted for disposable in-memory work."""
        self.assertFalse(is_production_database(":memory:"))

        with GovernedStorageContext.write_context(":memory:") as conn:
            conn.execute("CREATE TABLE mem_tbl (id INT);")
            conn.execute("INSERT INTO mem_tbl VALUES (42);")
            row = conn.execute("SELECT id FROM mem_tbl;").fetchone()
            self.assertEqual(row[0], 42)

    # Category 2: Malformed input
    def test_malformed_jwt_payload_and_corrupted_json_rejects(self) -> None:
        """Malformed JWT base64 chunks or malformed JSON payloads fail closed."""
        with self.assertRaises(RBACAuthorizationError):
            validate_mutation_authorization("header.corrupted_non_b64.sig", "Amit Ayer")

        with self.assertRaises(RBACAuthorizationError):
            validate_mutation_authorization("{'sub': missing_quotes", "Amit Ayer")

        with self.assertRaises(RBACAuthorizationError):
            validate_mutation_authorization("eyJhbGciOiJub25lIn0.e30.sig", "Amit Ayer")  # empty claims {}

    def test_malformed_and_negative_expiration_claims_reject(self) -> None:
        """Non-numeric or expired expiration claims are rejected immediately."""
        token_bad_exp = json.dumps({
            "sub": "Amit Ayer",
            "capabilities": [REQUIRED_MUTATION_CAPABILITY],
            "exp": "not-a-number",
        })
        with self.assertRaises(RBACAuthorizationError):
            validate_mutation_authorization(token_bad_exp, "Amit Ayer")

        token_expired = json.dumps({
            "sub": "Amit Ayer",
            "capabilities": [REQUIRED_MUTATION_CAPABILITY],
            "exp": time.time() - 3600,
        })
        with self.assertRaises(RBACAuthorizationError):
            validate_mutation_authorization(token_expired, "Amit Ayer")

    # Category 3: Invariant bypass attempts
    def test_invariant_bypass_symlink_from_sandbox_to_production_db_fails(self) -> None:
        """Symlink located inside a test sandbox pointing to canonical DB triggers mechanical barrier."""
        canonical_db = ROOT / "storage" / "CANONICAL_GRAPH.db"
        symlink_path = self.tmp_path / "sandbox_test_dir" / "innocent_symlink.db"
        symlink_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            symlink_path.symlink_to(canonical_db)
        except OSError:
            return  # Skip if environment forbids symlink creation

        self.assertTrue(is_production_database(symlink_path))
        with self.assertRaises(ProductionPurityViolationError):
            GovernedStorageContext.connect(symlink_path, readonly=False)

    def test_invariant_bypass_relative_traversal_to_production_db_fails(self) -> None:
        """Relative path traversal from temporary root to canonical DB triggers mechanical barrier."""
        canonical_db = ROOT / "storage" / "CANONICAL_GRAPH.db"
        nested_dir = self.tmp_path / "eval_sandbox_nest" / "sub"
        nested_dir.mkdir(parents=True, exist_ok=True)
        rel_traversal = nested_dir / os.path.relpath(canonical_db, nested_dir)

        self.assertTrue(is_production_database(rel_traversal))
        with self.assertRaises(ProductionPurityViolationError):
            GovernedStorageContext.connect(rel_traversal, readonly=False)

    def test_invariant_bypass_allow_production_writes_env_var_ignored(self) -> None:
        """Setting ALLOW_PRODUCTION_DB_WRITES=1 in environment cannot disable mechanical barrier."""
        canonical_db = ROOT / "storage" / "CANONICAL_GRAPH.db"
        old_val = os.environ.get("ALLOW_PRODUCTION_DB_WRITES")
        os.environ["ALLOW_PRODUCTION_DB_WRITES"] = "1"
        try:
            with self.assertRaises(ProductionPurityViolationError):
                GovernedStorageContext.connect(canonical_db, readonly=False)

            prod_ledger = GraphMutationLedger(DEFAULT_LEDGER_PATH)
            with self.assertRaises(ProductionPurityViolationError):
                prod_ledger.record_mutation(run_id="env-bypass-attempt", node_count=1)
        finally:
            if old_val is not None:
                os.environ["ALLOW_PRODUCTION_DB_WRITES"] = old_val
            else:
                os.environ.pop("ALLOW_PRODUCTION_DB_WRITES", None)

    def test_invariant_bypass_ambient_dev_mutation_env_var_ignored_by_rbac(self) -> None:
        """Setting NEE_ALLOW_DEV_MUTATION=1 or NEE_ALLOW_TEST_FIXTURES=1 cannot bypass token check."""
        for var_name in ("NEE_ALLOW_DEV_MUTATION", "NEE_ALLOW_TEST_FIXTURES"):
            old_val = os.environ.get(var_name)
            os.environ[var_name] = "1"
            try:
                with self.assertRaises(RBACAuthorizationError):
                    validate_mutation_authorization(None, "Amit Ayer")
            finally:
                if old_val is not None:
                    os.environ[var_name] = old_val
                else:
                    os.environ.pop(var_name, None)


if __name__ == "__main__":
    unittest.main()

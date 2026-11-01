"""tests/end_to_end/test_mock_offline_isolation_wave2_e2e.py

End-to-End tests for Wave 2: Verified Isolated Roots & Authentic Authority Governance.
Verifies:
1. Real disposable resources: Ephemeral temporary databases work cleanly for read/write/transactions.
2. Protected targets rejection: Canonical production databases fail closed on write attempts.
3. Production ledger barrier: Canonical graph mutation ledger rejects direct test mutation.
4. RBAC authentic token authorization: Enforces explicit token and capability; rejects missing token without dev bypass.
5. HITL authority & rollout boundaries: Unresolved decisions remain pending; rollout mode defaults to DISABLED.
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
from dual_pipeline.hitl_governance_core.autonomous_resolution.rollout import (
    RolloutMode,
    get_rollout_mode,
)
from dual_pipeline.hitl_governance_core.surfacing import validate_approval_origin
from dual_pipeline.hitl_ui_bridge import AntigravityUIBridge
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
class TestMockOfflineIsolationWave2E2E(unittest.TestCase):
    """End-to-end acceptance tests for Wave 2 isolation and authority boundaries."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.mkdtemp(prefix="wave2_e2e_")
        self.tmp_path = Path(self.tmp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_disposable_test_databases_operate_cleanly(self) -> None:
        """Real disposable SQLite databases allow reads, writes, and transactions."""
        db_path = self.tmp_path / "sandbox_disposable.db"
        self.assertFalse(is_production_database(db_path))

        with GovernedStorageContext.write_context(db_path) as conn:
            conn.execute("CREATE TABLE test_records (id INTEGER PRIMARY KEY, name TEXT);")
            conn.execute("INSERT INTO test_records (name) VALUES ('disposable_resource');")

        with GovernedStorageContext.read_context(db_path) as conn:
            row = conn.execute("SELECT name FROM test_records WHERE id=1;").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "disposable_resource")

    def test_canonical_production_database_write_fails_closed(self) -> None:
        """Write connections to canonical production database fail closed with ProductionPurityViolationError."""
        canonical_db = ROOT / "storage" / "CANONICAL_GRAPH.db"
        self.assertTrue(is_production_database(canonical_db))

        with self.assertRaises(ProductionPurityViolationError):
            GovernedStorageContext.connect(canonical_db, readonly=False)

        with self.assertRaises(ProductionPurityViolationError):
            with GovernedStorageContext.write_context(canonical_db):
                pass

    def test_production_mutation_ledger_rejects_direct_mutation(self) -> None:
        """Direct mutation recording to DEFAULT_LEDGER_PATH is blocked."""
        prod_ledger = GraphMutationLedger(DEFAULT_LEDGER_PATH)
        self.assertTrue(prod_ledger._is_production_ledger())

        with self.assertRaises(ProductionPurityViolationError):
            prod_ledger.record_mutation(run_id="wave2-e2e-illegal-run", node_count=5)

    def test_rbac_token_authorization_and_missing_token_rejection(self) -> None:
        """RBAC validates valid bearer token and fails closed when token is absent."""
        reviewer = "Amit Ayer"
        valid_token = json.dumps({
            "sub": reviewer,
            "capabilities": [REQUIRED_MUTATION_CAPABILITY],
            "exp": time.time() + 3600,
        })

        auth = validate_mutation_authorization(valid_token, reviewer)
        self.assertTrue(auth["authorized"])
        self.assertEqual(auth["subject"], reviewer)
        self.assertIn(REQUIRED_MUTATION_CAPABILITY, auth["capabilities"])

        # Missing token must raise RBACAuthorizationError without ambient bypass
        with self.assertRaises(RBACAuthorizationError) as ctx:
            validate_mutation_authorization(None, reviewer)
        self.assertIn("MUTATION_REJECTED", str(ctx.exception))

    def test_hitl_ui_bridge_leaves_missing_approval_pending(self) -> None:
        """AntigravityUIBridge leaves unreviewed choices in pending HITL_REQUIRED state without mock operator."""
        bridge = AntigravityUIBridge(mock_mode=True)
        options = [
            {"label": "Option A", "confidence": 0.55, "confidence_tier": "MEDIUM"},
            {"label": "Option B", "confidence": 0.45, "confidence_tier": "MEDIUM"},
        ]

        result = bridge.arbitrate_and_present(
            pipeline_id="pipe-w2-test",
            decision_id="dec-w2-001",
            category="architectural_decision",
            question="Resolve architectural tradeoff?",
            options=options,
        )

        self.assertEqual(result["status"], "HITL_REQUIRED")
        self.assertIsNone(result["selected_option"])
        self.assertIsNone(result.get("ledger_record"))

    def test_autonomous_rollout_defaults_to_disabled_under_tests(self) -> None:
        """RolloutMode strictly defaults to DISABLED even when executing under test runner."""
        old_val = os.environ.pop("NEE_PARAM_HITL_AUTONOMOUS_ROLLOUT_MODE", None)
        try:
            mode = get_rollout_mode()
            self.assertEqual(mode, RolloutMode.DISABLED)
        finally:
            if old_val is not None:
                os.environ["NEE_PARAM_HITL_AUTONOMOUS_ROLLOUT_MODE"] = old_val

    def test_synthetic_approval_markers_reject_mock_operator(self) -> None:
        """Origin validator rejects approvals containing mock_operator or mock_mode."""
        is_auth, msg = validate_approval_origin("mock_operator approved the attribution")
        self.assertFalse(is_auth)
        self.assertIn("APPROVAL_REJECTED_SYNTHETIC_ORIGIN", msg)

        is_auth2, msg2 = validate_approval_origin({"source": "mock_mode_harness"})
        self.assertFalse(is_auth2)


if __name__ == "__main__":
    unittest.main()

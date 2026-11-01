#!/usr/bin/env python3
"""Wave 15 tests: Verify pre-mutation gate L1-to-C0.3 change set reconciliation."""
from __future__ import annotations

import unittest

from tools.nee_pre_mutation_gate import GateError, verify_l1_to_change_set_reconciliation


class Wave15PreMutationReconciliationTests(unittest.TestCase):
    def test_reconciliation_passes_when_positive_operations_have_nonzero_delta(self) -> None:
        proposal = {
            "l1_planned_operations": [
                {"candidate_id": "cand-01", "expected_operation": "add_assertion"}
            ],
            "graph_delta_rows": [
                {"component_key": "assertion", "delta": "+1"}
            ],
        }
        # Should not raise
        verify_l1_to_change_set_reconciliation(proposal)

    def test_reconciliation_fails_when_positive_operations_dropped_without_reason(self) -> None:
        proposal = {
            "l1_planned_operations": [
                {"candidate_id": "cand-01", "expected_operation": "add_assertion"}
            ],
            "graph_delta_rows": [
                {"component_key": "assertion", "delta": "0"}
            ],
        }
        with self.assertRaises(GateError) as ctx:
            verify_l1_to_change_set_reconciliation(proposal)
        self.assertIn("GATE_DISCREPANCY_UNEXPLAINED_ZERO_OP", str(ctx.exception))

    def test_reconciliation_passes_when_zero_op_has_valid_reason_code(self) -> None:
        proposal = {
            "l1_planned_operations": [
                {"candidate_id": "cand-01", "expected_operation": "add_assertion"}
            ],
            "graph_delta_rows": [
                {"component_key": "assertion", "delta": "0"}
            ],
            "zero_op_resolution_reason": "EXACT_DUPLICATE_IDENTICAL_DIGEST",
        }
        # Should not raise
        verify_l1_to_change_set_reconciliation(proposal)

    def test_reconciliation_no_op_for_legacy_proposal_without_l1_ops(self) -> None:
        proposal = {
            "graph_delta_rows": [
                {"component_key": "assertion", "delta": "0"}
            ],
        }
        # Legacy proposals without l1_planned_operations pass without error
        verify_l1_to_change_set_reconciliation(proposal)


if __name__ == "__main__":
    unittest.main()

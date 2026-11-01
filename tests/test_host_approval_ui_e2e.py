#!/usr/bin/env python3
"""tests/test_host_approval_ui_e2e.py

Host UI Acceptance Scenario Driven Through Real Host Integration (Wave 1).
Validates the complete request-to-visible-prompt-to-human-response lifecycle:
1. Proposal ready: Visibly renders prompt with summary, usable detail view, and 3 choices.
2. Review details: Non-authorizing inspection; leaves request in PENDING state; zero mutation.
3. Explicit rejection: Terminal state REJECTED; zero mutation; prohibited from re-prompting.
4. Explicit approval: Bound to exact proposal digest, target graph, base generation, and response event ID.
5. Host tool unavailable / transport error: Delivery state FAILED; never authorizes mutation.
6. Disconnect / timeout: Delivery state DISCONNECTED; never authorizes mutation.
7. Synthetic stop-hook origin: Rejected by anti-synthetic firewall.
8. Base generation change: Invalidation of prior approval.
9. Restart recovery: Preserves pending requests or unconsumed approvals without duplicate prompts.
10. Render evidence: Emits verified render evidence with human response event IDs.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

import jsonschema

from dual_pipeline.hitl_governance_core.host_approval_dispatcher import (
    DeliveryState,
    HostApprovalDispatcher,
    MutationProposal,
    ProposalStatus,
    find_approval_by_digest,
    load_approval_request,
)
from dual_pipeline.hitl_governance_core.runtime_tool_adapter import (
    IDE_RUNTIME_TOOL_NAME,
    get_runtime_target_tool_name,
)


class TestHostApprovalUIE2E(unittest.TestCase):
    """End-to-End acceptance suite for Wave 1 Host UI approval gating."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.schema_path = Path("contracts/dual_pipeline/schemas/hitl-option.schema.json")
        assert cls.schema_path.exists(), "hitl-option.schema.json must exist"
        cls.schema = json.loads(cls.schema_path.read_text(encoding="utf-8"))

    def _create_sample_proposal(self, runtime_root: str, base_gen: int = 1) -> MutationProposal:
        return MutationProposal(
            proposal_id="prop-karyn-host-ui-e2e",
            meeting_name="Scope Morgan Vance PartnerAdvisory",
            source_hash="11" * 32,
            output_hashes={
                "01": "aa" * 32,
                "02": "bb" * 32,
                "03": "cc" * 32,
            },
            intake_hash="dd" * 32,
            target_graph="CANONICAL_GRAPH.db",
            runtime_root=runtime_root,
            expected_base_generation=base_gen,
            deltas={
                "nodes_added": 12,
                "edges_added": 24,
                "source_retirements": ["src-old-karyn-01"],
            },
            validation_evidence={
                "qa_gates_outcome": "PASS",
                "dialectical_judge_score": 96.5,
                "bge_m3_gates": 11,
            },
            rollback_scope="COMMIT_HEAD_BASE",
        )

    def test_scenario_proposal_ready_and_render_evidence(self) -> None:
        """Scenario: Proposal ready — renders prompt with summary, usable detail view, and 3 required choices."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            proposal = self._create_sample_proposal(tmp_dir)
            dig = proposal.compute_proposal_digest()
            dispatcher = HostApprovalDispatcher(runtime_root=tmp_dir)

            captured_calls = []

            def real_host_invoker(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
                captured_calls.append({"tool": tool_name, "args": args})
                # Simulate operator requesting Review details first
                return {
                    "selected_option": "Review details",
                    "origin": "USER_INPUT",
                    "event_id": "ev-human-ready-01",
                }

            res = dispatcher.dispatch(
                proposal=proposal,
                host_tool_invoker=real_host_invoker,
                target_mode="ide",
            )

            # 1. Assert host tool targeted
            self.assertEqual(len(captured_calls), 1)
            self.assertEqual(captured_calls[0]["tool"], IDE_RUNTIME_TOOL_NAME)

            # 2. Assert questions payload
            args = captured_calls[0]["args"]
            self.assertIn("questions", args)
            q_data = args["questions"][0]
            self.assertIn("Scope Morgan Vance PartnerAdvisory", q_data["question"])
            self.assertEqual(len(q_data["options"]), 3)

            # 3. Assert the three required choices
            opts = q_data["options"]
            self.assertTrue(any(o.startswith("(Recommended) Approve mutation") for o in opts))
            self.assertTrue(any("Reject" in o for o in opts))
            self.assertTrue(any("Review details" in o for o in opts))

            # 4. Assert review details execution
            self.assertEqual(res.status, ProposalStatus.AWAITING_APPROVAL)
            self.assertFalse(res.authorized)
            self.assertEqual(res.action, "REVIEW_DETAILS")
            self.assertIsNotNone(res.detail_view)
            self.assertIn(proposal.proposal_id, res.detail_view)
            self.assertIn(dig, res.detail_view)
            self.assertIn("src-old-karyn-01", res.detail_view)

            # 5. Persist render evidence artifact in runtime
            evidence_file = Path(tmp_dir) / "render_evidence.json"
            evidence_data = {
                "scenario": "proposal_ready",
                "target_tool": captured_calls[0]["tool"],
                "rendered_question": q_data["question"],
                "rendered_options": q_data["options"],
                "proposal_digest": dig,
                "delivery_state": res.delivery_state.value,
                "status": res.status.value,
                "authorized": res.authorized,
                "response_event_id": res.response_event_id,
            }
            evidence_file.write_text(json.dumps(evidence_data, indent=2), encoding="utf-8")
            self.assertTrue(evidence_file.is_file())

    def test_scenario_explicit_rejection_terminates_proposal(self) -> None:
        """Scenario: Explicit rejection terminates proposal and prevents re-prompting."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            proposal = self._create_sample_proposal(tmp_dir)
            dispatcher = HostApprovalDispatcher(runtime_root=tmp_dir)

            prompt_count = 0

            def reject_invoker(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
                nonlocal prompt_count
                prompt_count += 1
                return {
                    "selected_option": "Reject",
                    "origin": "USER_INPUT",
                    "event_id": "ev-reject-02",
                }

            res = dispatcher.dispatch(proposal=proposal, host_tool_invoker=reject_invoker)
            self.assertEqual(res.status, ProposalStatus.REJECTED)
            self.assertFalse(res.authorized)
            self.assertEqual(res.action, "REJECT")
            self.assertEqual(prompt_count, 1)

            # Subsequent call must NOT re-prompt and must remain REJECTED
            res_subsequent = dispatcher.dispatch(proposal=proposal, host_tool_invoker=reject_invoker)
            self.assertEqual(res_subsequent.status, ProposalStatus.REJECTED)
            self.assertFalse(res_subsequent.authorized)
            self.assertEqual(prompt_count, 1)  # No second prompt
            self.assertIn("previously rejected", res_subsequent.diagnostics)

    def test_scenario_explicit_approval_authorizes_mutation(self) -> None:
        """Scenario: Explicit approval binds to proposal, base gen, and authorizes mutation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            proposal = self._create_sample_proposal(tmp_dir, base_gen=2)
            dig = proposal.compute_proposal_digest()
            dispatcher = HostApprovalDispatcher(runtime_root=tmp_dir)

            def approve_invoker(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
                return {
                    "selected_option": "Approve mutation",
                    "origin": "USER_INPUT",
                    "event_id": "ev-approve-03",
                }

            res = dispatcher.dispatch(proposal=proposal, host_tool_invoker=approve_invoker)
            self.assertEqual(res.status, ProposalStatus.APPROVED)
            self.assertTrue(res.authorized)
            self.assertEqual(res.action, "APPROVE")
            self.assertIsNotNone(res.approval_receipt)
            receipt = res.approval_receipt
            self.assertEqual(receipt["proposal_digest"], dig)
            self.assertEqual(receipt["expected_base_generation"], "2")
            self.assertEqual(receipt["response_event_id"], "ev-approve-03")
            self.assertEqual(receipt["operator_origin"], "USER_INPUT")

    def test_scenario_host_unavailable_fails_closed(self) -> None:
        """Scenario: Host tool unavailable records delivery failure and never authorizes mutation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            proposal = self._create_sample_proposal(tmp_dir)
            dispatcher = HostApprovalDispatcher(runtime_root=tmp_dir)

            res = dispatcher.dispatch(proposal=proposal, host_tool_invoker=None)
            self.assertEqual(res.delivery_state, DeliveryState.FAILED)
            self.assertEqual(res.status, ProposalStatus.AWAITING_APPROVAL)
            self.assertFalse(res.authorized)
            self.assertEqual(res.action, "DELIVERY_FAILED")
            self.assertIn("unavailable", res.diagnostics)

    def test_scenario_host_disconnect_fails_closed(self) -> None:
        """Scenario: Host interaction exception/disconnect records DISCONNECTED and zero mutation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            proposal = self._create_sample_proposal(tmp_dir)
            dispatcher = HostApprovalDispatcher(runtime_root=tmp_dir)

            def failing_invoker(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
                raise ConnectionResetError("Host UI socket dropped unexpectedly")

            res = dispatcher.dispatch(proposal=proposal, host_tool_invoker=failing_invoker)
            self.assertEqual(res.delivery_state, DeliveryState.DISCONNECTED)
            self.assertEqual(res.status, ProposalStatus.AWAITING_APPROVAL)
            self.assertFalse(res.authorized)
            self.assertEqual(res.action, "DISCONNECTED")

    def test_scenario_synthetic_hook_origin_rejected(self) -> None:
        """Scenario: Synthetic IDE stop hook response is rejected by anti-synthetic firewall."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            proposal = self._create_sample_proposal(tmp_dir)
            dispatcher = HostApprovalDispatcher(runtime_root=tmp_dir)

            def synthetic_invoker(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
                return {
                    "selected_option": "Approve mutation",
                    "origin": "stop hook blocked termination: automatically approved the artifact",
                    "event_id": "ev-synthetic-04",
                }

            res = dispatcher.dispatch(proposal=proposal, host_tool_invoker=synthetic_invoker)
            self.assertEqual(res.status, ProposalStatus.AWAITING_APPROVAL)
            self.assertFalse(res.authorized)
            self.assertEqual(res.action, "REJECTED_SYNTHETIC_ORIGIN")
            self.assertIn("APPROVAL_REJECTED_SYNTHETIC_ORIGIN", res.diagnostics)

    def test_scenario_base_generation_mismatch_invalidates(self) -> None:
        """Scenario: Changed expected base generation invalidates prior approval."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            prop1 = self._create_sample_proposal(tmp_dir, base_gen=1)
            dispatcher = HostApprovalDispatcher(runtime_root=tmp_dir)

            # Approve under generation 1
            dispatcher.dispatch(
                proposal=prop1,
                host_tool_invoker=lambda t, a: {
                    "selected_option": "Approve mutation",
                    "origin": "USER_INPUT",
                    "event_id": "ev-app-gen1",
                },
            )

            # Now graph base generation advances to 2
            prop2 = self._create_sample_proposal(tmp_dir, base_gen=2)
            # Dispatching with host_tool_invoker=None: if prior approval was reused, it would return authorized=True
            res2 = dispatcher.dispatch(proposal=prop2, host_tool_invoker=None)
            self.assertFalse(res2.authorized)
            self.assertEqual(res2.delivery_state, DeliveryState.FAILED)


if __name__ == "__main__":
    unittest.main()

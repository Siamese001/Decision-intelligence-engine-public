#!/usr/bin/env python3
"""tests/end_to_end/test_question_contract_e2e.py

End-to-End lifecycle test for Wave 5 Question Payload Contract.
Exercises:
1. End-to-end question construction with QuestionBuilder and Decision D5 context handling.
2. Independent validation via QuestionValidator and CLI preflight check_ask_user_question.
3. Schema v1.2.0 compliance verification on generated options with required display_text.
4. Screen rendering budget simulation: all text 100% visible, zero slicing or truncation.
5. Tool call parameter packaging for interactive operator presentation.
6. Operator answer persistence through DecisionRecordWriter with cryptographic hash chaining.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import jsonschema

from dual_pipeline.hitl_governance_core.decision_record_writer import (
    record_operator_answer,
    verify_chain_integrity,
)
from dual_pipeline.hitl_governance_core.host_approval_dispatcher import (
    DeliveryState,
    HostApprovalDispatcher,
    MutationProposal,
    ProposalStatus,
)
from dual_pipeline.hitl_governance_core.question_builder import (
    QuestionBuilder,
)
from dual_pipeline.hitl_governance_core.question_validator import (
    MAX_OPTION_LENGTH,
    MAX_QUESTION_LENGTH,
    validate_question_payload,
)
from tools.check_ask_user_question import check_payload


class TestQuestionContractE2E(unittest.TestCase):
    """End-to-end lifecycle test for question builder, validator, and persistent recording."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.schema_path = Path("contracts/dual_pipeline/schemas/hitl-option.schema.json")
        assert cls.schema_path.exists(), "hitl-option.schema.json must exist"
        cls.schema = json.loads(cls.schema_path.read_text(encoding="utf-8"))

    def test_full_question_lifecycle_and_operator_recording(self) -> None:
        """Exercise complete lifecycle: build -> validate -> schema check -> render -> record."""
        # 1. Build question payload using QuestionBuilder
        builder = QuestionBuilder(
            question="When a draft choice exceeds the display character limit, how should the question builder handle it?"
        )
        builder.add_option(
            label="Preserve full context in question text",
            plain_english_summary="Move extensive background detail into the question text.",
            impact_statement="Preserves full context and avoids disrupting the workflow.",
            confidence_score=0.65,
            recommended=True,
            background_detail="Operators need complete situational awareness without modal truncation.",
        )
        builder.add_option(
            label="Strictly reject oversized choices",
            plain_english_summary="Halt execution immediately until the author shortens the text.",
            impact_statement="Guarantees strict size limits upfront but increases revision overhead.",
            confidence_score=0.40,
            recommended=False,
        )

        payload = builder.build()

        # 2. Verify budget constraints and zero truncation
        self.assertLessEqual(
            len(payload.question),
            MAX_QUESTION_LENGTH,
            f"Question exceeded {MAX_QUESTION_LENGTH} chars: {len(payload.question)}",
        )
        self.assertNotIn("...", payload.question)
        self.assertNotIn("\u2026", payload.question)

        for idx, opt_text in enumerate(payload.display_options, start=1):
            self.assertLessEqual(
                len(opt_text),
                MAX_OPTION_LENGTH,
                f"Option {idx} exceeded {MAX_OPTION_LENGTH} chars: {len(opt_text)}",
            )
            self.assertNotIn("...", opt_text)
            self.assertNotIn("\u2026", opt_text)

        # 3. Independent validation pass
        val_res = validate_question_payload(payload.question, payload.options)
        self.assertTrue(val_res.is_valid, f"Validation failed: {val_res.errors}")

        # 4. CLI preflight check pass
        payload_dict = {
            "question": payload.question,
            "options": payload.display_options,
        }
        cli_exit_code = check_payload(payload_dict)
        self.assertEqual(cli_exit_code, 0, "CLI preflight check failed")

        # 5. Schema v1.2.0 contract verification
        for opt in payload.options:
            jsonschema.validate(instance=opt, schema=self.schema)
            self.assertIn("display_text", opt)
            self.assertIn("confidence_score", opt)
            self.assertIn("confidence_level", opt)
            self.assertIn("impact_statement", opt)

        # 6. Format tool call for interactive presentation
        tool_call = payload.to_tool_call(tool_name="ask_user_question")
        self.assertIn("questions", tool_call)
        presented_question = tool_call["questions"][0]
        self.assertEqual(presented_question["question"], payload.question)
        self.assertEqual(len(presented_question["options"]), 2)
        self.assertFalse(presented_question["is_multi_select"])

        # 7. Simulate operator selection and persist to isolated test ledger
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_sqlite = Path(tmp_dir) / "test_hitl.sqlite"
            test_json = Path(tmp_dir) / "test_hitl.json"

            selected_choice = presented_question["options"][0]
            rec_result = record_operator_answer(
                decision_id="D5_lifecycle_e2e_test",
                question=payload.question,
                options=presented_question["options"],
                selected_option=selected_choice,
                operator_origin="USER_INPUT",
                category="operator_interaction",
                rationale="Operator confirmed preserving context via question text relocation.",
                sqlite_path=test_sqlite,
                json_path=test_json,
            )

            self.assertEqual(rec_result.status, "PERSISTED")
            self.assertEqual(rec_result.decision_id, "D5_lifecycle_e2e_test")
            self.assertEqual(rec_result.sequence_number, 0)
            self.assertEqual(rec_result.previous_entry_hash, "0" * 64)
            self.assertTrue(len(rec_result.entry_digest) == 64)

            # 8. Verify Merkle chain integrity on persisted test ledger
            chain_ok, chain_msg = verify_chain_integrity(test_sqlite)
            self.assertTrue(chain_ok, f"Chain integrity failed: {chain_msg}")

            # 9. Verify JSON export parity
            self.assertTrue(test_json.exists())
            export_data = json.loads(test_json.read_text(encoding="utf-8"))
            saved_decisions = export_data.get("decisions") or export_data.get("operator_decisions", [])
            self.assertEqual(len(saved_decisions), 1)
            saved_entry = saved_decisions[0]
            self.assertEqual(saved_entry["decision_id"], "D5_lifecycle_e2e_test")
            self.assertEqual(saved_entry["entry_digest"], rec_result.entry_digest)

    def test_mutation_proposal_lifecycle_and_correlation(self) -> None:
        """Wave 1 E2E: Proposal creation, review details rendering, and approval correlation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            proposal = MutationProposal(
                proposal_id="prop-karyn-wave1-e2e",
                meeting_name="Scope Morgan Vance PartnerAdvisory",
                source_hash="a" * 64,
                output_hashes={"01": "b" * 64, "02": "c" * 64, "03": "d" * 64},
                intake_hash="e" * 64,
                target_graph="CANONICAL_GRAPH.db",
                runtime_root=tmp_dir,
                expected_base_generation=1,
                deltas={"nodes_added": 15, "edges_added": 30, "source_retirements": []},
                validation_evidence={"qa_gates": "PASS", "judge_score": 95},
                rollback_scope="COMMIT_HEAD_123",
            )
            dig = proposal.compute_proposal_digest()
            self.assertEqual(len(dig), 64)

            dispatcher = HostApprovalDispatcher(runtime_root=tmp_dir)

            # Step 1: Dispatch with "Review details" response
            def review_details_invoker(tool_name: str, args: dict) -> dict:
                # Assert tool call format
                self.assertIn("questions", args)
                return {
                    "selected_option": "Review details",
                    "origin": "USER_INPUT",
                    "event_id": "ev-review-1",
                }

            res1 = dispatcher.dispatch(proposal=proposal, host_tool_invoker=review_details_invoker)
            self.assertEqual(res1.status, ProposalStatus.AWAITING_APPROVAL)
            self.assertFalse(res1.authorized)
            self.assertEqual(res1.action, "REVIEW_DETAILS")
            self.assertIsNotNone(res1.detail_view)
            self.assertIn(proposal.proposal_id, res1.detail_view)
            self.assertIn(dig, res1.detail_view)
            self.assertIn("COMMIT_HEAD_123", res1.detail_view)

            # Step 2: Next turn, operator responds with "Approve mutation"
            def approve_invoker(tool_name: str, args: dict) -> dict:
                return {
                    "selected_option": "Approve mutation",
                    "origin": "USER_INPUT",
                    "event_id": "ev-approve-2",
                }

            res2 = dispatcher.dispatch(proposal=proposal, host_tool_invoker=approve_invoker)
            self.assertEqual(res2.status, ProposalStatus.APPROVED)
            self.assertTrue(res2.authorized)
            self.assertEqual(res2.action, "APPROVE")
            self.assertIsNotNone(res2.approval_receipt)
            self.assertEqual(res2.approval_receipt["proposal_digest"], dig)
            self.assertEqual(res2.approval_receipt["response_event_id"], "ev-approve-2")
            self.assertEqual(res2.approval_receipt["expected_base_generation"], "1")

            # Step 3: Resuming execution restores existing unconsumed approval without re-asking
            res3 = dispatcher.dispatch(proposal=proposal, host_tool_invoker=None)
            self.assertEqual(res3.status, ProposalStatus.APPROVED)
            self.assertTrue(res3.authorized)
            self.assertIn("Existing unconsumed approval restored", res3.diagnostics)

    def test_stage_02_action_ledger_grounding_and_gate_x2_e2e(self) -> None:
        """End-to-end lifecycle verification of Action Ledger healing, date math, and Gate X2."""
        from dual_pipeline.transcript_qa_core.table_healer import heal_output_2_table
        from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator

        xgates = XExitGatesEvaluator()

        # 1. Pipeline draft with relative timing, unowned filler, and named DRI
        raw_draft_recap = (
            "# Engineering Architecture Review -- Executive Strategic & Technical Brief | 10/17/2026\n\n"
            "**Date:** 10/17/2026\n"
            "**Time:** 10:00 - 10:45 ET\n"
            "**Duration:** ~45 minutes\n"
            "**Attendees:**\n"
            "Engineering:\n"
            "- Amit Ayer (Principal Architect)\n"
            "- Alex Brannan (Staff Engineer)\n\n"
            "# Meeting Objective\n"
            "Review deployment infrastructure and model routing protocols for the upcoming release window.\n\n"
            "# Key Discussion Points & Findings\n"
            "- **Amit Ayer aligns on pipeline architecture:** Defined automated container builds and verification gates.\n\n"
            "# Action & Governance Ledger\n\n"
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Action | Deploy staging environment | Amit Ayer | by Friday | [00:14:00] | In Progress |\n"
            "| 2 | Action | Generic unowned thought | Unassigned | TBD | [00:25:00] | Open |\n\n"
            "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
            "# Amit Ayer - Engineering, Principal Architect\n"
            "- **Architecture**: Outlined delivery milestones and container boundaries.\n"
        )

        # 2. Gate X2 must fail-closed on unhealed raw draft due to lazy Unassigned | TBD row
        with self.assertRaises(ValueError) as ctx:
            xgates._validate_action_items(raw_draft_recap)
        self.assertIn("X2 FAIL", str(ctx.exception))
        self.assertIn("pairs unassigned owner", str(ctx.exception))

        # 3. Deterministic Table Healer processes the draft
        changes = []
        healed_recap = heal_output_2_table(raw_draft_recap, changes)

        # Verify relative date resolved to Friday 10/23/2026 (base date 10/17/2026 Saturday)
        self.assertIn("10/23/2026", healed_recap)
        self.assertIn("Amit Ayer", healed_recap)

        # Verify lazy Unassigned | TBD row pruned
        self.assertNotIn("Generic unowned thought", healed_recap)

        # 4. Gate X2 must pass cleanly on the healed recap
        xgates._validate_action_items(healed_recap)

        # 5. Exploratory meeting: all filler rows pruned -> clean confirmation sentinel
        exploratory_recap = (
            "**Date:** 10/17/2026\n\n"
            "# Action & Governance Ledger\n\n"
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Action | Open ideation | Unassigned | TBD | [00:10:00] | Open |\n"
            "| 2 | Action | Exploratory musing | Unassigned (Pending Owner Assignment) | Not stated | [00:20:00] | Open |\n"
        )
        exploratory_changes = []
        healed_exploratory = heal_output_2_table(exploratory_recap, exploratory_changes)

        self.assertIn(
            "No formal actions were established in this session; open initiatives remain unresolved questions.",
            healed_exploratory,
        )
        self.assertNotIn("| Open ideation |", healed_exploratory)
        xgates._validate_action_items(healed_exploratory)


if __name__ == "__main__":
    unittest.main()

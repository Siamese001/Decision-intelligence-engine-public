"""
tests/test_llm_judge_alignment_adversarial.py

Adversarial Edge-Case Suite for Wave 2:
Output 1, Output 2, and Output 3 LLM Judge & Healer Invariant Hardening.

Covers Mandatory Edge-Case Categories (per e2e-and-edge-case-coverage.md):
1. Invariant bypass attempt:
   - Attempted score inflation: Perfect deterministic checks cannot elevate lower LLM deductions.
   - Attempted generic predicate bypass: "RELATED_TO" and "CONTEXT_FOR" are strictly penalized.
2. Boundary payload & malformed input:
   - Whitespace-padded span coverage calculation.
   - Empty, malformed, or missing scorecards and candidate JSON payloads.
   - Zero-score preservation across dimensions in Output 2.
3. Exact production regression:
   - PartnerAdvisory_Gcp regression: 6 TRANSCRIPT_HITL records fail Gate 3.14 on multi-turn dialogue.
   - Preservation of valid evidence kinds (decision, risk, strategic) against healer collapse.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch
from typing import Any, Dict, List

from dual_pipeline.llm_judge_core.forensic_checks import assign_forensic_verdict
from dual_pipeline.llm_judge_core.graph_checks import (
    GENERIC_PREDICATES,
    apply_deterministic_graph_scoring,
)
from dual_pipeline.llm_judge_core.judges.judge_serialization import compute_span_coverage
from dual_pipeline.llm_judge_core.judges.recap import _apply_defect_penalties
from dual_pipeline.transcript_qa_core.gate3_evidence import (
    _is_collateral_evidence,
    check_gate_3_14,
    check_substantive_transcript_evidence,
)
from dual_pipeline.transcript_qa_core.graph_healer import (
    _heal_orphan_entities,
    _normalize_evidence_item,
)


class LLMJudgeAlignmentAdversarialTests(unittest.TestCase):
    """Adversarial suite testing boundary conditions, invariant bypasses, and exact regressions."""

    # -------------------------------------------------------------------------
    # 1. Invariant Bypass Attempts: Anti-Inflation & Predicate Hardening
    # -------------------------------------------------------------------------

    def test_invariant_bypass_output1_attribution_anti_inflation(self) -> None:
        """Adversarial: Attempting to inflate an LLM attribution deduction via clean deterministic text fails."""
        parsed_eval = {
            "dimension_1_forensic_grounding_and_fidelity": 25,
            "dimension_2_attribution_and_room_mic_hygiene": 10,  # 10/25 LLM score
            "dimension_3_chronological_and_timestamp_integrity": 25,
            "dimension_4_asr_cleanup_and_domain_preservation": 25,
            "composite_score": 85,
            "verdict": "PASS",
        }
        with patch(
            "dual_pipeline.transcript_qa_core.gate1_attribution.check_ambiguity_lock_register_and_speaker_hitl",
            return_value=(True, {}, []),
        ):
            result = assign_forensic_verdict(
                parsed=dict(parsed_eval),
                f1_text="## Forensic Transcript\n\n[00:00:01] Alice Smith: Hi\n[00:00:05] Bob Jones: Hello",
                metadata={},
            )
            self.assertEqual(
                result["dimension_2_attribution_and_room_mic_hygiene"],
                10,
                "Deterministic scoring must never inflate LLM attribution deduction upwards!",
            )

    def test_invariant_bypass_output3_generic_predicates_penalized(self) -> None:
        """Adversarial: Generic predicates RELATED_TO and CONTEXT_FOR must be penalized."""
        self.assertIn("RELATED_TO", GENERIC_PREDICATES)
        self.assertIn("CONTEXT_FOR", GENERIC_PREDICATES)
        self.assertIn("RELATES_TO", GENERIC_PREDICATES)

        parsed_eval = {
            "dimension_1_strategic_edge": 15,
            "dimension_2_knowledge_layer_density": 15,
            "dimension_3_semantic_atomicity": 20,
            "dimension_4_predicate_specificity": 15,  # Perfect initial LLM score
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 20,
            "composite_score": 100,
            "verdict": "PASS",
        }
        # Projection using adversarial generic predicates
        f3_data = {
            "entity_upserts": [
                {"id": "concept:a", "type": "concept", "name": "A"},
                {"id": "concept:b", "type": "concept", "name": "B"},
            ],
            "relationship_signals": [
                {"from_id": "concept:a", "to_id": "concept:b", "relationship": "RELATED_TO"},
                {"from_id": "concept:b", "to_id": "concept:a", "relationship": "CONTEXT_FOR"},
            ],
            "evidence": [],
        }
        res = apply_deterministic_graph_scoring(parsed_eval, f3_data, "")
        # Two generic relationships should trigger deterministic penalty on D4
        self.assertLess(
            res["dimension_4_predicate_specificity"],
            15,
            "Generic predicates RELATED_TO and CONTEXT_FOR must trigger deductions on D4!",
        )

    # -------------------------------------------------------------------------
    # 2. Boundary Payload & Malformed Inputs
    # -------------------------------------------------------------------------

    def test_boundary_span_coverage_whitespace_padding_not_truncated(self) -> None:
        """Adversarial: Span coverage comparison must strip whitespace to avoid false truncation."""
        candidate_bounded = "   [00:00:10] Alice: This is an important strategic point.   \n\n"
        candidate_full = "[00:00:10] Alice: This is an important strategic point."
        source_bounded = "   [00:00:10] Alice: This is an important strategic point.   \n\n"
        source_full = "[00:00:10] Alice: This is an important strategic point."

        is_complete, ratio = compute_span_coverage(
            source_bounded=source_bounded,
            source_full=source_full,
            candidate_bounded=candidate_bounded,
            candidate_full=candidate_full,
        )
        self.assertTrue(is_complete)
        self.assertAlmostEqual(ratio, 1.0, places=2)

    def test_boundary_output2_zero_score_preserved_during_rounding(self) -> None:
        """Adversarial: _apply_defect_penalties must never clobber an explicit 0 score up to 25."""
        parsed = {
            "dimension_1_executive_altitude": 0,  # Explicit zero
            "dimension_2_technical_substance": 25,
            "dimension_3_strategic_synthesis": 25,
            "dimension_4_scoping_and_modality": 25,
            "composite_score": 75,
        }
        res = _apply_defect_penalties(
            parsed=parsed,
            f2_text="## 1. Executive Summary\nWe will boil the ocean and synergize deliverables.\n## 5. Action & Governance Ledger\nNo formal action items were established during this session.",
            ref_artifact_text="",
        )
        self.assertEqual(
            res["dimension_1_executive_altitude"],
            0,
            "Explicit zero dimension score must not be clobbered by None-coalescing default 25!",
        )

    def test_boundary_empty_and_malformed_graph_projection_handling(self) -> None:
        """Adversarial: Empty or malformed Output 3 parsed dictionary handled gracefully."""
        parsed_eval = {
            "dimension_1_strategic_edge": 15,
            "dimension_2_knowledge_layer_density": 15,
            "dimension_3_semantic_atomicity": 20,
            "dimension_4_predicate_specificity": 15,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 20,
            "composite_score": 100,
        }
        # Completely empty projection dictionary
        res_empty = apply_deterministic_graph_scoring(dict(parsed_eval), {}, "")
        self.assertIn("verdict", res_empty)

        # Malformed mutation arrays (not lists)
        malformed = {
            "entity_upserts": "not a list",
            "relationship_signals": 12345,
            "evidence": None,
        }
        res_malformed = apply_deterministic_graph_scoring(dict(parsed_eval), malformed, "")
        self.assertIn("verdict", res_malformed)

    # -------------------------------------------------------------------------
    # 3. Exact Production Regressions (PartnerAdvisory_Gcp & Evidence Normalization)
    # -------------------------------------------------------------------------

    def test_exact_regression_partner_gcp_gate3_14_rejection(self) -> None:
        """Adversarial Regression: PartnerAdvisory_Gcp with 6 TRANSCRIPT_HITL evidence items fails Gate 3.14."""
        # 12-turn transcript representing the PartnerAdvisory GCP alignment meeting
        f1_transcript = """
[00:00:25] Anya Gayvoronskaya: Hi, Alex.
[00:00:26] Alex Brannan: Yeah. How are you?
[00:00:28] Bryan Armalavage: Hey, good afternoon folks.
[00:00:30] Robson N. Silva: Hey, good afternoon everyone.
[00:00:32] Amit Ayer: Hey everyone, can you guys hear me?
[00:03:30] Rusty Gattmann: Congratulations, you got awarded the automation RFP.
[00:04:26] Amit Ayer: We did, that's very big. We have to staff it now.
[00:05:19] Rusty Gattmann: PartnerAdvisory is the only one on this list that has the GCP skill sets.
[00:06:08] Amit Ayer: We're putting staffing in front of the client this week.
[00:07:00] Rusty Gattmann: Bring GCP resources from your organization.
[00:07:53] Alex Brannan: Pulling in Vani, she's the Google partner engineer aligned to PartnerAdvisory.
[00:08:48] Robson N. Silva: Welcome to the team, Vani.
"""
        # Exactly 6 TRANSCRIPT_HITL evidence items generated by incremental reconciliation
        partner_advisory_f3 = {
            "evidence_index": {
                "ev_hitl_role_amit_ayer": {
                    "statement_id": "ev_hitl_role_amit_ayer",
                    "text": "Human operator verified professional title 'Principal Architect' for Amit Ayer.",
                    "source": "TRANSCRIPT_HITL",
                    "source_type": "TRANSCRIPT_HITL",
                    "speaker": "Operator",
                    "locator": {"kind": "hitl_decision_ledger", "value": "res_001"},
                },
                "ev_hitl_role_alex_brannan": {
                    "statement_id": "ev_hitl_role_alex_brannan",
                    "text": "Human operator verified professional title 'Partner Director' for Alex Brannan.",
                    "source": "TRANSCRIPT_HITL",
                    "source_type": "TRANSCRIPT_HITL",
                    "speaker": "Operator",
                    "locator": {"kind": "hitl_decision_ledger", "value": "res_002"},
                },
                "ev_hitl_role_rusty_gattmann": {
                    "statement_id": "ev_hitl_role_rusty_gattmann",
                    "text": "Human operator verified professional title 'Alliances Lead' for Rusty Gattmann.",
                    "source": "TRANSCRIPT_HITL",
                    "source_type": "TRANSCRIPT_HITL",
                    "speaker": "Operator",
                    "locator": {"kind": "hitl_decision_ledger", "value": "res_003"},
                },
                "ev_hitl_role_dan_okray": {
                    "statement_id": "ev_hitl_role_dan_okray",
                    "text": "Human operator verified professional title 'Client Partner' for Drew Oakley.",
                    "source": "TRANSCRIPT_HITL",
                    "source_type": "TRANSCRIPT_HITL",
                    "speaker": "Operator",
                    "locator": {"kind": "hitl_decision_ledger", "value": "res_004"},
                },
                "ev_hitl_role_bryan_armalavage": {
                    "statement_id": "ev_hitl_role_bryan_armalavage",
                    "text": "Human operator verified professional title 'Senior Director' for Bryan Armalavage.",
                    "source": "TRANSCRIPT_HITL",
                    "source_type": "TRANSCRIPT_HITL",
                    "speaker": "Operator",
                    "locator": {"kind": "hitl_decision_ledger", "value": "res_005"},
                },
                "ev_hitl_role_vani_eswarappa": {
                    "statement_id": "ev_hitl_role_vani_eswarappa",
                    "text": "Human operator verified professional title 'Partner Engineer' for Vani Eswarappa.",
                    "source": "TRANSCRIPT_HITL",
                    "source_type": "TRANSCRIPT_HITL",
                    "speaker": "Operator",
                    "locator": {"kind": "hitl_decision_ledger", "value": "res_006"},
                },
            }
        }

        # Gate 3.14 MUST fail closed: 0 spoken dialogue evidence items
        sub_ok, sub_errs = check_substantive_transcript_evidence(
            data=partner_advisory_f3,
            f1_text=f1_transcript,
        )
        self.assertFalse(sub_ok, "Gate 3.14 must fail closed on PartnerAdvisory_Gcp when 0 spoken evidence exists!")
        self.assertTrue(any("spoken turns" in e.lower() or "spoken dialogue grounding" in e.lower() for e in sub_errs))

        passed, failures, details = check_gate_3_14(
            data=partner_advisory_f3,
            f1_text=f1_transcript,
        )
        self.assertFalse(passed)
        self.assertFalse(details["substantive_transcript_evidence"])

    def test_exact_regression_graph_healer_preserves_valid_evidence_kinds(self) -> None:
        """BUG-O3-04: _normalize_evidence_item must preserve decision, risk, strategic kinds."""
        for kind in ("decision", "risk", "strategic", "factual", "operational"):
            item = {"kind": kind, "text": "Test statement"}
            _normalize_evidence_item("ev1", item)
            self.assertEqual(
                item.get("kind"),
                kind,
                f"Evidence kind '{kind}' was unexpectedly overwritten!",
            )

        # Invalid or unknown kinds should normalize to summary
        invalid_item = {"kind": "unrecognized_custom_kind", "text": "Test"}
        _normalize_evidence_item("ev2", invalid_item)
        self.assertEqual(invalid_item.get("kind"), "summary")


if __name__ == "__main__":
    unittest.main()

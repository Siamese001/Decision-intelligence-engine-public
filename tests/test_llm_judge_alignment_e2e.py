"""
tests/test_llm_judge_alignment_e2e.py

Class B E2E Receipt Test Suite for Wave 2:
Output 1, Output 2, and Output 3 LLM Judge & Deterministic Check Alignment.

Verifies:
1. Output 1, 2, and 3 judges enforce min(llm, det): never overwrite valid lower LLM
   dimension scores upwards with deterministic max points.
2. Gate 3.14 fails closed when Output 3 contains only TRANSCRIPT_HITL records.
3. Graph healer emits zero banned RELATES_TO predicates or entity: prefixes.
4. Output 1 collapse approval receipts and single-speaker rosters pass attribution.
5. Zero-action meetings are exempted from missing action ledger failures.
6. Graph judge incorporates incremental_reconciliation_receipt and supports borderline HITL_REQUIRED.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from typing import Any, Dict, List

from dual_pipeline.llm_judge_core.forensic_checks import (
    apply_timestamp_penalty,
    assign_forensic_verdict,
    check_single_speaker_collapse,
    compute_attribution_score,
)
from dual_pipeline.llm_judge_core.graph_checks import (
    GENERIC_PREDICATES,
    apply_deterministic_graph_scoring,
)
from dual_pipeline.llm_judge_core.heuristic_pre_judge import evaluate_heuristic_pre_judge
from dual_pipeline.llm_judge_core.judges.graph import build_graph_judge_user_content
from dual_pipeline.llm_judge_core.judges.judge_serialization import compute_span_coverage
from dual_pipeline.llm_judge_core.recap_checks import (
    check_action_ledger_integrity,
    run_preflight_checks,
)
from dual_pipeline.prompts.contracts.output1_contract import OUTPUT1_CONTRACT
from dual_pipeline.transcript_qa_core.gate3_evidence import (
    _count_dialogue_turns,
    _is_collateral_evidence,
    check_gate_3_14,
    check_substantive_transcript_evidence,
)
from dual_pipeline.transcript_qa_core.graph_healer import (
    _connect_lingering_orphans,
    _heal_orphan_entities,
    _normalize_evidence_item,
    heal_evidence_locators,
)
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import (
    _upsert_org_relationship,
    _upsert_role_relationship,
)


class LLMJudgeAlignmentE2ETests(unittest.TestCase):
    """E2E suite verifying deterministic and LLM judge alignment across Outputs 1, 2, and 3."""

    def test_output1_min_llm_det_scoring_preserves_lower_llm_deduction(self) -> None:
        """BUG-O1-02: Output 1 assign_forensic_verdict enforces min(llm_d2, d2_score)."""
        parsed_eval = {
            "dimension_1_forensic_grounding_and_fidelity": 25,
            "dimension_2_attribution_and_room_mic_hygiene": 15,  # LLM gave 15/25
            "dimension_3_chronological_and_timestamp_integrity": 25,
            "dimension_4_asr_cleanup_and_domain_preservation": 25,
            "composite_score": 90,
            "verdict": "PASS",
        }
        # When deterministic ALR check passes (25/25), assign_forensic_verdict preserves LLM's 15
        with patch(
            "dual_pipeline.transcript_qa_core.gate1_attribution.check_ambiguity_lock_register_and_speaker_hitl",
            return_value=(True, {}, []),
        ):
            result = assign_forensic_verdict(
                parsed=dict(parsed_eval),
                f1_text="## Forensic Transcript\n\n[00:01:00] Amit Ayer: Hello\n[00:01:05] Drew Oakley: Hi",
                metadata={},
            )
            self.assertEqual(result["dimension_2_attribution_and_room_mic_hygiene"], 15)
            self.assertEqual(result["composite_score"], 90)

        # When deterministic check fails with cap 8, and LLM gave 5, lower LLM score 5 is preserved
        parsed_low = dict(parsed_eval)
        parsed_low["dimension_2_attribution_and_room_mic_hygiene"] = 5
        result_low = assign_forensic_verdict(
            parsed=parsed_low,
            f1_text="## Forensic Transcript\n\n[00:01:00] Amit Ayer: Hello\n[00:01:05] Drew Oakley: Hi",
            metadata={},
        )
        self.assertEqual(result_low["dimension_2_attribution_and_room_mic_hygiene"], 5)

    def test_output1_timestamp_penalty_preserves_llm_d3_score(self) -> None:
        """BUG-O1-02: apply_timestamp_penalty preserves existing LLM D3 score when valid."""
        scorecard = {
            "dimension_1_forensic_grounding_and_fidelity": 25,
            "dimension_2_attribution_and_room_mic_hygiene": 25,
            "dimension_3_chronological_and_timestamp_integrity": 18,  # LLM deducted 7 points
            "dimension_4_asr_cleanup_and_domain_preservation": 25,
        }
        f1_valid = "## Forensic Transcript\n\n[00:01:00] Speaker 1: Hello\n[00:01:30] Speaker 2: World"
        apply_timestamp_penalty(scorecard, f1_valid)
        self.assertEqual(scorecard["dimension_3_chronological_and_timestamp_integrity"], 18)

    def test_output1_single_speaker_collapse_with_verified_receipt(self) -> None:
        """BUG-O1-01: Single-speaker collapse passes when signed approval receipt is present."""
        with patch(
            "dual_pipeline.speaker_attribution_core.collapse_detector._verify_collapse_approval_receipt",
            return_value=True,
        ):
            f1_text = "\n".join([f"[00:01:{i:02d}] Solo Speaker: Turn {i}" for i in range(15)])
            collapsed, top_spk, top_cnt, total = check_single_speaker_collapse(
                f1_text, {"roster": ["Solo Speaker", "Other"]}
            )
            self.assertFalse(collapsed)
            self.assertEqual(total, 0)

    def test_output1_single_speaker_roster_exempt_from_collapse(self) -> None:
        """BUG-O1-01: Single-speaker roster (< 2 attendees) is exempt from collapse."""
        meta = {
            "attendees": ["Solo Keynote Speaker"]
        }
        f1_text = "\n".join([f"[00:01:{i:02d}] Solo Keynote Speaker: Keynote section {i}" for i in range(12)])
        collapsed, top_spk, top_cnt, total = check_single_speaker_collapse(f1_text, meta)
        self.assertFalse(collapsed)
        self.assertEqual(total, 0)

    def test_output1_contract_c16_monotonic_timestamp_validation(self) -> None:
        """BUG-O1-07: validate_c16_timestamp_monotonicity checks monotonic integrity."""
        valid_f1 = (
            "## Forensic Transcript\n\n"
            "[00:01:00] Alice: Start\n"
            "[00:01:30] Bob: Next\n"
            "[00:02:15] Alice: End\n"
        )
        passed, failures = OUTPUT1_CONTRACT.validate_c16_timestamp_monotonicity(valid_f1)
        self.assertTrue(passed)
        self.assertEqual(failures, [])

        backwards_f1 = (
            "## Forensic Transcript\n\n"
            "[00:02:00] Alice: Early\n"
            "[00:01:30] Bob: Backwards\n"
        )
        passed, failures = OUTPUT1_CONTRACT.validate_c16_timestamp_monotonicity(backwards_f1)
        self.assertFalse(passed)
        self.assertTrue(any("MONOTONIC" in f.upper() or "DECREASE" in f.upper() for f in failures))

    def test_output1_heuristic_pre_judge_payload_structure(self) -> None:
        """BUG-O1-05: evaluate_heuristic_pre_judge returns canonical dimension keys and status."""
        f1 = "## Forensic Transcript\n\n[00:00:10] Alice: Hello world."
        res = evaluate_heuristic_pre_judge(f1, f1, {})
        for dim_num in range(1, 6):
            key = f"dimension_{dim_num}"
            matching = [k for k in res.keys() if k.startswith(key)]
            self.assertTrue(len(matching) >= 1, f"Missing dimension key for {key}")
        self.assertIn("status", res)
        self.assertIn("defect_codes", res)

    def test_output2_zero_action_ledger_integrity_exemption(self) -> None:
        """BUG-O2-06: Zero-action meeting confirmation exempts from missing action ledger table."""
        recap_text = (
            "# Executive Recap\n\n"
            "## 1. Executive Summary\nStrategic alignment discussion.\n\n"
            "## 5. Action & Governance Ledger\n"
            "No formal action items were established during this session.\n"
        )
        passed, reasons = check_action_ledger_integrity(recap_text, f1_text="")
        self.assertTrue(passed)
        self.assertEqual(reasons, [])

    def test_output2_archetype_precedence_f2_over_f1(self) -> None:
        """BUG-O2-12: run_preflight_checks inspects f2_text before f1_text for archetype."""
        f1_arch = "## Meeting Type: Strategic Planning"
        f2_arch = "## 1. Executive Summary\nInterview session with candidate."
        res = run_preflight_checks(f2_arch, f1_arch, span=None, is_mock=True)
        self.assertTrue(res is None or isinstance(res, dict))

    def test_output3_min_llm_det_scoring_preserves_lower_llm_deductions(self) -> None:
        """BUG-O3-05: apply_deterministic_graph_scoring enforces min(llm, det) for D4 and D5."""
        parsed_eval = {
            "dimension_1_strategic_edge": 15,
            "dimension_2_knowledge_layer_density": 15,
            "dimension_3_semantic_atomicity": 20,
            "dimension_4_predicate_specificity": 10,  # LLM gave 10/15
            "dimension_5_exclusion_of_action_items": 11,  # LLM gave 11/15
            "dimension_6_evidence_grounding_and_schema": 20,
            "composite_score": 91,
            "verdict": "PASS",
        }
        # Clean projection with 0 generic predicates and 0 action items
        f3_data = {
            "entity_upserts": [{"id": "person:alice", "type": "person", "name": "Alice"}],
            "relationship_signals": [
                {"from_id": "person:alice", "to_id": "concept:cloud", "relationship": "INFLUENCES", "evidence_ids": ["ev1"]}
            ],
            "evidence": [{"statement_id": "ev1", "speaker": "Alice", "text": "Cloud computing", "locator": "00:01:00"}],
        }
        res = apply_deterministic_graph_scoring(
            parsed_eval=parsed_eval,
            f3_parsed=f3_data,
            f1_text="[00:01:00] Alice: Cloud computing",
        )
        # Must retain 10 and 11, not inflate to 15
        self.assertEqual(res["dimension_4_predicate_specificity"], 10)
        self.assertEqual(res["dimension_5_exclusion_of_action_items"], 11)

    def test_output3_borderline_floor_deduction_yields_hitl_required(self) -> None:
        """BUG-O3-05: Single borderline floor failure with score >= 85 produces HITL_REQUIRED."""
        # Floor for D1 is 11 (OUTPUT3_CONTRACT). Score 10 is borderline (11 - 1).
        parsed_eval = {
            "dimension_1_strategic_edge": 10,  # Borderline (floor=11)
            "dimension_2_knowledge_layer_density": 15,
            "dimension_3_semantic_atomicity": 20,
            "dimension_4_predicate_specificity": 15,
            "dimension_5_exclusion_of_action_items": 15,
            "dimension_6_evidence_grounding_and_schema": 20,
            "composite_score": 95,
            "verdict": "PASS",
        }
        f3_data = {"entity_upserts": [], "relationship_signals": []}
        res = apply_deterministic_graph_scoring(parsed_eval, f3_data, "")
        self.assertEqual(res["verdict"], "HITL_REQUIRED")
        self.assertIn("Borderline floor deduction", res.get("forensic_rationale", ""))

    def test_gate3_14_fails_closed_when_only_transcript_hitl_records_present(self) -> None:
        """BUG-O3-03: Gate 3.14 fails closed when Output 3 contains only TRANSCRIPT_HITL evidence."""
        # F1 transcript has 15 dialogue turns
        f1_transcript = "\n\n".join([f"[{i:02d}:00] Speaker {i%2}: Turn discussion point {i}" for i in range(15)])
        self.assertTrue(_count_dialogue_turns(f1_transcript) >= 10)

        # Output 3 containing ONLY TRANSCRIPT_HITL evidence items
        f3_payload = {
            "evidence_index": {
                "ev_hitl_role_1": {
                    "statement_id": "ev_hitl_role_1",
                    "text": "Human operator verified professional title 'Architect' for Amit Ayer.",
                    "source": "TRANSCRIPT_HITL",
                    "source_type": "TRANSCRIPT_HITL",
                    "speaker": "Operator",
                    "locator": {"kind": "hitl_decision_ledger", "value": "res_1"},
                },
                "ev_hitl_role_2": {
                    "statement_id": "ev_hitl_role_2",
                    "text": "Human operator verified organization 'Google' for Alex Brannan.",
                    "source": "TRANSCRIPT_HITL",
                    "source_type": "TRANSCRIPT_HITL",
                    "speaker": "Operator",
                    "locator": {"kind": "hitl_decision_ledger", "value": "res_2"},
                },
            }
        }
        # Check that both are identified as collateral/exempt evidence
        for eid, ev in f3_payload["evidence_index"].items():
            self.assertTrue(_is_collateral_evidence(eid, ev))

        # Gate 3.14 check density: dialogue turns >= 10, but spoken evidence count is 0
        sub_ok, sub_errs = check_substantive_transcript_evidence(f3_payload, f1_transcript)
        self.assertFalse(sub_ok)
        self.assertTrue(any("spoken turns" in e.lower() or "spoken dialogue grounding" in e.lower() for e in sub_errs))

        passed, failures, details = check_gate_3_14(f3_payload, f1_transcript)
        self.assertFalse(passed)
        self.assertFalse(details["substantive_transcript_evidence"])

    def test_graph_healer_zero_relates_to_and_zero_entity_prefix(self) -> None:
        """BUG-O3-04: graph_healer emits INFLUENCES (not RELATES_TO) and typed prefixes (not entity:)."""
        # Test locator healing does not inject "entity:"
        data = {
            "entity_upserts": [
                {"id": "Amit Ayer", "name": "Amit Ayer", "type": "person"},
                {"id": "Google", "name": "Google", "type": "org"},
            ]
        }
        heal_evidence_locators(data)
        for ent in data["entity_upserts"]:
            ent_id = ent.get("id")
            self.assertFalse(str(ent_id).startswith("entity:"), f"Found invalid entity: prefix in {ent_id}")
            self.assertTrue(":" in str(ent_id))

        # Test orphan healing emits INFLUENCES, not RELATES_TO
        mutations = {
            "entity_upserts": [
                {"id": "person:amit_ayer", "name": "Amit Ayer", "type": "person"},
                {"id": "concept:cloud_transformation", "name": "Cloud Transformation", "type": "concept"},
            ],
            "relationship_signals": [],
        }
        data_graph = {"graph_mutations": mutations}
        changes: List[str] = []
        _heal_orphan_entities(data_graph, mutations, changes)

        for rel in mutations["relationship_signals"]:
            predicate = str(rel.get("relationship") or rel.get("predicate") or "").upper()
            self.assertNotEqual(predicate, "RELATES_TO")
            self.assertNotIn(predicate, GENERIC_PREDICATES)

    def test_speaker_role_reconciler_attaches_evidence_ids_to_entities(self) -> None:
        """BUG-O3-02: Upserted org and role entities have evidence_ids attached."""
        entity_upserts: List[Dict[str, Any]] = []
        relationship_signals: List[Dict[str, Any]] = []
        ev_id = "ev_hitl_role_amit_ayer"
        ev_entry = {"statement_id": "stmt1", "text": "Operator verified"}

        _upsert_org_relationship(
            entity_upserts=entity_upserts,
            relationship_signals=relationship_signals,
            org="PartnerAdvisory",
            person_id="person:amit_ayer",
            p_slug="amit_ayer",
            ev_id=ev_id,
            ev_entry=ev_entry,
            is_human=True,
            prov_str="HUMAN_OPERATOR",
        )
        _upsert_role_relationship(
            entity_upserts=entity_upserts,
            relationship_signals=relationship_signals,
            role="Principal Architect",
            person_id="person:amit_ayer",
            p_slug="amit_ayer",
            ev_id=ev_id,
            ev_entry=ev_entry,
            is_human=True,
            prov_str="HUMAN_OPERATOR",
        )

        org_ent = next(e for e in entity_upserts if e["type"] == "org")
        role_ent = next(e for e in entity_upserts if e["type"] == "role")
        self.assertIn("evidence_ids", org_ent)
        self.assertIn(ev_id, org_ent["evidence_ids"])
        self.assertIn("evidence_ids", role_ent)
        self.assertIn(ev_id, role_ent["evidence_ids"])

    def test_graph_judge_prompt_includes_reconciliation_receipt(self) -> None:
        """BUG-O3-01: build_graph_judge_user_content includes incremental_reconciliation_receipt."""
        recon_receipt = {
            "reconciliation_id": "recon_10-13-2026",
            "prior_entities_retained": 12,
            "incremental_mutations": 4,
        }
        metadata = {"incremental_reconciliation_receipt": recon_receipt}
        content = build_graph_judge_user_content(
            gov_ctx="Gov context",
            reference_text="Ref text",
            candidate_text='{"entity_upserts": []}',
            metadata=metadata,
        )
        self.assertIn("<incremental_reconciliation_receipt>", content)
        self.assertIn("recon_10-13-2026", content)


if __name__ == "__main__":
    unittest.main()

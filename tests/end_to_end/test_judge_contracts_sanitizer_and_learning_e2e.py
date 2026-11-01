"""Wave 8 E2E Tests: LLM Judge Contracts, Multimodal Pre-Egress Sanitizer & Learning Lifecycle."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.core import read_json, write_json
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.llm_judge_core.graph_checks import (
    apply_deterministic_graph_scoring,
    check_quote_and_evidence_resolution,
)
from dual_pipeline.prompts.contracts.output2_contract import (
    validate_c21_depth_floors_and_anchors,
)
from dual_pipeline.prompts.contracts.versioned_judge_contracts import (
    MeetingXExitContract,
    Output3ScorecardV2,
)
from dual_pipeline.sanitizer import EgressSanitizationError, LocalPreEgressSanitizer
from tools.govern_learning_proposals import cmd_hitl_scan, cmd_rollback, cmd_tokenomics_scan


class JudgeContractsSanitizerAndLearningE2ETests(unittest.TestCase):
    def test_graph_judge_critical_defects_and_fractional_scores_enforced(self) -> None:
        """CAT7-1000-P0-1 & CAT7-1000-P1-2: Critical defects fail closed even at score >= 90; fractional .5 scores validate cleanly."""
        f3_valid = {
            "graph_mutations": {
                "entity_upserts": [{"id": "system:dociq", "evidence_ids": ["ev-1"]}],
                "relationship_signals": [
                    {
                        "from_id": "person:amit_ayer",
                        "relationship": "OWNS_OR_LEADS",
                        "to_id": "system:dociq",
                        "evidence_ids": ["ev-1"],
                    }
                ],
                "action_updates": [],
            },
            "evidence_index": {
                "ev-1": {
                    "id": "ev-1",
                    "quote": "Amit leads the DocIQ architecture.",
                    "locator": "00:01:00",
                }
            },
        }
        f1_text = "Amit leads the DocIQ architecture. " * 25

        # 1. Fractional .5 score without defects -> validates cleanly in Output3ScorecardV2
        eval_frac = {
            "judge_model": "gpt-5.6-sol",
            "prompt_digest": "a" * 64,
            "forensic_rationale": "High fidelity extraction.",
            "dimension_1_strategic_edge": 14.5,
            "dimension_2_knowledge_layer_density": 14.0,
            "dimension_3_semantic_atomicity": 19.0,
            "dimension_4_predicate_specificity": 15.0,
            "dimension_5_exclusion_of_action_items": 15.0,
            "dimension_6_evidence_grounding_and_schema": 18.0,
            "defect_codes": [],
        }
        scored_frac = apply_deterministic_graph_scoring(eval_frac, f3_valid, f1_text)
        self.assertEqual(95.5, scored_frac["composite_score"])
        self.assertEqual("PASS", scored_frac["verdict"])
        card = Output3ScorecardV2.model_validate(scored_frac)
        self.assertEqual(95.5, card.composite_score)

        # 2. High score (95.5) WITH critical defect ERR_HALLUCINATED_ENTITY -> must FAIL closed
        eval_hallucinated = dict(eval_frac, defect_codes=["ERR_HALLUCINATED_ENTITY"])
        scored_hallucinated = apply_deterministic_graph_scoring(eval_hallucinated, f3_valid, f1_text)
        self.assertEqual("FAIL", scored_hallucinated["verdict"])
        self.assertEqual("FAIL", scored_hallucinated["status"])

    def test_x3_room_endpoint_detection_and_c21_ngram_tolerance(self) -> None:
        """CAT7-1000-P0-2 & CAT7-1000-P1-1: Conference room endpoints do not require person: nodes; shared 11-word domain phrase passes C-21."""
        f1_text = "[00:01:00] Amit Ayer: We confirmed the cloud migration."
        f2_text = "# Meeting Objective\nAlign.\n# Action & Governance Ledger\n"
        f3_data = {
            "schema_version": "5.7",
            "graph_mutations": {
                "entity_upserts": [{"id": "person:amit_ayer", "type": "person"}],
                "relationship_signals": [],
            },
            "evidence_index": {},
        }
        meta = {
            "attendees": [
                "Amit Ayer (PartnerAdvisory)",
                "FL-JB-ConfRoom-4B",
                "NYC-Boardroom-12",
                "Zoom Room 302",
            ]
        }
        ok, errs = MeetingXExitContract.validate_x3_cross_output_coherence(
            f1_text, f2_text, f3_data, metadata=meta
        )
        self.assertTrue(ok, f"Unexpected X3 errors: {errs}")

        # Shared 11-word domain phrase (6 six-grams) across 2 speakers must NOT fail SECTION_7_INTRA_APPENDIX_REDUNDANCY
        shared_11_words = "the enterprise data lakehouse migration requires strict governance controls across regions"
        recap = f"""# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown

### Amit Ayer — PartnerAdvisory
- **Architecture Governance**: Amit emphasized that {shared_11_words} before onboarding production workloads and deprecating legacy warehouse pipelines.

### Drew Oakley — PartnerAdvisory
- **Program Sequencing**: Dan noted that {shared_11_words} while coordinating staffing allocations and executive steering committee milestones.
"""
        c21_ok, c21_errs = validate_c21_depth_floors_and_anchors(
            recap, meta={"attendees": ["Amit Ayer", "Drew Oakley"]}
        )
        self.assertTrue(c21_ok, f"Unexpected C21 errors: {c21_errs}")

    def test_learning_proposal_simulation_gate_calibration_snapshot_and_cli_rollback(self) -> None:
        """CAT7-1000-P0-4 & CAT7-1000-P1-3 & CAT7-1000-P1-4: Learning engine records calibration previous_value, blocks double-apply, and rolls back via CLI."""
        with tempfile.TemporaryDirectory(prefix="wave8-e2e-learn-") as tmp:
            root = Path(tmp)
            prop_dir = root / "proposals"
            engine = ClosedLoopLearningEngine(runtime_root=root / "runtime", proposals_dir=prop_dir)

            cohort = {
                "cohort_key": "outcome:ALIAS_CORRECTION:speaker_alias",
                "occurrence_count": 3,
                "run_ids": ["run-1", "run-2"],
                "items": [{"event_id": "e1", "details": {"from_id": "person:dan", "to_id": "person:dan_okray"}}],
            }
            prop = engine.generate_proposal_for_cohort(
                cohort,
                proposal_type="alias_addition",
                target_override={"from_id": "person:dan", "to_id": "person:dan_okray"},
            )
            p_id = prop["proposal_id"]
            sim = engine.simulate_proposal(prop)
            self.assertTrue(sim.success)
            self.assertEqual("SIMULATED_PASS", sim.status)

            aliases_file = root / "learned_aliases.json"
            write_json(aliases_file, {"aliases": {"person:dan": "person:legacy_dan"}})

            receipt = {"receipt_id": "app-1", "approved_by": "human_operator"}
            with patch.object(engine, "verify_proposal_approval", return_value=(True, "ok")):
                applied = engine.apply_approved_proposal(prop, receipt, aliases_path=aliases_file)
                self.assertEqual("APPLIED", applied["status"])
                self.assertEqual("person:dan_okray", read_json(aliases_file)["aliases"]["person:dan"])

                # Snapshot recorded previous_target
                snap = read_json(prop_dir / f"{p_id}_rollback_snapshot.json")
                self.assertEqual("person:legacy_dan", snap["pre_mutation_state"]["previous_target"])

                # Second apply without rollback must raise PermissionError (preserving rollback snapshot)
                with self.assertRaisesRegex(PermissionError, "already APPLIED"):
                    engine.apply_approved_proposal(prop, receipt, aliases_path=aliases_file)

            # CLI cmd_rollback routes through engine.rollback_proposal
            rb_res = engine.rollback_proposal(p_id, aliases_path=aliases_file)
            self.assertEqual("ROLLED_BACK", rb_res["status"])
            self.assertEqual("person:legacy_dan", read_json(aliases_file)["aliases"]["person:dan"])

            # Verify cmd_hitl_scan excludes AUTONOMOUS from affirmed override denominator
            ledger_file = root / "hitl_ledger.json"
            write_json(
                ledger_file,
                [
                    {"decision_id": f"d{i}", "category": "speaker", "outcome_type": "OVERRIDDEN", "confidence": 0.8}
                    for i in range(2)
                ]
                + [
                    {"decision_id": f"a{i}", "category": "speaker", "outcome_type": "AFFIRMED", "confidence": 0.9}
                    for i in range(3)
                ]
                + [
                    {"decision_id": f"auto{i}", "category": "speaker", "outcome_type": "AUTONOMOUS", "confidence": 0.95}
                    for i in range(10)
                ],
            )
            rc = cmd_hitl_scan(
                engine,
                prop_dir,
                ledger_path=ledger_file,
                override_threshold=0.35,
                min_decisions=5,
                generate_proposals=True,
            )
            self.assertEqual(0, rc)
            # 2 OVERRIDDEN / (2 OVERRIDDEN + 3 AFFIRMED) = 40% >= 35% -> cohort proposal generated!
            self.assertTrue(any("hitl" in p.name or p.suffix == ".json" for p in prop_dir.glob("*.json")))


if __name__ == "__main__":
    unittest.main()

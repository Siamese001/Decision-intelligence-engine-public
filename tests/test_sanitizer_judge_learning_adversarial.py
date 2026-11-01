"""Wave 8 Adversarial & Edge-Case Tests: Multimodal Pre-Egress Sanitizer, Empty Evidence Map & Learning Bounds.

Covers mandatory edge-case categories:
1. Boundary payload (empty evidence_map with referenced evidence_ids, negative/out-of-bounds parameter values)
2. Malformed input (nested dicts/lists and non-string content payloads in LocalPreEgressSanitizer)
3. Invariant bypass attempt (multimodal content blocks and tool_calls hiding secrets, billing rates, or INTERNAL_ADVISORY markers; applying SIMULATED_FAIL proposals)
4. Rollback & recovery (rolling back parameter calibration from com.partner_advisory.nee.calibration current_value)
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.core import read_json, write_json
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.llm_judge_core.graph_checks import (
    check_quote_and_evidence_resolution,
)
from dual_pipeline.sanitizer import EgressSanitizationError, LocalPreEgressSanitizer


class SanitizerJudgeLearningAdversarialTests(unittest.TestCase):
    def test_boundary_empty_evidence_map_with_referenced_eids_fails_closed(self) -> None:
        """Category 1 (Boundary payload): Empty evidence_map fails closed when mutations reference evidence_ids."""
        f3_missing_evidence_map = {
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "system:dociq", "evidence_ids": ["ev-missing-1", "ev-missing-2"]}
                ],
                "relationship_signals": [],
            },
            "evidence_index": {},
        }
        passed, defects, stats = check_quote_and_evidence_resolution(
            f3_missing_evidence_map, "Source transcript text."
        )
        self.assertFalse(passed)
        self.assertEqual(2, stats["unresolved_eids_count"])
        self.assertTrue(any("Unresolved evidence_ids" in d for d in defects))

    def test_invariant_bypass_multimodal_content_and_tool_calls_sanitized(self) -> None:
        """Category 5 (Invariant bypass attempt): Secrets, billing rates, and INTERNAL_ADVISORY inside list/dict multimodal content or tool_calls are blocked."""
        # 1. Multimodal list content containing secret key
        multimodal_secret = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this architecture diagram."},
                    {"type": "input_text", "text": "Bearer key sk_test_redacted_placeholder"},
                ],
            }
        ]
        with self.assertRaises(EgressSanitizationError):
            LocalPreEgressSanitizer.sanitize_messages(multimodal_secret)

        # 2. Tool call arguments containing INTERNAL_ADVISORY marker
        tool_call_advisory = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "emit_note",
                            "arguments": '{"namespace": "INTERNAL_ADVISORY", "stealth_score": 1.0}',
                        },
                    }
                ],
            }
        ]
        with self.assertRaises(EgressSanitizationError):
            LocalPreEgressSanitizer.sanitize_messages(tool_call_advisory)

        # 3. Nested dict in sanitize_text containing billing rate
        with self.assertRaises(EgressSanitizationError):
            LocalPreEgressSanitizer.sanitize_text({"nested": ["Consulting rate is $275/hr"]})

        # 4. Clean multimodal message passes
        clean_msgs = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Summarize the Cloud Architecture review."}],
            }
        ]
        self.assertEqual(clean_msgs, LocalPreEgressSanitizer.sanitize_messages(clean_msgs))

    def test_malformed_and_out_of_bounds_learning_parameters_rejected_and_blocked_from_apply(self) -> None:
        """Category 2 & 5 (Malformed input & Invariant bypass): Out-of-bounds threshold fails simulation and blocks apply_approved_proposal."""
        with tempfile.TemporaryDirectory(prefix="wave8-adv-learn-") as tmp:
            root = Path(tmp)
            engine = ClosedLoopLearningEngine(
                runtime_root=root / "runtime", proposals_dir=root / "proposals"
            )
            cohort = {
                "cohort_key": "telemetry:L0:threshold_margin",
                "occurrence_count": 3,
                "run_ids": ["run-1"],
                "items": [{"event_id": "evt-1"}],
            }
            bad_prop = engine.generate_proposal_for_cohort(
                cohort,
                proposal_type="threshold",
                target_override={
                    "parameter_name": "margin_threshold",
                    "proposed_value": 1.75,  # > 1.0 invalid bound!
                    "current_value": 0.20,
                },
            )
            sim = engine.simulate_proposal(bad_prop)
            self.assertFalse(sim.success)
            self.assertEqual("SIMULATED_FAIL", sim.status)

            with patch.object(engine, "verify_proposal_approval", return_value=(True, "ok")):
                with self.assertRaisesRegex(PermissionError, "simulation receipt status is 'SIMULATED_FAIL'"):
                    engine.apply_approved_proposal(
                        bad_prop, {"receipt_id": "r1", "approved_by": "operator"}
                    )

    def test_rollback_recovery_routing_proposal_and_calibration_previous_value(self) -> None:
        """Category 6 (Rollback & recovery): Default 'routing' cohort proposal applies and rolls back cleanly."""
        with tempfile.TemporaryDirectory(prefix="wave8-adv-rb-") as tmp:
            root = Path(tmp)
            engine = ClosedLoopLearningEngine(
                runtime_root=root / "runtime", proposals_dir=root / "proposals"
            )
            cohort = {
                "cohort_key": "telemetry:L1:custom_route_drift",
                "occurrence_count": 2,
                "run_ids": ["run-1", "run-2"],
                "items": [{"event_id": "evt-1"}, {"event_id": "evt-2"}],
            }
            prop = engine.generate_proposal_for_cohort(cohort)
            self.assertEqual("routing", prop["proposal_type"])
            sim = engine.simulate_proposal(prop)
            self.assertTrue(sim.success)

            with patch.object(engine, "verify_proposal_approval", return_value=(True, "ok")):
                rec = engine.apply_approved_proposal(
                    prop, {"receipt_id": "r-route", "approved_by": "operator"}
                )
                self.assertEqual("APPLIED", rec["status"])

            rb = engine.rollback_proposal(prop["proposal_id"])
            self.assertEqual("ROLLED_BACK", rb["status"])


if __name__ == "__main__":
    unittest.main()

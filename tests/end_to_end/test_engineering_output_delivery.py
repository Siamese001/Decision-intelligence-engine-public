"""End-to-end tests for engineering output delivery, surface authority, and reduction.

Verifies:
1. Exact delivered text equals checked text; post-validation mutations fail closed.
2. Missing checks or invalid text cannot silently fall back to success.
3. Delivery surfaces correctly map to machine-enforced vs instruction-only authority.
4. Host chat-stream interception is truthfully marked instruction-only / unsupported.
5. Authored text achieves >90% word and character reduction vs frozen RCA baseline.
6. End-to-end summary validation and readiness gate integration.
7. Non-engineering routes (query, copilot, HITL) retain existing behavior.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.engineering_output import (
    BASELINE_AUTHORED_CHARS,
    BASELINE_AUTHORED_WORDS,
    DeliverySurface,
    EnforcementAuthority,
    ExecutionEnvironment,
    Observation,
    compute_content_digest,
    deliver_output,
    measure_output_reduction,
    render_concise_response,
    render_deliverables_completion,
    verify_deliverable_links,
)
from tools.validate_implementation_summary import validate_summary_content
from tools.validate_turn_gates import evaluate_hitl_ambiguity, validate_post_turn_gate

ROOT = Path(__file__).resolve().parents[2]


class TestEngineeringOutputDeliveryE2E(unittest.TestCase):
    """End-to-end suite verifying delivery boundary and output reduction."""

    def test_exact_delivered_text_equals_checked_text(self) -> None:
        """Verify that delivered text matches validated text and post-validation edits fail."""
        text = (
            "Ran unit test suite with 10 test fixtures in local environment. "
            "Observed all 10 tests passed without errors. Ready for release."
        )
        digest = compute_content_digest(text)

        # 1. Delivery with matching digest succeeds
        res_ok = deliver_output(
            surface=DeliverySurface.REPOSITORY_RESPONSE,
            text=text,
            verified_digest=digest,
        )
        self.assertTrue(res_ok.is_delivered)
        self.assertEqual(res_ok.delivered_text, text)
        self.assertEqual(res_ok.content_digest, digest)

        # 2. Tampered / modified text after validation fails closed
        tampered_text = text + " Extra unvalidated claim that everything is perfect."
        res_tampered = deliver_output(
            surface=DeliverySurface.REPOSITORY_RESPONSE,
            text=tampered_text,
            verified_digest=digest,  # Digest of original, not tampered!
        )
        self.assertFalse(res_tampered.is_delivered)
        self.assertIsNone(res_tampered.delivered_text)
        self.assertTrue(any("Post-validation mutation detected" in e for e in res_tampered.errors))

    def test_missing_checks_cannot_silently_fall_back_to_success(self) -> None:
        """Verify that invalid or unchecked text cannot be delivered as success."""
        # 1. Oversized text
        oversized = "word " * 125
        res_over = deliver_output(
            surface=DeliverySurface.SAVED_SUMMARY,
            text=oversized,
        )
        self.assertFalse(res_over.is_delivered)
        self.assertIsNone(res_over.delivered_text)
        self.assertIn("exceeds maximum allowed ceiling of 120 words", res_over.rejection_reason or "")

        # 2. Procedural scratchpad included
        scratchpad_text = (
            "### 🧠 Agent Reasoning Scratchpad\n"
            "- **Active Model & Provider**: gpt-4\n"
            "Ran test in local. Observed pass."
        )
        res_sp = deliver_output(
            surface=DeliverySurface.SAVED_SUMMARY,
            text=scratchpad_text,
        )
        self.assertFalse(res_sp.is_delivered)
        self.assertIsNone(res_sp.delivered_text)
        self.assertIn("Forbidden process section detected", res_sp.rejection_reason or "")

    def test_surface_classification_and_host_enforcement_truth(self) -> None:
        """Verify surface authority classification and accurate host limitation reporting."""
        # Repository-owned surfaces are machine-enforced
        res_summary = deliver_output(
            surface=DeliverySurface.SAVED_SUMMARY,
            text="Ran migration script with test database in local sandbox. Observed schema updated cleanly.",
        )
        self.assertEqual(res_summary.enforcement_authority, EnforcementAuthority.MACHINE_ENFORCED)
        self.assertTrue(res_summary.host_interception_supported)

        res_repo = deliver_output(
            surface=DeliverySurface.REPOSITORY_RESPONSE,
            text="Ran test runner with 10 tests in local. Observed 10 passed.",
        )
        self.assertEqual(res_repo.enforcement_authority, EnforcementAuthority.MACHINE_ENFORCED)
        self.assertTrue(res_repo.host_interception_supported)

        # Host-owned surfaces are instruction-only (no physical interception hook in Antigravity)
        res_chat = deliver_output(
            surface=DeliverySurface.IDE_ANSWER,
            text="Ran verification with sample in local. Observed clean exit.",
        )
        self.assertEqual(res_chat.enforcement_authority, EnforcementAuthority.INSTRUCTION_ONLY)
        self.assertFalse(res_chat.host_interception_supported)

        res_cards = deliver_output(
            surface=DeliverySurface.HOST_ACTIVITY_CARDS,
            text="Ran verification with sample in local. Observed clean exit.",
        )
        self.assertEqual(res_cards.enforcement_authority, EnforcementAuthority.INSTRUCTION_ONLY)
        self.assertFalse(res_cards.host_interception_supported)

    def test_output_reduction_measurement_against_frozen_baseline(self) -> None:
        """Verify concise completion achieves >90% reduction vs frozen RCA baseline."""
        concise_sample = (
            "Ran the Wave 2 behavior and concise rendering suite with 13 unit, adversarial, "
            "and CLI test cases in the local worktree environment. Observed all 13 tests passing, "
            "rejecting identical state on success claims, enforcing independent content and cache "
            "verification on rollbacks, flagging stale source edits, and rejecting omitted limitations. "
            "Integration with validate_turn_gates.py passed cleanly. Ready for Wave 3."
        )

        metrics = measure_output_reduction(
            authored_text=concise_sample,
            baseline_words=BASELINE_AUTHORED_WORDS,
            baseline_chars=BASELINE_AUTHORED_CHARS,
        )

        # Baseline checks
        self.assertEqual(metrics["baseline_words"], 1278)
        self.assertEqual(metrics["baseline_chars"], 14373)

        # Ceiling checks
        self.assertTrue(metrics["within_120_word_ceiling"])
        self.assertTrue(metrics["within_1400_char_ceiling"])

        # Reduction checks: >90% reduction
        self.assertGreater(metrics["word_reduction_pct"], 90.0)
        self.assertGreater(metrics["char_reduction_pct"], 90.0)

        # Authored text ratio: <= 10% of baseline
        self.assertLess(metrics["word_ratio_pct"], 10.0)
        self.assertLess(metrics["char_ratio_pct"], 10.0)

    def test_readiness_pipeline_walkthrough_summary_e2e(self) -> None:
        """Verify summary validation handles both valid concise summaries and rejects oversized ones."""
        valid_concise = (
            "Ran test suite with full coverage fixtures in local environment. "
            "Observed all 13 tests passing without regressions. Generated wave 2 runtime receipt."
        )
        is_val, errs, recs = validate_summary_content(valid_concise, filename="test_walkthrough.md")
        self.assertTrue(is_val, f"Expected valid summary, errors: {errs}")
        self.assertEqual(len(recs), 1)

        # Oversized summary must be rejected
        oversized = "Ran tests in local environment. Observed pass. " + ("extra words " * 120)
        is_val_over, errs_over, _ = validate_summary_content(oversized, filename="test_walkthrough.md")
        self.assertFalse(is_val_over)
        self.assertTrue(any("exceeds maximum allowed ceiling of 120 words" in e for e in errs_over))

    def test_non_engineering_routes_remain_intact(self) -> None:
        """Verify query, copilot, and HITL ambiguity evaluation preserve existing behavior."""
        # 1. Query route with direct answer
        query_response = "The active knowledge graph runtime generation is v2.1.0 with 1,024 nodes."
        res_query = validate_post_turn_gate(
            candidate_response=query_response,
            response_route="query",
            word_limit=50,
        )
        self.assertEqual(res_query["status"], "PASS")

        # 2. Copilot route with direct answer
        copilot_response = "```text\nOperator should confirm speaker identity before continuing intake.\n```"
        res_copilot = validate_post_turn_gate(
            candidate_response=copilot_response,
            response_route="copilot",
        )
        self.assertEqual(res_copilot["status"], "PASS")

        # 3. HITL ambiguity evaluation margin gate
        options_ambiguous = [
            {"confidence": 0.55, "label": "Option A"},
            {"confidence": 0.50, "label": "Option B"},
        ]
        eval_amb = evaluate_hitl_ambiguity(options_ambiguous)
        self.assertTrue(eval_amb["requires_hitl"])
        self.assertEqual(eval_amb["action"], "SURFACE_HITL_ATOMIC")

        options_decisive = [
            {"confidence": 0.90, "label": "Option A"},
            {"confidence": 0.40, "label": "Option B"},
        ]
        eval_dec = evaluate_hitl_ambiguity(options_decisive)
        self.assertFalse(eval_dec["requires_hitl"])
        self.assertEqual(eval_dec["action"], "PROCEED_AUTONOMOUSLY")

    def test_deliverable_links_delivery_surface_and_host_readback(self) -> None:
        """Verify delivery and host authority boundaries for deliverable links response."""
        with tempfile.TemporaryDirectory(prefix="test_delivery_links_") as td:
            dir_path = Path(td)
            t1 = dir_path / "01_transcript.md"
            t2 = dir_path / "02_recap.md"
            t3 = dir_path / "03_projection.json"
            judge = dir_path / "llm_judge_evaluation.json"

            t1.write_text("transcript text", encoding="utf-8")
            t2.write_text("recap text", encoding="utf-8")
            t3.write_text("{}", encoding="utf-8")
            judge.write_text('{"status": "PASS", "composite_score": 96.0}', encoding="utf-8")

            deliverables = {
                "Output 01 Transcript": t1,
                "Output 02 Recap": t2,
                "Output 03 Signal Projection": t3,
                "LLM Judge Evaluation": judge,
            }

            rendered = render_deliverables_completion(
                action="ingest-meeting",
                scope="Executive_Sync (10-09-2026)",
                input_desc="audio and metadata",
                environment=ExecutionEnvironment.LOCAL,
                deliverables=deliverables,
                judge_verdict="PASS",
                judge_score=96.0,
                package_only=True,
                limitation_or_next="Package-only intake complete; active graph publication requires activation.",
            )

            # 1. Deliver to REPOSITORY_RESPONSE (machine-enforced)
            res_repo = deliver_output(
                surface=DeliverySurface.REPOSITORY_RESPONSE,
                text=rendered,
            )
            self.assertTrue(res_repo.is_delivered)
            self.assertEqual(res_repo.enforcement_authority, EnforcementAuthority.MACHINE_ENFORCED)
            self.assertTrue(res_repo.host_interception_supported)
            self.assertEqual(res_repo.delivered_text, rendered)

            # 2. Deliver to IDE_ANSWER (instruction-only, host interception unsupported)
            res_ide = deliver_output(
                surface=DeliverySurface.IDE_ANSWER,
                text=rendered,
            )
            self.assertTrue(res_ide.is_delivered)
            self.assertEqual(res_ide.enforcement_authority, EnforcementAuthority.INSTRUCTION_ONLY)
            self.assertFalse(res_ide.host_interception_supported)
            self.assertEqual(res_ide.delivered_text, rendered)

            # 3. Post-turn gate integration check
            gate_res = validate_post_turn_gate(
                candidate_response=rendered,
                response_route="engineering",
            )
            self.assertEqual(gate_res["status"], "PASS")

    def test_ci_discovery_resolution_e2e(self) -> None:
        """Verify this test module resolves under dynamic CI test discovery."""
        from tools.test_discovery_core import resolve_all_tests

        det, e2e, bge = resolve_all_tests(repo_root=ROOT)
        self.assertIn("tests.end_to_end.test_engineering_output_delivery", e2e)


if __name__ == "__main__":
    unittest.main()

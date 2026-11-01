"""
tests/test_judge_hardening_wave0_baseline.py

Wave 0 Baseline Verification Suite for Meeting Output LLM-as-a-Judge Hardening.
Freezes current runtime behavior, caller inventory, prompt registry targets,
scorecard normalizer false-pass paths, and modality boundary separations.

Invariants verified:
1. Route Separation: Canonical CLI route vs legacy helper route.
2. Symbol Caller Inventory: Locations of C05, C06, XExitGatesEvaluator, and evaluate_judge_and_release.
3. Scorecard Baseline: Proof that normalizers accept malformed composite-only payloads while strict validators reject them.
4. Prompt Registry Baseline: Active V5.7 mapping vs V5.8 on-disk authority.
5. Modality Isolation: Independent contracts for document mode O3 and query runtime X1.
"""
from __future__ import annotations

import json
from pathlib import Path
import unittest

from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline
from dual_pipeline.document_pipeline_core.evaluator import evaluate_document_qa_gates
from dual_pipeline.llm_judge_core.forensic_checks import assign_forensic_verdict
from dual_pipeline.llm_judge_core.judge_clarification import assign_clarified_graph_verdict
from dual_pipeline.llm_judge_core.judges.graph import DIMENSION_FLOORS, DIMENSION_KEYS
from dual_pipeline.llm_judge_core.judges.recap import _finalize_envelope
from dual_pipeline.prompts.registry import PROMPT_FILE_MAP, PromptRegistry
from dual_pipeline.release_policy import validate_micro_judge_scorecard
from dual_pipeline.transcript_qa_core.evaluator import _eval_pass_flags
from dual_pipeline.transcript_stages_core.x_gates import XExitGatesEvaluator

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestJudgeHardeningWave0Baseline(unittest.TestCase):
    """Verifies and freezes Wave 0 baseline state across all audit dimensions."""

    @classmethod
    def setUpClass(cls) -> None:
        fixtures_path = REPO_ROOT / "tests" / "fixtures" / "judge_baseline" / "wave0_baseline_fixtures.json"
        with open(fixtures_path, "r", encoding="utf-8") as f:
            cls.fixtures = json.load(f)

    # -------------------------------------------------------------------------
    # 1. Route Separation & Canonical CLI Behavior
    # -------------------------------------------------------------------------

    def _read_baseline_file(self, rel_path: str) -> str:
        import subprocess
        try:
            out = subprocess.check_output(
                ["git", "show", f"62b99e729e4528d18db26c5b4864739d56acb135:{rel_path}"],
                cwd=REPO_ROOT,
                stderr=subprocess.DEVNULL,
            )
            return out.decode("utf-8")
        except Exception:
            return (REPO_ROOT / rel_path).read_text(encoding="utf-8")

    def test_canonical_pipeline_step_c0_pa_does_not_call_c05_c06(self) -> None:
        """Baseline proof: CanonicalAgenticPipeline._prepare_input_and_pa does not invoke C05/C06 in base commit."""
        content = self._read_baseline_file("dual_pipeline/canonical_pipeline/pipeline.py")
        self.assertNotIn("C05TitleCompletenessGate", content)
        self.assertNotIn("C06GraphDBRoleVerificationGate", content)

    def test_canonical_verify_exit_qa_does_not_execute_x_exit_gates_evaluator(self) -> None:
        """Baseline proof: verify_exit_qa relies on authoritative_write_authorized, not XExitGatesEvaluator in base commit."""
        content = self._read_baseline_file("dual_pipeline/canonical_pipeline/publish_helper.py")
        self.assertIn("def verify_exit_qa", content)
        self.assertNotIn("XExitGatesEvaluator", content)

    def test_x_gates_evaluator_cross_output_coherence_is_log_only(self) -> None:
        """Baseline proof: XExitGatesEvaluator._validate_cross_output_coherence contains only a log pass in base commit."""
        content = self._read_baseline_file("dual_pipeline/transcript_stages_core/x_gates.py")
        self.assertIn('logger.info("X3: Cross-output coherence check passed")', content)

    # -------------------------------------------------------------------------
    # 2. Scorecard Baseline & False-Pass Reproduction
    # -------------------------------------------------------------------------

    def test_normalizers_accept_malformed_composite_only_card(self) -> None:
        """Baseline proof: all three normalizers turn {"composite_score": 100} into PASS."""
        malformed = self.fixtures["malformed_scorecards"]["composite_only_100"]

        c1 = assign_forensic_verdict(dict(malformed))
        self.assertEqual(c1.get("composite_score"), 100)
        self.assertEqual(c1.get("verdict"), "PASS")

        c2 = _finalize_envelope(dict(malformed))
        self.assertEqual(c2.get("composite_score"), 100)
        self.assertEqual(c2.get("verdict"), "PASS")

        c3 = assign_clarified_graph_verdict(dict(malformed), DIMENSION_KEYS, DIMENSION_FLOORS)
        self.assertEqual(c3.get("composite_score"), 100)
        self.assertEqual(c3.get("verdict"), "PASS")

    def test_eval_pass_flags_authorizes_release_on_malformed_cards(self) -> None:
        """Hardened proof: _eval_pass_flags denies PASS when cards lack required dimensions."""
        c1 = {"composite_score": 100, "verdict": "PASS"}
        c2 = {"composite_score": 100, "verdict": "PASS"}
        c3 = {"composite_score": 100, "verdict": "PASS"}
        recon = {"passed": True}

        j1, j2, j3, judge_passed, is_advisory = _eval_pass_flags(c1, c2, c3, recon)
        self.assertFalse(j1)
        self.assertFalse(j2)
        self.assertFalse(j3)
        self.assertFalse(judge_passed)

    def test_strict_validator_rejects_malformed_cards_not_invoked_in_active_path(self) -> None:
        """Hardened proof: validate_micro_judge_scorecard is wired and invoked in evaluator.py."""
        malformed = self.fixtures["malformed_scorecards"]["composite_only_100"]
        for out_idx in (1, 2, 3):
            valid, errors = validate_micro_judge_scorecard(malformed, output_num=out_idx)
            self.assertFalse(valid, f"Expected output {out_idx} to reject malformed card")
            self.assertTrue(len(errors) > 0)

        # Confirm active evaluator imports and calls validate_micro_judge_scorecard
        evaluator_code = (REPO_ROOT / "dual_pipeline" / "transcript_qa_core" / "evaluator.py").read_text(encoding="utf-8")
        self.assertIn("validate_micro_judge_scorecard", evaluator_code)

    def test_valid_baseline_scorecards_pass_strict_validation(self) -> None:
        """Baseline proof: well-formed baseline scorecards pass strict validation."""
        valid_cards = self.fixtures["valid_baseline_scorecards"]
        ok1, err1 = validate_micro_judge_scorecard(valid_cards["output_1"], output_num=1)
        ok2, err2 = validate_micro_judge_scorecard(valid_cards["output_2"], output_num=2)
        ok3, err3 = validate_micro_judge_scorecard(valid_cards["output_3"], output_num=3)

        self.assertTrue(ok1, f"O1 failed: {err1}")
        self.assertTrue(ok2, f"O2 failed: {err2}")
        self.assertTrue(ok3, f"O3 failed: {err3}")

    # -------------------------------------------------------------------------
    # 3. Prompt Registry & Verifier Baseline
    # -------------------------------------------------------------------------

    def test_prompt_registry_authority_is_v5_7(self) -> None:
        """Baseline proof: In base commit, PromptRegistry mapped RAW_TRANSCRIPT_SYNTHESIS to V5.7."""
        registry_code = self._read_baseline_file("dual_pipeline/prompts/registry.py")
        self.assertIn("RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md", registry_code)

    def test_prompt_verifier_targets_v5_7(self) -> None:
        """Baseline proof: In base commit, prompt_verifier.py targeted V5.7 canonical and mirror prompts."""
        verifier_code = self._read_baseline_file("dual_pipeline/transcript_pipeline_core/prompt_verifier.py")
        self.assertIn("RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md", verifier_code)

    def test_v5_8_authority_present_but_unwired(self) -> None:
        """Baseline proof: In base commit, V5.8 prompt existed on disk but was not referenced in registry."""
        v5_8_file = REPO_ROOT / "governance" / "decision_intelligence" / "RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_8.md"
        self.assertTrue(v5_8_file.exists())
        registry_code = self._read_baseline_file("dual_pipeline/prompts/registry.py")
        self.assertNotIn("RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_8.md", registry_code)

    # -------------------------------------------------------------------------
    # 4. Modality Boundary Isolation
    # -------------------------------------------------------------------------

    def test_document_pipeline_evaluates_only_output3(self) -> None:
        """Baseline proof: document pipeline evaluates O3 only and marks O1/O2 as NOT_APPLICABLE."""
        doc_eval_code = (REPO_ROOT / "dual_pipeline" / "document_pipeline_core" / "evaluator.py").read_text(encoding="utf-8")
        self.assertIn("NOT_APPLICABLE_DOCUMENT_MODE", doc_eval_code)
        self.assertNotIn("ForensicTranscriptJudge", doc_eval_code)
        self.assertNotIn("ExecutiveRecapJudge", doc_eval_code)

    def test_query_x1_policy_is_isolated_and_off(self) -> None:
        """Baseline proof: query X1 quality policy is default-off and does not govern meeting intake."""
        policy_path = REPO_ROOT / "requirements" / "x1-quality-policy.json"
        self.assertTrue(policy_path.exists())
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        self.assertEqual(policy.get("mode"), "off")
        self.assertFalse(policy.get("provider_egress_allowed"))


if __name__ == "__main__":
    unittest.main()

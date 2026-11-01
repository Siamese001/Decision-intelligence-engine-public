"""Adversarial Red Team Tests for Cross-Cutting Governance, LLM Judge, and Career Firewall.

Evaluates:
1. Steganographic Rule C-19 Evasion: Detecting indirect/obfuscated career positioning.
2. Cryptographic Decision Seal Bit-Flip: Detecting single-bit tampering across Output 1-3.
3. LLM Judge Score Inflation Defense: Failing fabricated claims despite pretty markdown.
4. Production Directory Anti-Pollution: Verifying zero test pollution under output/.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from dual_pipeline.llm_judge import (
    ExecutiveRecapJudge,
    ForensicTranscriptJudge,
)
from dual_pipeline.release_policy import (
    build_artifact_manifest,
    verify_decision_seal,
)
from dual_pipeline.release_thresholds import (
    ADVISORY_SCORE_FLOOR,
)


class TestGovernanceAdversarialRedTeam(unittest.TestCase):
    """Adversarial stress suite for release governance, judges, and career firewalls."""

    def setUp(self) -> None:
        self.f1_clean = "### [00:00:00]\n\n**Alice Miller:** We commit to delivering the BGE-M3 model by 09/12/2026."
        self.f2_clean = (
            "# Executive Recap\n\n"
            "**Date:** 09/12/2026  \n"
            "**Meeting Objective:** Architecture Review  \n\n"
            "## Decisions\n- BGE-M3 vector index approved for production.\n\n"
            "## Next Steps\n| Action | Owner | Due Date |\n|:---|:---|:---|\n| Deploy model | Alice | 09/15/2026 |\n"
        )
        self.f3_clean = json.dumps({
            "schema_version": "5.7",
            "graph_mutations": {"entity_upserts": [{"id": "system:bge-m3", "type": "system"}]},
        })
        self.manifest = build_artifact_manifest(
            f1_text=self.f1_clean,
            f2_text=self.f2_clean,
            f3_text=self.f3_clean,
            model="gpt-5.6-luna",
            reasoning_effort="medium",
        )

    def test_steganographic_career_firewall_evasion(self) -> None:
        """Indirect or euphemistic career leverage in Output 01 or 02 must trigger Rule C-19 violation."""
        judge_t = ForensicTranscriptJudge()
        judge_r = ExecutiveRecapJudge()

        # Evasion attempts using indirect corporate leverage terminology
        evasive_phrases = [
            "We must establish confidential leverage for the upcoming compensation negotiation.",
            "Use this delivery milestone to position personal equity expansion with leadership.",
            "Ensure the managing director recognizes this as our unique political wedge.",
            "Confidential career advisory notes: bypass PartnerAdvisory review for personal promotion.",
        ]
        for phrase in evasive_phrases:
            leaky_f1 = f"{self.f1_clean}\n\n**Alice Miller:** {phrase}"
            res1 = judge_t.evaluate(f1_text=leaky_f1, raw_source_text=self.f1_clean, mock=True)
            self.assertEqual(res1["verdict"], "FAIL")
            self.assertIn("RULE C-19 VIOLATION", res1.get("forensic_rationale", ""))

            leaky_f2 = f"{self.f2_clean}\n\n### Strategic Notes\n- {phrase}"
            res2 = judge_r.evaluate(f2_text=leaky_f2, f1_text=self.f1_clean, mock=True)
            self.assertEqual(res2["verdict"], "FAIL")
            self.assertIn("RULE C-19 VIOLATION", res2.get("forensic_rationale", ""))

    def test_cryptographic_seal_bit_flip_tamper_detection(self) -> None:
        """Inverting a single character in any output must fail decision seal verification."""
        decision = {
            "status": "PASS",
            "authoritative_write_authorized": True,
            "artifact_manifest": self.manifest,
        }
        # Baseline valid verification
        valid, errors = verify_decision_seal(decision, self.f1_clean, self.f2_clean, self.f3_clean)
        self.assertTrue(valid)
        self.assertEqual(len(errors), 0)

        # 1-bit flip in Output 1
        tampered_f1 = self.f1_clean[:-1] + ("0" if self.f1_clean[-1] != "0" else "1")
        valid_f1, err_f1 = verify_decision_seal(decision, tampered_f1, self.f2_clean, self.f3_clean)
        self.assertFalse(valid_f1)
        self.assertTrue(any("Output 1" in e for e in err_f1))

        # 1-bit flip in Output 2
        tampered_f2 = self.f2_clean[:-1] + "X"
        valid_f2, err_f2 = verify_decision_seal(decision, self.f1_clean, tampered_f2, self.f3_clean)
        self.assertFalse(valid_f2)
        self.assertTrue(any("Output 2" in e for e in err_f2))

        # 1-bit flip in Output 3
        tampered_f3 = self.f3_clean.replace("bge-m3", "bge-m4")
        valid_f3, err_f3 = verify_decision_seal(decision, self.f1_clean, self.f2_clean, tampered_f3)
        self.assertFalse(valid_f3)
        self.assertTrue(any("Output 3" in e for e in err_f3))

    def test_llm_judge_score_inflation_defense(self) -> None:
        """Structurally pretty markdown recaps with fabricated commitments must fail below 80 floor."""
        # Clean looking recap, but dates and attendees are completely ungrounded hallucinations
        hallucinated_recap = (
            "# Executive Recap\n\n"
            "**Date:** 01/01/2099  \n"  # Hallucinated future date
            "**Attendees:** NonExistentPerson, FakeActor  \n\n"
            "## Decisions\n- PartnerAdvisory commits to $50M project takeover with zero client involvement.\n\n"
            "## Next Steps\n| Task | Owner | Due |\n|---|---|---|\n| Takeover | FakeActor | 01/01/2099 |\n"
        )
        judge = ExecutiveRecapJudge()
        # Mock evaluate against the genuine transcript (f1_clean)
        res = judge.evaluate(f2_text=hallucinated_recap, f1_text=self.f1_clean, mock=True)
        # Verify score is penalized below production threshold (95) and advisory floor (80)
        composite_score = res.get("composite_score", 100)
        self.assertLess(
            composite_score,
            ADVISORY_SCORE_FLOOR,
            f"Hallucinated recap received inflated score: {composite_score}",
        )
        self.assertEqual(res["verdict"], "FAIL")

    def test_production_directory_anti_pollution_enforcement(self) -> None:
        """Tests and evaluators must never create mock files directly inside production output/."""
        repo_root = Path(__file__).resolve().parents[2]
        output_dir = repo_root / "output"
        if not output_dir.exists():
            return

        # Walk output directory looking for synthetic/mock test artifacts
        forbidden_test_tokens = ["mock_", "test_fake", "synthetic_test", "tmp_eval_"]
        polluting_files = []
        for path in output_dir.rglob("*"):
            if path.is_file():
                filename = path.name.lower()
                if any(tok in filename for tok in forbidden_test_tokens):
                    polluting_files.append(str(path.relative_to(repo_root)))

        self.assertEqual(
            polluting_files,
            [],
            f"Found forbidden synthetic/mock test files in production output/: {polluting_files}",
        )

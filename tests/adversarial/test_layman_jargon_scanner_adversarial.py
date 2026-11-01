"""
tests/adversarial/test_layman_jargon_scanner_adversarial.py

Adversarial and Edge-Case Test Suite for the Zero-Jargon Layman Invariant,
Layman Clarity Scanner, and hitl-option.schema.json v1.1.0 Contract.
Covers:
  - Boundary payloads (empty, minimal, huge text, unicode, emoji, markdown)
  - Malformed input (bad types, inverted credible intervals, out-of-range scores)
  - Invariant bypass & evasion attempts (embedded code tokens, snake_case, exception leaks)
  - Impact statement evasion attempts (tautological or non-substantive text)
  - Concurrency stress across multi-threaded option validation
"""
from __future__ import annotations

import concurrent.futures
import json
import unittest
from pathlib import Path

import jsonschema

from dual_pipeline.hitl_governance_core.layman_clarity import (
    LaymanClarityError,
    build_layman_option,
    contains_jargon,
    format_layman_display_label,
    scan_for_jargon,
    validate_impact_statement,
    validate_layman_option,
    validate_plain_english,
)
from dual_pipeline.hitl_governance_core.validation import (
    validate_hitl_presentation,
)


class LaymanJargonScannerAdversarialTests(unittest.TestCase):
    """Adversarial suite probing edge cases, bypass attempts, and stress limits."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.schema_path = Path("contracts/dual_pipeline/schemas/hitl-option.schema.json")
        assert cls.schema_path.exists(), "hitl-option.schema.json must exist"
        cls.schema = json.loads(cls.schema_path.read_text(encoding="utf-8"))

    # =========================================================================
    # 1. Boundary Payloads
    # =========================================================================

    def test_boundary_empty_and_whitespace_inputs(self) -> None:
        """Verify scanner and validators safely reject empty or pure whitespace strings."""
        for empty_val in ("", "   ", "\t\n\r", " \n "):
            self.assertEqual(scan_for_jargon(empty_val), [])
            errs_summary = validate_plain_english(empty_val, field_name="plain_english_summary")
            self.assertTrue(any("LAYMAN_EMPTY_FIELD" in e for e in errs_summary))
            errs_impact = validate_impact_statement(empty_val, field_name="impact_statement")
            self.assertTrue(any("LAYMAN_MISSING_IMPACT" in e for e in errs_impact))

    def test_boundary_unicode_and_emoji_handling(self) -> None:
        """Verify scanner handles Unicode, emojis, and special symbols cleanly without false crashes."""
        text = "Authorize expansion into international markets 🚀 — guarantees 99.9% uptime across regions."
        self.assertFalse(contains_jargon(text))
        errs = validate_plain_english(text)
        self.assertEqual(errs, [])

    def test_boundary_huge_payload_stress(self) -> None:
        """Verify scanner handles large paragraphs (10,000+ chars) efficiently without regex catastrophic backtracking."""
        clean_sentence = "The executive council recommends authorizing the migration to improve service latency. "
        large_payload = clean_sentence * 200  # ~18,000 characters
        violations = scan_for_jargon(large_payload)
        self.assertEqual(violations, [])

        # Inject single violation at the very end
        poisoned_payload = large_payload + " Inspect mcp_server.py for details."
        poison_violations = scan_for_jargon(poisoned_payload)
        self.assertTrue(any("Forbidden file reference" in v for v in poison_violations))

    # =========================================================================
    # 2. Malformed Input & Inverted Ranges
    # =========================================================================

    def test_malformed_inverted_credible_interval_rejected(self) -> None:
        """Verify options with inverted credible intervals (lower > upper) fail closed."""
        with self.assertRaises(LaymanClarityError) as ctx:
            build_layman_option(
                label="Standard Option",
                plain_english_summary="Valid summary in plain English.",
                impact_statement="Operational execution preserves system safety.",
                confidence_score=0.80,
                credible_interval=(0.95, 0.65),  # Inverted: lo > hi!
            )
        self.assertIn("LAYMAN_INVALID_INTERVAL_RANGE", str(ctx.exception))

    def test_malformed_out_of_bounds_confidence_rejected(self) -> None:
        """Verify out-of-bounds confidence values fail closed."""
        opt_bad = {
            "label": "Option A",
            "plain_english_summary": "Valid summary.",
            "impact_statement": "Valid impact statement preserving safety.",
            "confidence_score": 1.5,  # > 1.0
            "confidence_pct": 150.0,
            "confidence_level": "HIGH",
            "tier": "HIGH",
            "credible_interval": [0.8, 0.9],
            "evidence_refs": [],
            "recommended": True,
        }
        errs = validate_layman_option(opt_bad)
        self.assertTrue(any("LAYMAN_INVALID_CONFIDENCE_SCORE" in e for e in errs))

    # =========================================================================
    # 3. Invariant Bypass & Jargon Injection Attacks
    # =========================================================================

    def test_adversarial_file_extension_injection_detected(self) -> None:
        """Verify embedded file extensions and script names are strictly caught."""
        evasive_texts = [
            "We should retire the legacy mcp_server.py component immediately.",
            "Review configuration settings in parameters.json before proceeding.",
            "Modify the deployment pipeline script in deploy.sh right now.",
            "Read the rules from AGENTS.md carefully.",
            "Update styles in styles.css and components in app.ts.",
        ]
        for t in evasive_texts:
            violations = scan_for_jargon(t)
            self.assertTrue(
                any("Forbidden file reference" in v for v in violations),
                f"Failed to detect file extension in: '{t}'",
            )

    def test_adversarial_code_keyword_injection_detected(self) -> None:
        """Verify programming syntax keywords are strictly caught in user options."""
        evasive_keywords = [
            "Execute def test_worker() in the background.",
            "Instantiate class ServiceRouter to handle traffic.",
            "Ensure import os is completed before startup.",
            "Pass kwargs to the underlying handler function.",
            "Initialize the object with __init__ parameters.",
            "Return dict containing the configuration mappings.",
            "Assign self to the parent instance.",
        ]
        for t in evasive_keywords:
            violations = scan_for_jargon(t)
            self.assertTrue(
                any("Forbidden code keyword" in v for v in violations),
                f"Failed to detect code keyword in: '{t}'",
            )

    def test_adversarial_snake_case_leak_detected(self) -> None:
        """Verify snake_case code variable names are caught in options and summaries."""
        evasive_snake_cases = [
            "Adjust the margin_threshold to 0.15.",
            "Evaluate top candidate confidence c_top against floor.",
            "Persist the result into hitl_decision_ledger.",
            "Check autonomous_context before proceeding.",
        ]
        for t in evasive_snake_cases:
            violations = scan_for_jargon(t)
            self.assertTrue(
                any("Forbidden snake_case identifier" in v for v in violations),
                f"Failed to detect snake_case in: '{t}'",
            )

    def test_adversarial_exception_name_leak_detected(self) -> None:
        """Verify exception and error class names are caught."""
        evasive_exceptions = [
            "This option suppresses HitlConfidenceMissingError during evaluation.",
            "Catches ValueError when scores are invalid.",
            "May raise KeyError if parameters are missing.",
        ]
        for t in evasive_exceptions:
            violations = scan_for_jargon(t)
            self.assertTrue(
                any("Forbidden exception/error name" in v for v in violations),
                f"Failed to detect exception name in: '{t}'",
            )

    def test_adversarial_git_plumbing_leak_detected(self) -> None:
        """Verify internal git plumbing commands are caught."""
        evasive_git = [
            "Revert the previous change by checking out HEAD~1.",
            "Apply the fix via cherry-pick from main branch.",
            "Run git commit to seal the changes.",
            "Inspect git reflog to restore lost commits.",
        ]
        for t in evasive_git:
            violations = scan_for_jargon(t)
            self.assertTrue(
                any("Forbidden git internal token" in v for v in violations),
                f"Failed to detect git plumbing token in: '{t}'",
            )

    # =========================================================================
    # 4. Impact Statement Evasion Attacks
    # =========================================================================

    def test_adversarial_tautological_impact_statement_rejected(self) -> None:
        """Verify non-substantive or tautological impact statements fail validation."""
        bad_impacts = [
            "Impact: impact",
            "This is the impact of the option.",
            "No impact at all.",
            "Operational execution.",
        ]
        for bi in bad_impacts:
            errs = validate_impact_statement(bi)
            self.assertTrue(
                len(errs) > 0,
                f"Tautological impact statement incorrectly passed: '{bi}'",
            )

    # =========================================================================
    # 5. Interactive Presentation Guard Integration
    # =========================================================================

    def test_adversarial_presentation_guard_blocks_poisoned_payload(self) -> None:
        """Verify validate_hitl_presentation fails closed when jargon is injected into options."""
        poisoned_presentation = {
            "questions": [
                {
                    "question": "Which action should be authorized?",
                    "options": [
                        "Option A: Execute mcp_server.py (Confidence: 85%)",
                        "Option B: Do nothing (Confidence: 15%)",
                    ],
                }
            ]
        }
        errors = validate_hitl_presentation(poisoned_presentation, require_layman=True)
        self.assertTrue(
            any("HITL_VIOLATION_JARGON_IN_OPTION" in e for e in errors),
            f"Expected jargon rejection in presentation options, got: {errors}",
        )

    # =========================================================================
    # 6. Concurrency Stress Test
    # =========================================================================

    def test_concurrency_multi_threaded_validation_thread_safety(self) -> None:
        """Verify multi-threaded concurrent calls to scanner and builder execute without race conditions."""
        def worker(i: int) -> bool:
            opt = build_layman_option(
                label=f"Strategic Choice {i}",
                plain_english_summary=f"Summary for strategic initiative {i} in plain English.",
                impact_statement=f"Operational outcome {i} reduces latency and improves throughput.",
                confidence_score=0.85,
                credible_interval=(0.80, 0.90),
                sample_size=i + 10,
                recommended=(i == 0),
            )
            jsonschema.validate(instance=opt, schema=self.schema)
            lbl = format_layman_display_label(opt)
            return len(lbl) > 0 and not contains_jargon(lbl)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(worker, i) for i in range(50)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(len(results), 50)
        self.assertTrue(all(results))


if __name__ == "__main__":
    unittest.main()

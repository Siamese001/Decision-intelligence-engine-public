"""Adversarial and contract correctness test suite for D02 Executive Recap synthesis (Wave 2).

Verifies Wave 2 contract correctness invariants:
1. System prompt (shared archetype V5.7 prefix) propagation into Pass 2A, Pass 2B, Pass 2C.
2. Zero occurrences of hardcoded timestamp literal '00:28:00' across dual_pipeline/.
3. Dynamic duration and archetype resolution in remediation directives.
4. TruncatedResponseError raised on finishReason in ('MAX_TOKENS', 'LENGTH').
5. Multi-part content concatenation in Gemini API response parsing.
6. Pass2BStructureError validation (markdown table vs zero-commitments sentinel vs unstructured prose).
7. Mock-sniffing removal in L2StageSynthesizers.synthesize_recap.
8. Boundary payloads: empty candidate lists, missing parts, whitespace-only content.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.transcript_stages_core.external_synthesizer import (
    TruncatedResponseError,
    _extract_gemini_metadata,
)
from dual_pipeline.transcript_stages_core.recap_modular_dag import (
    Pass2BStructureError,
    execute_d02_3pass_dag,
    validate_pass_2b_structure,
)


class D02ContractAdversarialTests(unittest.TestCase):
    """Adversarial and invariant contract tests for D02 Executive Recap."""

    def test_system_prompt_propagated_to_all_three_passes(self) -> None:
        """Verify that system_prompt (archetype V5.7 prefix) is propagated to 2A, 2B, and 2C."""
        shared_prefix = "V5_7_SHARED_ARCHETYPE_SYSTEM_PROMPT_TOKEN_12345"
        captured_prompts: dict[str, str] = {}

        def mock_generate(*args, **kwargs):
            pass_id = kwargs.get("pass_id", "UNKNOWN")
            system_prompt = kwargs.get("system_prompt", "")
            captured_prompts[pass_id] = system_prompt
            pass_responses = {
                "2A": "narrative " * 850,
                "2B": "| Item # | Action | Owner | Target Date | Next Step |\n| 1 | Task | Amit | 10/01/2026 | Next |",
                "2C": "perspective " * 650,
            }
            return pass_responses.get(pass_id, "ok")

        with patch(
            "dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content",
            side_effect=mock_generate,
        ):
            execute_d02_3pass_dag(
                model="gemini-3.8-flash",
                system_prompt=shared_prefix,
                user_content="transcription snippet",
                min_words_2a=800,
                min_words_2c=600,
            )

        self.assertIn("2A", captured_prompts, "Pass 2A was not called")
        self.assertIn("2B", captured_prompts, "Pass 2B was not called")
        self.assertIn("2C", captured_prompts, "Pass 2C was not called")

        self.assertIn(
            shared_prefix,
            captured_prompts["2A"],
            "Pass 2A system instruction dropped shared archetype prompt",
        )
        self.assertIn(
            shared_prefix,
            captured_prompts["2B"],
            "Pass 2B system instruction dropped shared archetype prompt",
        )
        self.assertIn(
            shared_prefix,
            captured_prompts["2C"],
            "Pass 2C system instruction dropped shared archetype prompt",
        )

    def test_zero_occurrences_of_hardcoded_timestamp_in_codebase(self) -> None:
        """Assert zero occurrences of hardcoded '00:28:00' in dual_pipeline/."""
        dual_pipeline_dir = Path(__file__).resolve().parent.parent / "dual_pipeline"
        self.assertTrue(dual_pipeline_dir.exists(), f"Path not found: {dual_pipeline_dir}")

        matches: list[str] = []
        for py_path in dual_pipeline_dir.rglob("*.py"):
            text = py_path.read_text(encoding="utf-8")
            if "00:28:00" in text:
                matches.append(str(py_path))

        self.assertEqual(
            matches,
            [],
            f"Found hardcoded '00:28:00' literal in dual_pipeline files: {matches}",
        )

    def test_dynamic_remediation_directive_resolution(self) -> None:
        """Verify dynamic meeting duration and archetype in recap remediation directive."""
        from dual_pipeline.transcript_stages_core.recap_executor import L2ExecutiveRecapExecutor

        mock_provider = MagicMock()
        mock_provider.synthesize_recap.return_value = (
            "# Title\n\nExecutive Summary content...\n\n"
            "## Action & Governance Ledger\n\n| Item # | Action | Owner | Target Date | Next Step |\n| 1 | Task | A | 10/01 | Done |\n\n"
            "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n## Person A\n- **Theme**: Content..."
        )

        executor = L2ExecutiveRecapExecutor(provider=mock_provider)

        test_cases = [
            (15, "ARCHITECTURE_DEEP_DIVE", "< 00:15:00", "architecture deep dive"),
            (90, "STRATEGIC_DISCOVERY", "< 01:30:00", "strategic discovery"),
            (45, "TACTICAL_ALIGNMENT", "< 00:45:00", "tactical alignment"),
        ]

        for dur_mins, archetype, expected_ts, expected_arch in test_cases:
            metadata = {
                "meeting_duration": dur_mins,
                "archetype": archetype,
                "prior_recap_candidate": "# Previous Title\n\nPrevious summary...",
            }
            executor.execute(
                forensic_transcript="[00:01:00] Speaker A: Hello",
                pa_context={"metadata": metadata},
                remediation_directive="Expand section 2",
            )
            call_args = mock_provider.synthesize_recap.call_args
            self.assertIsNotNone(call_args)
            _, user_content = call_args[0]
            self.assertIn(
                expected_ts,
                user_content,
                f"Expected max timestamp {expected_ts} for duration {dur_mins}m",
            )
            self.assertIn(
                expected_arch,
                user_content,
                f"Expected archetype name '{expected_arch}' in directive",
            )
            self.assertNotIn("00:28:00", user_content)

    def test_truncated_response_error_on_max_tokens_and_length(self) -> None:
        """Verify TruncatedResponseError is raised when finishReason is MAX_TOKENS or LENGTH."""
        for finish_reason in ("MAX_TOKENS", "LENGTH", "max_tokens", "length"):
            payload = {
                "candidates": [
                    {
                        "content": {"parts": [{"text": "Partial narrative before truncation..."}]},
                        "finishReason": finish_reason,
                    }
                ],
                "usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 2048},
            }
            with self.assertRaises(TruncatedResponseError) as ctx:
                _extract_gemini_metadata(payload)
            self.assertIn("truncated due to", str(ctx.exception))

    def test_normal_finish_reason_does_not_raise_truncated_error(self) -> None:
        """Verify STOP and other normal finish reasons extract text cleanly."""
        payload = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "Complete narrative."}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20},
        }
        text, usage, reason = _extract_gemini_metadata(payload)
        self.assertEqual(text, "Complete narrative.")
        self.assertEqual(reason, "STOP")

    def test_multipart_gemini_response_concatenation(self) -> None:
        """Verify that multi-part candidate responses are concatenated without loss."""
        payload = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "First chunk of discussion. "},
                            {"text": "Second chunk of discussion. "},
                            {"text": "Final concluding remarks."},
                        ]
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {"promptTokenCount": 200, "candidatesTokenCount": 50},
        }
        text, _, _ = _extract_gemini_metadata(payload)
        expected = "First chunk of discussion. Second chunk of discussion. Final concluding remarks."
        self.assertEqual(text, expected)

    def test_pass_2b_structure_validation_adversarial(self) -> None:
        """Verify validate_pass_2b_structure accepts valid tables and sentinel, rejects invalid."""
        # Valid Markdown table
        table = (
            "| Item # | Action / Decision | Owner | Target Date | Next Step |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            "| 1 | Finalize SOW | Mandy | 10/15/2026 | Review with team |"
        )
        self.assertTrue(validate_pass_2b_structure(table))

        # Valid zero-commitments sentinel
        sentinel_1 = "No forward commitments or action items were assigned during this technical sync."
        self.assertTrue(validate_pass_2b_structure(sentinel_1))

        sentinel_2 = "NO FORWARD COMMITMENTS OR ACTION ITEMS were agreed upon."
        self.assertTrue(validate_pass_2b_structure(sentinel_2))

        # Invalid unstructured prose
        prose = "During the meeting the team discussed various items and agreed to keep in touch."
        with self.assertRaises(Pass2BStructureError) as ctx:
            validate_pass_2b_structure(prose)
        self.assertIn("violates contract", str(ctx.exception))

        # Empty text
        with self.assertRaises(Pass2BStructureError):
            validate_pass_2b_structure("")

    def test_synthesizer_no_mock_sniffing(self) -> None:
        """Verify that hasattr(mock, 'call_count') does not divert gemini models away from DAG."""
        from dual_pipeline.transcript_stages_core.synthesizers import LLMTranscriptSynthesizer

        synthesizer = LLMTranscriptSynthesizer(d02_model="gemini-3.8-flash", _client=MagicMock())

        # In the old code, any MagicMock had hasattr(..., 'call_count') == True,
        # which bypassed the DAG. We verify that having a mock with call_count attribute
        # does NOT divert from DAG and mock_completion is never called.
        mock_completion = MagicMock()
        self.assertTrue(hasattr(mock_completion, "call_count"))
        synthesizer._create_completion_with_effort = mock_completion

        with patch(
            "dual_pipeline.transcript_stages_core.recap_modular_dag.execute_d02_3pass_dag",
            return_value="DAG rendered recap",
        ) as mock_dag:
            result = synthesizer.synthesize_recap("System prompt", "User content")
            self.assertEqual(result, "DAG rendered recap")
            mock_dag.assert_called_once_with("gemini-3.8-flash", "System prompt", "User content")
            mock_completion.assert_not_called()

    def test_adversarial_malformed_gemini_payloads(self) -> None:
        """Verify informative error handling for malformed or missing candidate payloads."""
        # Missing candidates list
        with self.assertRaises(RuntimeError) as ctx1:
            _extract_gemini_metadata({})
        self.assertIn("no candidates", str(ctx1.exception))

        # Empty candidates list
        with self.assertRaises(RuntimeError) as ctx2:
            _extract_gemini_metadata({"candidates": []})
        self.assertIn("no candidates", str(ctx2.exception))

        # Empty parts list
        with self.assertRaises(RuntimeError) as ctx3:
            _extract_gemini_metadata({"candidates": [{"content": {"parts": []}}]})
        self.assertIn("empty response", str(ctx3.exception))

        # Whitespace-only parts
        with self.assertRaises(RuntimeError) as ctx4:
            _extract_gemini_metadata(
                {"candidates": [{"content": {"parts": [{"text": "   \n\t  "}]}}]}
            )
        self.assertIn("empty response", str(ctx4.exception))


if __name__ == "__main__":
    unittest.main()

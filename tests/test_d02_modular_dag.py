"""Unit tests for D02 3-Pass Parallel Modular DAG executive recap synthesis."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.transcript_stages_core.recap_modular_dag import (
    PASS_2B_SYSTEM_PROMPT,
    MonolithicRecapBlockedError,
    Pass2BStructureError,
    WordFloorError,
    execute_d02_3pass_dag,
    execute_pass_2a,
    execute_pass_2b,
    execute_pass_2c,
    validate_pass_2b_structure,
    validate_word_floor,
)


class D02ModularDAGTests(unittest.TestCase):
    """Unit tests for D02 3-Pass parallel DAG components."""

    def test_validate_word_floor(self) -> None:
        """Verify word floor validation logic."""
        sample = "one two three four five"
        self.assertEqual(validate_word_floor(sample, min_words=5, pass_label="Test"), 5)

        with self.assertRaises(WordFloorError) as ctx:
            validate_word_floor(sample, min_words=6, pass_label="Test")
        self.assertIn("failing required floor", str(ctx.exception))

    def test_execute_pass_2a_success(self) -> None:
        """Verify Pass 2A returns narrative when meeting word floor."""
        long_narrative = "word " * 850
        with patch(
            "dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content",
            return_value=long_narrative,
        ):
            result = execute_pass_2a(model="gemini-3.8-flash", user_content="snippet", min_words=800)
            self.assertGreaterEqual(len(result.split()), 800)

    def test_execute_pass_2a_retry_on_short_output(self) -> None:
        """Verify Pass 2A auto-retries when first response is below word floor."""
        short_output = "word " * 200
        long_output = "word " * 850

        with patch(
            "dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content",
            side_effect=[short_output, long_output],
        ) as mock_api:
            result = execute_pass_2a(model="gemini-3.8-flash", user_content="snippet", min_words=800, max_retries=2)
            self.assertEqual(mock_api.call_count, 2)
            self.assertGreaterEqual(len(result.split()), 800)

    def test_execute_pass_2b_action_ledger(self) -> None:
        """Verify Pass 2B generates structured action ledger table."""
        table_content = (
            "| Item # | Action / Decision | Owner | Target Date | Next Step |\n"
            "| 1 | Deploy L1 Google Adapter | Amit Ayer | 09/28/2026 | Run tests |"
        )
        with patch(
            "dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content",
            return_value=table_content,
        ):
            result = execute_pass_2b(model="gemini-3.8-flash", user_content="snippet")
            self.assertIn("| Item # |", result)
            self.assertIn("Deploy L1 Google Adapter", result)

    def test_pass_2b_system_prompt_grounding_directives(self) -> None:
        """Verify PASS_2B_SYSTEM_PROMPT instructs H-6 waterfall, relative timing math, and zero filler."""
        self.assertIn("5-Step Owner Waterfall (H-6)", PASS_2B_SYSTEM_PROMPT)
        self.assertIn("Transcript-Relative Timing Math", PASS_2B_SYSTEM_PROMPT)
        self.assertIn("Zero Unowned Filler Prohibition", PASS_2B_SYSTEM_PROMPT)
        self.assertIn("No formal actions were established in this session", PASS_2B_SYSTEM_PROMPT)

    def test_validate_pass_2b_structure_valid_table_and_sentinels(self) -> None:
        """Verify validate_pass_2b_structure accepts grounded tables and clean sentinels."""
        valid_table = (
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Action | Finalize SOW | Mandy | 10/15/2026 | [00:15:20] | In Progress |"
        )
        self.assertTrue(validate_pass_2b_structure(valid_table))

        sentinel_1 = "No formal actions were established in this session; open initiatives remain unresolved questions."
        self.assertTrue(validate_pass_2b_structure(sentinel_1))

        sentinel_2 = "No forward commitments or action items were assigned during this discussion."
        self.assertTrue(validate_pass_2b_structure(sentinel_2))

    def test_validate_pass_2b_structure_rejects_unowned_tbd_filler(self) -> None:
        """Verify validate_pass_2b_structure rejects lazy Unassigned + TBD filler rows."""
        filler_table = (
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Action | Review logs | Unassigned | TBD | [00:12:00] | Open |"
        )
        with self.assertRaises(Pass2BStructureError) as ctx:
            validate_pass_2b_structure(filler_table)
        self.assertIn("lazy unowned/undated filler row", str(ctx.exception))

    def test_validate_pass_2b_structure_rejects_all_unassigned(self) -> None:
        """Verify validate_pass_2b_structure rejects tables where all rows lack named DRI."""
        unassigned_table = (
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Action | Review logs | Unassigned (Pending Owner Assignment) | 10/24/2026 | [00:12:00] | Open |"
        )
        with self.assertRaises(Pass2BStructureError) as ctx:
            validate_pass_2b_structure(unassigned_table)
        self.assertIn("contains no named accountable owners", str(ctx.exception))

    def test_execute_pass_2b_retry_on_structure_violation(self) -> None:
        """Verify execute_pass_2b auto-retries when first response generates unowned TBD filler."""
        bad_output = (
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Action | Discuss model | Unassigned | TBD | [00:12:00] | Open |"
        )
        good_output = (
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Action | Discuss model | Alex Brannan | 10/24/2026 | [00:12:00] | Confirmed |"
        )
        with patch(
            "dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content",
            side_effect=[bad_output, good_output],
        ) as mock_api:
            result = execute_pass_2b(model="gemini-3.8-flash", user_content="snippet", max_retries=2)
            self.assertEqual(mock_api.call_count, 2)
            self.assertIn("Alex Brannan", result)
            self.assertIn("10/24/2026", result)

    def test_execute_pass_2b_exhausted_retries(self) -> None:
        """Verify execute_pass_2b raises Pass2BStructureError when retries are exhausted."""
        bad_output = (
            "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 1 | Action | Discuss model | Unassigned | TBD | [00:12:00] | Open |"
        )
        with patch(
            "dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content",
            return_value=bad_output,
        ):
            with self.assertRaises(Pass2BStructureError) as ctx:
                execute_pass_2b(model="gemini-3.8-flash", user_content="snippet", max_retries=1)
            self.assertIn("failed structure validation after 1 retries", str(ctx.exception))

    def test_execute_pass_2c_perspectives(self) -> None:
        """Verify Pass 2C generates stakeholder perspectives meeting word floor."""
        perspectives_content = "perspective " * 650
        with patch(
            "dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content",
            return_value=perspectives_content,
        ):
            result = execute_pass_2c(model="gemini-3.8-flash", user_content="snippet", min_words=600)
            self.assertGreaterEqual(len(result.split()), 600)

    def test_execute_d02_3pass_dag_stitching(self) -> None:
        """Verify full 3-pass parallel DAG execution stitches sections properly."""
        narrative = "# Meeting Title\n\nExecutive summary content..." + (" context" * 50)
        ledger = "| Item # | Action | Owner | Target Date | Next Step |\n| 1 | Action 1 | Alex | Today | Done |"
        perspectives = "## Alex Brennan\n- **Theme**: Strategic alignment discussion..."

        with patch(
            "dual_pipeline.transcript_stages_core.recap_modular_dag.execute_pass_2a",
            return_value=narrative,
        ), patch(
            "dual_pipeline.transcript_stages_core.recap_modular_dag.execute_pass_2b",
            return_value=ledger,
        ), patch(
            "dual_pipeline.transcript_stages_core.recap_modular_dag.execute_pass_2c",
            return_value=perspectives,
        ):
            stitched = execute_d02_3pass_dag(
                model="gemini-3.8-flash",
                system_prompt="recap prompt",
                user_content="user content",
                min_words_2a=50,
                min_words_2c=10,
            )

            self.assertIn("# Meeting Title", stitched)
            self.assertTrue(
                "## Action & Governance Ledger" in stitched or "# Action & Governance Ledger" in stitched,
                "Expected Action & Governance Ledger header in stitched output",
            )
            self.assertIn("| Item # |", stitched)
            self.assertIn("# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown", stitched)
            self.assertIn("Alex Brennan", stitched)

    def test_monolithic_mode_blocked_on_gemini(self) -> None:
        """Verify monolithic single-call mode is explicitly blocked for non-frontier models."""
        with self.assertRaises(MonolithicRecapBlockedError) as ctx:
            execute_d02_3pass_dag(
                model="gemini-3.8-flash",
                system_prompt="prompt",
                user_content="content",
                allow_monolithic=True,
            )
        self.assertIn("Monolithic single-call mode is disabled", str(ctx.exception))

    def test_call_gemini_generate_content_banned_model_rejection(self) -> None:
        """Verify call_gemini_generate_content strictly rejects banned models."""
        from dual_pipeline.transcript_stages_core.external_synthesizer import (
            call_gemini_generate_content,
        )
        with self.assertRaises(ValueError) as ctx:
            call_gemini_generate_content(
                model="gemini-2.5-flash",  # allow-retired-model
                system_prompt="sys",
                user_content="user",
            )
        self.assertIn("strictly banned", str(ctx.exception))

    def test_call_gemini_generate_content_thinking_config_and_parameter_immunity(self) -> None:
        """Verify call_gemini_generate_content configures thinkingLevel HIGH and strips temperature for gemini-3.8-flash."""
        from dual_pipeline.transcript_stages_core.external_synthesizer import (
            call_gemini_generate_content,
        )

        captured_payload = {}

        def fake_urlopen(req, timeout=120.0):
            nonlocal captured_payload
            captured_payload = json.loads(req.data.decode("utf-8"))
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({
                "candidates": [{"content": {"parts": [{"text": "response text"}]}}]
            }).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("config.credentials.CredentialManager.get_credential", return_value="fake-api-key"):
            res = call_gemini_generate_content(
                model="gemini-3.8-flash",
                system_prompt="sys",
                user_content="user",
                temperature=0.7,
            )
            self.assertEqual(res, "response text")
            gen_cfg = captured_payload.get("generationConfig", {})
            self.assertEqual(gen_cfg.get("thinkingConfig"), {"thinkingLevel": "HIGH"})
            self.assertNotIn("temperature", gen_cfg)

    def test_call_gemini_generate_content_explicit_thinking_disabled(self) -> None:
        """Verify explicit thinking disabled restores temperature parameter."""
        from dual_pipeline.transcript_stages_core.external_synthesizer import (
            call_gemini_generate_content,
        )

        captured_payload = {}

        def fake_urlopen(req, timeout=120.0):
            nonlocal captured_payload
            captured_payload = json.loads(req.data.decode("utf-8"))
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({
                "candidates": [{"content": {"parts": [{"text": "response text"}]}}]
            }).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            return mock_resp

        with patch("urllib.request.urlopen", side_effect=fake_urlopen), \
             patch("config.credentials.CredentialManager.get_credential", return_value="fake-api-key"):
            call_gemini_generate_content(
                model="gemini-3.8-flash",
                system_prompt="sys",
                user_content="user",
                thinking_level="NONE",
                temperature=0.3,
            )
            gen_cfg = captured_payload.get("generationConfig", {})
            self.assertNotIn("thinkingConfig", gen_cfg)
            self.assertEqual(gen_cfg.get("temperature"), 0.3)

    def test_init_stage_models_banned_model_remapping(self) -> None:
        """Verify init_stage_models safely remaps banned models to approved defaults."""
        from dual_pipeline.transcript_stages_core.external_synthesizer import init_stage_models

        class FakeSynth:
            pass

        s = FakeSynth()
        init_stage_models(s, model="gemini-2.5-flash", d01="gemini-2.5-flash", d02=None, d03="gemini-2.5-flash", agentic=None)  # allow-retired-model
        self.assertEqual(s.d01_model, "gemini-3.8-flash")
        self.assertEqual(s.d02_model, "gemini-3.8-flash")
        self.assertEqual(s.d03_model, "gemini-3.8-flash")
        self.assertEqual(s.agentic_model, "gemini-3.8-flash")


if __name__ == "__main__":
    unittest.main()

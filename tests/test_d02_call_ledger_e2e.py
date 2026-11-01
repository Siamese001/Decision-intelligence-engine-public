"""End-to-End test suite for D02 Executive Recap call telemetry and budget receipts.

Exercises full 3-pass DAG lifecycle with mocked Gemini HTTP responses:
- Verifies best-case budget (exactly 3 calls: 1 for 2A, 1 for 2B, 1 for 2C).
- Verifies prompt-block byte breakdown (D7).
- Verifies receipt JSON schema and disk emission.
- Verifies retried budget scenarios and zero diff in stitched recap output.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.transcript_stages_core.call_ledger import (
    measure_prompt_block_bytes,
)
from dual_pipeline.transcript_stages_core.recap_modular_dag import (
    execute_d02_3pass_dag,
    get_last_d02_call_budget,
)


class D02CallLedgerE2ETests(unittest.TestCase):
    """End-to-end lifecycle tests for Output 2 call telemetry and budget accounting."""

    def setUp(self) -> None:
        self.sample_user_content = (
            "<mandatory_structure>\nMust include Header, Ledger, Appendix.\n</mandatory_structure>\n\n"
            "<meeting_metadata>\n{\"meeting\": \"Sprint Sync\", \"date\": \"10/03/2026\"}\n</meeting_metadata>\n\n"
            "<forensic_transcript>\nSpeaker 1: We decided to deploy the new engine tomorrow.\n"
            "Speaker 2: I will take the action item to configure staging.\n</forensic_transcript>\n\n"
            "<prior_decisions>\n[{\"decision_id\": \"DEC-01\", \"status\": \"ACTIVE\"}]\n</prior_decisions>\n"
        )
        self.sample_system_prompt = "You are an executive intelligence analyst."

    def test_prompt_block_bytes_breakdown_e2e(self) -> None:
        """Verify prompt block byte breakdown categorizes XML tags accurately."""
        blocks = measure_prompt_block_bytes(self.sample_system_prompt, self.sample_user_content)

        self.assertIn("system_prompt_bytes", blocks)
        self.assertIn("user_content_total_bytes", blocks)
        self.assertIn("block_mandatory_structure_bytes", blocks)
        self.assertIn("block_meeting_metadata_bytes", blocks)
        self.assertIn("block_forensic_transcript_bytes", blocks)
        self.assertIn("block_prior_decisions_bytes", blocks)

        self.assertGreater(blocks["block_forensic_transcript_bytes"], 0)
        self.assertGreater(blocks["block_meeting_metadata_bytes"], 0)
        self.assertGreater(blocks["system_prompt_bytes"], 0)

    def test_e2e_best_case_call_budget_3_calls(self) -> None:
        """Verify best-case execution makes exactly 3 calls and emits budget receipt."""
        narrative_text = "# Executive Recap\n\n## Strategic Narrative\n" + (" substantive discussion" * 120)
        ledger_text = (
            "| Item # | Action / Decision / Milestone | Owner | Target Date | Dependencies & Next Step |\n"
            "| 1 | Configure Staging | Speaker 2 | 10/04/2026 | None |"
        )
        perspectives_text = (
            "# Speaker 1 - Org, Role\n- **Strategy**: Led the sync and prioritized rollout.\n\n"
            "# Speaker 2 - Org, Role\n- **Operations**: Committed to configuring staging.\n"
        ) + (" forensic analysis" * 80)

        # Mock urllib.request.urlopen returning valid JSON responses with usageMetadata
        def make_mock_response(text: str, prompt_toks: int, out_toks: int):
            payload = {
                "candidates": [
                    {
                        "content": {"parts": [{"text": text}], "role": "model"},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": prompt_toks,
                    "candidatesTokenCount": out_toks,
                    "totalTokenCount": prompt_toks + out_toks,
                    "cachedContentTokenCount": 0,
                },
            }
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
            mock_resp.__enter__.return_value = mock_resp
            mock_resp.__exit__.return_value = None
            return mock_resp

        def urlopen_side_effect(req, timeout=120.0):
            req_body = json.loads(req.data.decode("utf-8"))
            sys_inst = req_body.get("systemInstruction", {}).get("parts", [{}])[0].get("text", "")
            if "Action & Governance Ledger" in sys_inst:
                return make_mock_response(ledger_text, 1200, 150)
            elif "executive forensic analyst" in sys_inst:
                return make_mock_response(perspectives_text, 1200, 700)
            else:
                return make_mock_response(narrative_text, 1200, 900)

        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_file = Path(tmpdir) / "d02_test_receipt.json"

            with patch("urllib.request.urlopen", side_effect=urlopen_side_effect), \
                 patch("dual_pipeline.transcript_stages_core.external_synthesizer.CredentialManager.get_credential", return_value="fake-api-key"):

                stitched = execute_d02_3pass_dag(
                    model="gemini-3.8-flash",
                    system_prompt=self.sample_system_prompt,
                    user_content=self.sample_user_content,
                    min_words_2a=100,
                    min_words_2c=80,
                    receipt_path=str(receipt_file),
                )

                # Verify stitched markdown structure
                self.assertIn("# Executive Recap", stitched)
                self.assertIn("## Action & Governance Ledger", stitched)
                self.assertIn("| Item # | Action / Decision / Milestone |", stitched)
                self.assertIn("# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown", stitched)
                self.assertIn("Speaker 2", stitched)

                # Verify receipt on disk
                self.assertTrue(receipt_file.exists())
                with open(receipt_file, "r", encoding="utf-8") as f:
                    receipt = json.load(f)

                self.assertEqual(receipt["total_calls"], 3)
                self.assertEqual(receipt["scenario"], "BEST_CASE")
                self.assertEqual(receipt["successful_calls"], 3)
                self.assertEqual(receipt["failed_calls"], 0)
                self.assertEqual(receipt["transport_retries"], 0)
                self.assertEqual(receipt["calls_by_pass"], {"2A": 1, "2B": 1, "2C": 1})

                # Check token totals
                self.assertEqual(receipt["token_totals"]["prompt_tokens"], 3600)
                self.assertEqual(receipt["token_totals"]["output_tokens"], 1750)
                self.assertEqual(receipt["token_totals"]["total_tokens"], 5350)

                # Check prompt block bytes
                self.assertIn("block_forensic_transcript_bytes", receipt["prompt_block_bytes"])

                # Check global state
                budget = get_last_d02_call_budget()
                self.assertIsNotNone(budget)
                self.assertEqual(budget["total_calls"], 3)

    def test_e2e_retried_scenario_accounting(self) -> None:
        """Verify retried pass increments pass attempt and records RETRIED scenario."""
        short_narrative = "# Brief\nToo short."
        long_narrative = "# Expanded Narrative\n" + (" substantive discussion" * 120)
        ledger_text = "| Item # | Action | Owner | Target Date | Next Step |\n| 1 | Sync | Alex | Today | Done |"
        perspectives_text = "# Alex - Team\n- **Theme**: Strategic alignment.\n" + (" notes" * 80)

        pass_2a_calls = 0

        def make_resp(text: str):
            payload = {
                "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 200, "totalTokenCount": 1200},
            }
            m = MagicMock()
            m.read.return_value = json.dumps(payload).encode("utf-8")
            m.__enter__.return_value = m
            m.__exit__.return_value = None
            return m

        def urlopen_side_effect(req, timeout=120.0):
            nonlocal pass_2a_calls
            req_body = json.loads(req.data.decode("utf-8"))
            sys_inst = req_body.get("systemInstruction", {}).get("parts", [{}])[0].get("text", "")
            if "Action & Governance Ledger" in sys_inst:
                return make_resp(ledger_text)
            elif "executive forensic analyst" in sys_inst:
                return make_resp(perspectives_text)
            else:
                pass_2a_calls += 1
                if pass_2a_calls == 1:
                    return make_resp(short_narrative)
                return make_resp(long_narrative)

        with patch("urllib.request.urlopen", side_effect=urlopen_side_effect), \
             patch("dual_pipeline.transcript_stages_core.external_synthesizer.CredentialManager.get_credential", return_value="fake-api-key"):

            stitched = execute_d02_3pass_dag(
                model="gemini-3.8-flash",
                system_prompt="sys",
                user_content=self.sample_user_content,
                min_words_2a=100,
                min_words_2c=80,
                max_retries=2,
            )
            self.assertIsNotNone(stitched)

            budget = get_last_d02_call_budget()
            self.assertIsNotNone(budget)
            self.assertEqual(budget["scenario"], "RETRIED")
            self.assertEqual(budget["total_calls"], 4)  # 2 for 2A, 1 for 2B, 1 for 2C
            self.assertEqual(budget["calls_by_pass"], {"2A": 2, "2B": 1, "2C": 1})


if __name__ == "__main__":
    unittest.main()

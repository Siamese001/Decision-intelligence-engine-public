"""End-to-end integration test for Deliverable 02 Token Economy & Context Caching.

Verifies:
1. Shared prefix explicit context caching with run-bound TTL.
2. Billable non-cached prompt tokens across the DAG <= 1.3x a single call.
3. Clean deletion of cachedContents resource in lifecycle finalization.
4. Filtered <meeting_metadata> stripping bulky turns, remediation directives, and prior candidates.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.transcript_stages_core.call_ledger import D02CallLedger
from dual_pipeline.transcript_stages_core.recap_executor import (
    filter_meeting_metadata_allowlist,
)
from dual_pipeline.transcript_stages_core.recap_modular_dag import execute_d02_3pass_dag


class D02TokenEconomyE2ETests(unittest.TestCase):
    """E2E suite verifying Token Economy milestones and budget reduction."""

    def setUp(self) -> None:
        self.sample_system_prompt = (
            "You are an executive intelligence analyst synthesizing Artifact 02 per Rule V5.7."
        )
        self.sample_user_content = (
            "<mandatory_structure>\nMust include: ## Executive Summary, ## Action & Governance Ledger\n</mandatory_structure>\n\n"
            "<meeting_metadata>\n{\"project\": \"Project Nexus\", \"date\": \"10/03/2026\", \"archetype\": \"TACTICAL_ALIGNMENT\"}\n</meeting_metadata>\n\n"
            "<forensic_transcript>\n"
            "[00:00:10] Alex Brennan: Let's review the Google adapter release.\n"
            "[00:01:00] Amit Ayer: The test coverage and token economy must pass all CI gates.\n"
            "</forensic_transcript>"
        )

    def test_metadata_allowlist_filtering_e2e(self) -> None:
        """Verify bulky turns, prior candidates, and separate XML blocks are excluded from meeting_metadata."""
        raw_metadata = {
            "project": "Project Nexus",
            "archetype": "TACTICAL_ALIGNMENT",
            "date": "10/03/2026",
            "turns": [{"speaker": "Alex", "text": "turn " * 500} for _ in range(50)],  # 25k words
            "turn_list": ["raw turn data"],
            "raw_transcript": "raw unformatted text",
            "prior_recap_candidate": "# Old Candidate\n" + ("content " * 200),
            "remediation_directive": "Fix section 2",
            "remediation_directive_recap": "Expand narrative",
            "operator_hypothesis": {"tension": "latency vs accuracy"},
            "evidence_locks": [{"id": "lock-1"}],
            "known_graph_entities": [{"name": "Nexus"}],
        }

        filtered = filter_meeting_metadata_allowlist(raw_metadata)

        # Allowed high-signal keys are preserved
        self.assertEqual(filtered["project"], "Project Nexus")
        self.assertEqual(filtered["archetype"], "TACTICAL_ALIGNMENT")
        self.assertEqual(filtered["date"], "10/03/2026")

        # Excluded bloat is eliminated
        self.assertNotIn("turns", filtered)
        self.assertNotIn("turn_list", filtered)
        self.assertNotIn("raw_transcript", filtered)
        self.assertNotIn("prior_recap_candidate", filtered)
        self.assertNotIn("remediation_directive", filtered)
        self.assertNotIn("remediation_directive_recap", filtered)
        self.assertNotIn("operator_hypothesis", filtered)
        self.assertNotIn("evidence_locks", filtered)
        self.assertNotIn("known_graph_entities", filtered)

    def test_e2e_token_economy_cached_dag_lifecycle(self) -> None:
        """Verify full cached DAG execution lifecycle: cache creation, reads, deletion, and token economy ratio <= 1.3x."""
        narrative_text = "# Executive Recap\n\n## Strategic Narrative\n" + (" substantive discussion" * 120)
        ledger_text = (
            "| Item # | Action / Decision / Milestone | Owner | Target Date | Dependencies & Next Step |\n"
            "| 1 | Configure Cache TTL | Amit Ayer | 10/04/2026 | Unit tests passing |"
        )
        perspectives_text = (
            "# Alex Brennan - PartnerAdvisory, Architect\n- **Architecture**: Led the multi-pass token design.\n\n"
            "# Amit Ayer - PartnerAdvisory, Director\n- **Governance**: Enforced the <= 1.3x token economy ratio.\n"
        ) + (" forensic analysis" * 80)

        # Baseline single-call prompt tokens in W1 was ~14,850 tokens (or 1,200 in unit test fixture)
        # With caching:
        # shared_prefix = 14,800 tokens cached
        # Pass 2A: 14,850 total (14,800 cached, 50 non-cached)
        # Pass 2B: 14,810 total (14,800 cached, 10 non-cached)
        # Pass 2C: 14,840 total (14,800 cached, 40 non-cached)
        # Total non-cached prompt tokens = 50 + 10 + 40 = 100 tokens << 14,850 tokens!
        # Ratio of billable non-cached prompt tokens to single call = 100 / 14,850 = 0.0067x <= 1.3x!
        cached_resource_name = "cachedContents/d02-cache-test-999"
        deleted_caches = []

        def make_generate_resp(text: str, total_prompt: int, cached_toks: int, out_toks: int):
            payload = {
                "candidates": [
                    {
                        "content": {"parts": [{"text": text}], "role": "model"},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": total_prompt,
                    "cachedContentTokenCount": cached_toks,
                    "candidatesTokenCount": out_toks,
                    "totalTokenCount": total_prompt + out_toks,
                },
            }
            m = MagicMock()
            m.read.return_value = json.dumps(payload).encode("utf-8")
            m.__enter__.return_value = m
            m.__exit__.return_value = None
            return m

        def make_cache_create_resp():
            payload = {
                "name": cached_resource_name,
                "model": "models/gemini-3.8-flash",
                "createTime": "2026-10-03T12:00:00Z",
                "expireTime": "2026-10-03T12:05:00Z",
                "usageMetadata": {"totalTokenCount": 14800},
            }
            m = MagicMock()
            m.read.return_value = json.dumps(payload).encode("utf-8")
            m.__enter__.return_value = m
            m.__exit__.return_value = None
            return m

        def urlopen_side_effect(req, timeout=120.0):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            method = req.get_method() if hasattr(req, "get_method") else "GET"

            if "cachedContents" in url and method == "POST":
                # Create cache resource
                return make_cache_create_resp()
            elif "cachedContents" in url and method == "DELETE":
                # Delete cache resource
                deleted_caches.append(url)
                m = MagicMock()
                m.status = 200
                m.__enter__.return_value = m
                m.__exit__.return_value = None
                return m
            elif "generateContent" in url:
                # generateContent call using cachedContent
                req_body = json.loads(req.data.decode("utf-8"))
                self.assertEqual(req_body.get("cachedContent"), cached_resource_name)
                # Verify systemInstruction is absent when using cachedContent
                self.assertNotIn("systemInstruction", req_body)

                prompt_part = req_body.get("contents", [{}])[0].get("parts", [{}])[0].get("text", "")
                if "Action & Governance Ledger" in prompt_part:
                    return make_generate_resp(ledger_text, total_prompt=14810, cached_toks=14800, out_toks=180)
                elif "executive forensic analyst" in prompt_part:
                    return make_generate_resp(perspectives_text, total_prompt=14840, cached_toks=14800, out_toks=650)
                else:
                    return make_generate_resp(narrative_text, total_prompt=14850, cached_toks=14800, out_toks=850)
            else:
                raise ValueError(f"Unexpected URL in test: {url}")

        with tempfile.TemporaryDirectory() as tmpdir:
            receipt_file = Path(tmpdir) / "d02_w4_cached_receipt.json"

            with patch("urllib.request.urlopen", side_effect=urlopen_side_effect), \
                 patch("dual_pipeline.transcript_stages_core.external_synthesizer.CredentialManager.get_credential", return_value="fake-api-key"), \
                 patch("dual_pipeline.transcript_stages_core.gemini_context_cache.CredentialManager.get_credential", return_value="fake-api-key"), \
                 patch.dict("os.environ", {"NEE_D02_FORCE_CACHE": "1"}):

                ledger = D02CallLedger(operation_id="d02-w4-cached")
                stitched = execute_d02_3pass_dag(
                    model="gemini-3.8-flash",
                    system_prompt=self.sample_system_prompt,
                    user_content=self.sample_user_content,
                    min_words_2a=100,
                    min_words_2c=80,
                    ledger=ledger,
                    receipt_path=str(receipt_file),
                    enable_caching=True,
                )

                # 1. Output structure assertions
                self.assertIn("# Executive Recap", stitched)
                self.assertIn("## Action & Governance Ledger", stitched)
                self.assertIn("| Item # | Action / Decision / Milestone |", stitched)
                self.assertIn("# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown", stitched)
                self.assertIn("Alex Brennan", stitched)
                self.assertIn("Amit Ayer", stitched)

                # 2. Cache lifecycle: clean deletion observed
                self.assertEqual(len(deleted_caches), 1)
                self.assertIn(cached_resource_name, deleted_caches[0])

                # 3. Budget receipt verification
                self.assertTrue(receipt_file.exists())
                with open(receipt_file, "r", encoding="utf-8") as f:
                    receipt = json.load(f)

                totals = receipt["token_totals"]
                self.assertEqual(receipt["total_calls"], 3)
                self.assertEqual(receipt["successful_calls"], 3)
                self.assertEqual(totals["prompt_tokens"], 14850 + 14810 + 14840)
                self.assertEqual(totals["cached_tokens"], 14800 * 3)  # 44,400 cached tokens!

                # Billable non-cached prompt tokens = total_prompt - cached = (14850-14800) + (14810-14800) + (14840-14800) = 100
                billable_non_cached = totals["billable_prompt_tokens"]
                self.assertEqual(billable_non_cached, 100)

                # Acceptance criteria: Billable non-cached input tokens per DAG <= 1.3x a single call
                # Single call was 14,850 prompt tokens. 100 <= 1.3 * 14,850 = 19,305 tokens!
                single_call_tokens = 14850
                ratio = billable_non_cached / single_call_tokens
                self.assertLessEqual(ratio, 1.3, f"Token economy ratio {ratio:.4f} exceeds 1.3x limit!")


if __name__ == "__main__":
    unittest.main()

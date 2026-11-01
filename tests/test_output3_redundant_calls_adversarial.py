"""tests/test_output3_redundant_calls_adversarial.py

Adversarial and edge-case test suite for Wave 3 redundant/multiplicative call elimination.
Verifies that:
1. OpenAI Output 3 truncation (finish_reason='length') raises SynthesisTruncationError without looping.
2. Google Gemini truncation (finishReason='MAX_TOKENS') raises SynthesisTruncationError fail-fast.
3. build_stage03_user_content strips duplicate large objects from <meeting_metadata>.
4. build_stage03_user_content deduplicates and caps known_graph_entities to <= 100 entries.
5. check_idempotent_meeting_run invalidates prior cache when prompt template hash changes.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.transcript_pipeline_core.caching import (
    check_idempotent_meeting_run,
)
from dual_pipeline.transcript_stages_core.graph_executor import build_stage03_user_content
from dual_pipeline.transcript_stages_core.models import SynthesisTruncationError
from dual_pipeline.transcript_stages_core.synthesizers import LLMTranscriptSynthesizer


class Output3RedundantCallsAdversarialTests(unittest.TestCase):
    def test_openai_output3_truncation_fail_fast(self) -> None:
        """When OpenAI hits length limit on Output 3, fail fast without making an identical second attempt."""
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.choices = [
            MagicMock(
                finish_reason="length",
                message=MagicMock(content='{"incomplete": "json'),
            )
        ]
        mock_resp.usage = MagicMock(
            prompt_tokens=5000,
            completion_tokens=10000,
            prompt_tokens_details=None,
            completion_tokens_details=None,
        )
        mock_client.chat.completions.create.return_value = mock_resp

        synth = LLMTranscriptSynthesizer(model="gpt-6-astra", reasoning_effort="high", _client=mock_client)

        with self.assertRaises(SynthesisTruncationError) as ctx:
            synth.synthesize_graph("You are a knowledge graph extractor.", "Extract entities from transcript.")

        self.assertIn("truncated mid-stream due to output token limit", str(ctx.exception))
        # Ensure only 1 API call was made — no expensive identical retry!
        self.assertEqual(mock_client.chat.completions.create.call_count, 1)

    def test_gemini_output3_truncation_fail_fast(self) -> None:
        """When Gemini returns finishReason='MAX_TOKENS', fail fast with SynthesisTruncationError."""
        resp_data = {
            "candidates": [
                {
                    "content": {"parts": [{"text": "{\"partial\": true"}]},
                    "finishReason": "MAX_TOKENS",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 6000,
                "candidatesTokenCount": 64000,
                "totalTokenCount": 70000,
            },
        }

        mock_http_resp = MagicMock()
        mock_http_resp.read.return_value = json.dumps(resp_data).encode("utf-8")
        mock_http_resp.__enter__.return_value = mock_http_resp

        with (
            patch("urllib.request.urlopen", return_value=mock_http_resp),
            patch("dual_pipeline.credentials.CredentialManager.get_credential", return_value="fake_api_key"),
        ):
            from dual_pipeline.transcript_stages_core.external_synthesizer import (
                call_gemini_generate_content,
            )

            with self.assertRaises(SynthesisTruncationError) as ctx:
                call_gemini_generate_content(
                    model="gemini-2.5-pro",
                    system_prompt="Extract graph",
                    user_content="Transcript text",
                )

            self.assertIn("truncated mid-stream due to output token limit", str(ctx.exception))

    def test_output3_prompt_metadata_deduplication(self) -> None:
        """Large redundant keys must not be duplicated into <meeting_metadata>."""
        large_entities = [{"node_id": f"entity:{i}", "name": f"Entity {i}"} for i in range(10)]
        meta = {
            "meeting_name": "Architecture Review",
            "date": "10/01/2026",
            "known_graph_entities": large_entities,
            "prior_canonical_projection": '{"prior": true}',
            "prior_context": "Previous meeting notes",
        }

        prompt = build_stage03_user_content(
            forensic_transcript="Alice: Hello team.",
            metadata=meta,
        )

        # <meeting_metadata> block must NOT contain the large entity array or prior projection
        self.assertIn("<meeting_metadata>", prompt)
        self.assertIn("<known_graph_entities>", prompt)

        # Extract <meeting_metadata> block
        meta_start = prompt.find("<meeting_metadata>")
        meta_end = prompt.find("</meeting_metadata>")
        meta_content = prompt[meta_start:meta_end]

        self.assertNotIn("known_graph_entities", meta_content)
        self.assertNotIn("prior_canonical_projection", meta_content)
        self.assertNotIn("prior_context", meta_content)

    def test_output3_prompt_known_entities_bounded(self) -> None:
        """Known graph entities with excessive count must be deduplicated and capped to 100."""
        # 150 entities with 50 duplicates
        many_entities = []
        for i in range(100):
            many_entities.append({"node_id": f"system:sys_{i}", "name": f"System {i}"})
        # Add 50 duplicate IDs
        for i in range(50):
            many_entities.append({"node_id": f"system:sys_{i}", "name": f"Duplicate System {i}"})
        # Add 50 new IDs (total 200 input items, 150 unique)
        for i in range(100, 150):
            many_entities.append({"node_id": f"system:sys_{i}", "name": f"System {i}"})

        meta = {
            "meeting_name": "Scalability Review",
            "known_graph_entities": many_entities,
        }

        prompt = build_stage03_user_content(
            forensic_transcript="Speaker: Discussion.",
            metadata=meta,
        )

        self.assertIn("<known_graph_entities>", prompt)
        entities_start = prompt.find("<known_graph_entities>")
        entities_end = prompt.find("</known_graph_entities>")
        entities_block = prompt[entities_start:entities_end]

        # The capped list must contain sys_99 but NOT exceed 100 items (so sys_149 must be omitted)
        self.assertIn("system:sys_99", entities_block)
        self.assertNotIn("system:sys_149", entities_block)

    def test_idempotent_cache_invalidated_on_prompt_template_change(self) -> None:
        """When prompt template hash has changed, check_idempotent_meeting_run must return None."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_p = Path(tmp_dir)
            base = "Review_10-01-2026_0900"
            (out_p / f"01_{base}_hitl_resolved_transcript.md").write_text("transcript", encoding="utf-8")
            (out_p / f"02_{base}_executive_recap.md").write_text("recap", encoding="utf-8")
            (out_p / f"03_{base}_canonical_signal_projection.json").write_text("{}", encoding="utf-8")

            # Final release decision recorded with a specific prompt template hash
            decision_payload = {
                "status": "PASS",
                "input_hash": "input_sha_abc123",
                "prompt_template_hash": "old_prompt_hash_11111",
            }
            (out_p / "final_release_decision.json").write_text(json.dumps(decision_payload), encoding="utf-8")

            meta = {
                "meeting_name": "Review",
                "date_filename": "10-01-2026",
                "time_filename": "0900",
            }

            # With mock compute_prompt_template_hash returning a DIFFERENT current hash:
            with patch("dual_pipeline.transcript_pipeline_core.caching.compute_prompt_template_hash", return_value="new_prompt_hash_22222"):
                result = check_idempotent_meeting_run(
                    output_dir=out_p,
                    metadata=meta,
                    source_file_hash="input_sha_abc123",
                )
                self.assertIsNone(result)

            # With MATCHING current hash:
            with patch("dual_pipeline.transcript_pipeline_core.caching.compute_prompt_template_hash", return_value="old_prompt_hash_11111"):
                result = check_idempotent_meeting_run(
                    output_dir=out_p,
                    metadata=meta,
                    source_file_hash="input_sha_abc123",
                )
                self.assertIsNotNone(result)
                self.assertIn("01_transcript", result)


if __name__ == "__main__":
    unittest.main()

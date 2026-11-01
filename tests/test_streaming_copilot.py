"""
tests/test_streaming_copilot.py

Unit tests for StreamingCopilotHandler progressive token streaming and JSON repair.
"""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from dual_pipeline.live_meeting_recorder_core.streaming_copilot import (
    StreamingCopilotHandler,
    re_repair_json,
)


class TestStreamingCopilot(unittest.TestCase):
    def test_re_repair_json_closed_and_unclosed(self):
        valid = '{"has_guidance": true}'
        self.assertEqual(re_repair_json(valid), '{"has_guidance": true}')

        unclosed = '{"has_guidance": true, "snap_cue": "Pivot"'
        self.assertEqual(re_repair_json(unclosed), '{"has_guidance": true, "snap_cue": "Pivot"}')

        prefix_noise = 'Here is JSON: {"has_guidance": false}'
        self.assertEqual(re_repair_json(prefix_noise), '{"has_guidance": false}')

        no_json = 'Plain text without braces'
        self.assertIsNone(re_repair_json(no_json))

    def test_streaming_token_emission(self):
        # Mock OpenAI stream chunks
        sample_payload = {
            "has_guidance": True,
            "category": "STRATEGIC",
            "snap_cue": "Phase the rollout",
            "suggested_script": "I suggest we phase into a two-week spike before committing.",
            "risk_trap": "Unbudgeted commitment",
            "confidence": 0.95,
        }
        json_str = json.dumps(sample_payload)

        # Split into token pieces
        tokens = [json_str[i : i + 8] for i in range(0, len(json_str), 8)]

        mock_chunks = []
        for t in tokens:
            choice = SimpleNamespace(delta=SimpleNamespace(content=t))
            mock_chunks.append(SimpleNamespace(choices=[choice]))

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = iter(mock_chunks)

        handler = StreamingCopilotHandler(client=mock_client)
        streamed_tokens = []

        def on_token(token):
            streamed_tokens.append(token)

        res = handler.execute_streaming_evaluation(
            call_kwargs={"model": "gpt-6-luna", "messages": []},
            on_token=on_token,
        )

        self.assertIsNotNone(res)
        self.assertEqual(res["track"], "TRACK_2_DEEP_REASONING")
        self.assertEqual(res["snap_cue"], "Phase the rollout")
        self.assertGreaterEqual(len(streamed_tokens), 1)
        self.assertEqual("".join(streamed_tokens), json_str)
        self.assertIn("first_token_ms", res)
        self.assertIn("total_reasoning_ms", res)


if __name__ == "__main__":
    unittest.main()

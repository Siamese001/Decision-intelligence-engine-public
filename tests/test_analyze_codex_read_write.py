#!/usr/bin/env python3
"""Tests for content-silent Codex rollout read/write analysis."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.analyze_codex_read_write import analyze_rollouts, last_rollout_usage


def token_event(
    *,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
) -> dict:
    return {
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": {
                "total_token_usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_input_tokens,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": 0,
                    "total_tokens": input_tokens + output_tokens,
                }
            },
        },
    }


class AnalyzeCodexReadWriteTests(unittest.TestCase):
    def _rollout(self, root: Path, name: str, events: list[dict]) -> Path:
        path = root / name
        path.write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )
        return path

    def test_last_snapshot_is_used_instead_of_summing_cumulative_events(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._rollout(
                Path(temporary),
                "rollout.jsonl",
                [
                    token_event(
                        input_tokens=100,
                        cached_input_tokens=90,
                        output_tokens=10,
                    ),
                    token_event(
                        input_tokens=200,
                        cached_input_tokens=180,
                        output_tokens=20,
                    ),
                ],
            )
            usage = last_rollout_usage(path)
        self.assertEqual(200, usage["input_tokens"])
        self.assertEqual(180, usage["cached_input_tokens"])
        self.assertEqual(20, usage["output_tokens"])

    def test_observed_241_to_1_and_96_percent_cache_posture_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = self._rollout(
                Path(temporary),
                "sol-rollout.jsonl",
                [
                    {"type": "response_item", "payload": {"content": "not emitted"}},
                    token_event(
                        input_tokens=24100,
                        cached_input_tokens=23136,
                        output_tokens=100,
                    ),
                ],
            )
            report = analyze_rollouts([path])
        self.assertEqual("BLOCK", report["status"])
        self.assertEqual(241.0, report["read_write_ratio"])
        self.assertEqual(0.96, report["cached_input_share"])
        self.assertFalse(report["content_emitted"])
        self.assertIn(
            "STOP_OR_REROUTE_MODEL_EXECUTION",
            report["recommended_actions"],
        )


if __name__ == "__main__":
    unittest.main()

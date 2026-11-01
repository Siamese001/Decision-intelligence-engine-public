"""
Adversarial and edge-case tests for Wave 5: Copilot Live Fallback Parity & Temporal Gate Full-Scan Resolution.
Covers:
1. Overflow & exhaustion (>25 DLQ files where target date is at index 30+ is still discovered without pre-filter truncation)
2. Boundary / duplicate payload (identical live turn and graph assertion claims deduplicated in eval_respond)
3. Malformed input (corrupted DLQ JSON files skipped cleanly while still discovering valid DLQ blockers past index 25)
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.copilot_intents import _fallback_respond_live_assertions
from dual_pipeline.query_router_core.temporal_gate import (
    extract_temporal_target_date,
    scan_unpromoted_dlq_and_worktrees,
)


class _DummyDialogue:
    def __init__(self, turns: list[dict[str, str]], raw_request: str = "") -> None:
        self._turns = turns
        self._raw_request = raw_request

    def focus_turns(self) -> list[dict[str, str]]:
        return list(self._turns)


class TestCopilotAndTemporalGateAdversarial(unittest.TestCase):
    def test_adversarial_dlq_scan_finds_blocker_beyond_25_files_and_skips_corrupt_json(self) -> None:
        # Category: Overflow & exhaustion + Malformed input
        with tempfile.TemporaryDirectory() as tmp:
            rt_root = Path(tmp) / ".local" / "dual_pipeline"
            dlq_dir = rt_root / "dlq"
            dlq_dir.mkdir(parents=True, exist_ok=True)

            # Write 30 newer unrelated or corrupted DLQ files (dlq_010 to dlq_039)
            for i in range(10, 40):
                p = dlq_dir / f"dlq_{i:03d}.json"
                if i % 5 == 0:
                    p.write_text("{corrupt_json", encoding="utf-8")
                else:
                    p.write_text(
                        json.dumps(
                            {
                                "dlq_id": f"dlq_{i:03d}",
                                "stage": "L2",
                                "metadata": {"date": "2026-10-28", "meeting_name": "Unrelated Sync"},
                            }
                        ),
                        encoding="utf-8",
                    )

            # Write target DLQ file at dlq_001.json (sorted 31st in reverse order!)
            (dlq_dir / "dlq_001.json").write_text(
                json.dumps(
                    {
                        "dlq_id": "dlq_target_001",
                        "stage": "U0",
                        "error_type": "HITLRequiredError",
                        "error_message": "Blocked on speaker attribution",
                        "metadata": {"date": "2026-10-19", "meeting_name": "Maximo Cutover Review"},
                    }
                ),
                encoding="utf-8",
            )

            diag = scan_unpromoted_dlq_and_worktrees(
                "2026-10-19",
                ["maximo", "cutover"],
                repo_root=Path(tmp),
                runtime_root=rt_root,
            )
            self.assertEqual(len(diag["dlq_blockers"]), 1)
            self.assertEqual(diag["dlq_blockers"][0]["dlq_id"], "dlq_target_001")

    def test_adversarial_live_assertions_deduplicate_exact_claims_and_prioritize_directive(self) -> None:
        # Category: Boundary / duplicate claim deduplication
        existing = [
            {
                "assertion_id": "asrt_1",
                "predicate": "COMMITTED_TO",
                "claim_text": "We must validate the Maximo disaster recovery runbook before Friday",
            }
        ]
        dialogue = _DummyDialogue(
            turns=[
                {
                    "seq": "1",
                    "speaker": "Speaker_Beta",
                    "text": "We must validate the Maximo disaster recovery runbook before Friday.",
                },
                {
                    "seq": "2",
                    "speaker": "Speaker_Beta",
                    "text": "We also need to lock the Snowflake schema contract by Wednesday.",
                },
            ],
            raw_request="respond to what Speaker_Beta just said in the live transcript",
        )
        merged = _fallback_respond_live_assertions(existing, dialogue, "Speaker_Beta", None)
        claims = [a.get("claim_text") for a in merged]
        self.assertEqual(len(claims), 2)
        # Explicit live directive puts the new live turn first with live_directive_lock=True
        self.assertTrue(merged[0].get("live_directive_lock"))
        self.assertIn("Snowflake schema contract", str(merged[0].get("claim_text")))

    def test_adversarial_date_extraction_handles_2digit_and_ordinal_month_formats(self) -> None:
        # Category: Boundary date formats
        self.assertEqual(extract_temporal_target_date("Meeting on 09-05-26"), "2026-09-05")
        self.assertEqual(extract_temporal_target_date("Notes from Oct. 3rd, 26"), "2026-10-03")
        self.assertEqual(extract_temporal_target_date("Notes from 15th of October 2026"), "2026-10-15")


if __name__ == "__main__":
    unittest.main()

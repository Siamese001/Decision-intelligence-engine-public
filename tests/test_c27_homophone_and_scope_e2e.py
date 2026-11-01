"""
tests/test_c27_homophone_and_scope_e2e.py

End-to-end verification for Wave 1 & Wave 2:
1. Live speaker hint path resolution discovers segments in sibling output/transcript_cache/<project>/<date>/
2. Queued quote hints in speaker_hints_queue.json deterministically reconcile against transcript turns
   and propagate across Brandon Que's Confluence SAA demo turns ([00:28:03] - [00:28:29]), overriding
   collapsed single-speaker fallback attribution ('Andy Kilinskis' -> 'Brandon Que')
3. ASR product homophone ('Sarah' -> 'Sherpa') is normalized on line 407 ([00:28:29])
4. Operator-provided full attendee names ('David Childers', 'Brandon Que') never collide with
   global directory first-name prefix priors ('David Decof', 'Brandon Dass')
5. Gate 1.10 passes cleanly on the reconciled transcript without any 'Sarah Duffy' mapping
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

from dual_pipeline.canonical_pipeline.u0_ingress import (
    hydrate_live_container_artifacts,
    reconcile_queued_hints_against_turns,
)
from dual_pipeline.cli.speaker_hint_handlers import _resolve_target_paths_from_dir
from dual_pipeline.live_meeting_recorder_core.attendees import parse_attendees
from dual_pipeline.transcript_qa_core.gate1_entity_context import (
    evaluate_referenced_entity_context_gate,
)

SPRINT_DEMO_ATTENDEES_STR = (
    "David Childers, Speaker_Alpha Muschett, Marcus Balk, Brandon Que, Catalina Orostegui, "
    "Jeffrey Glade, Andy Kilinskis, Lillian Le, Egle Tamasevice, Amit Ayer, "
    "Jennifer Pan, Alex Sanchez, Jake Stark, Morgan Vance Krutilla"
)


class TestC27HomophoneAndScopeE2E(unittest.TestCase):
    """End-to-end lifecycle verification for C-27 project scoping and live speaker hint reconciliation."""

    def test_full_name_attendees_never_degrade_to_global_directory_prefix_collisions(self) -> None:
        parsed = parse_attendees(SPRINT_DEMO_ATTENDEES_STR, project_name="project_beacon")
        names = [p["name"] for p in parsed]
        self.assertIn("David Childers", names)
        self.assertIn("Brandon Que", names)
        self.assertNotIn("David Decof", names)
        self.assertNotIn("Brandon Dass", names)
        self.assertNotIn("Sarah Duffy", names)

    def test_speaker_hint_handler_resolves_segments_from_sibling_transcript_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            container_dir = root / "output" / "project_beacon" / "10-16-2026" / "Sprint_Demo_Marcus_PartnerAdvisory"
            cache_dir = root / "output" / "transcript_cache" / "project_beacon" / "10-16-2026"
            container_dir.mkdir(parents=True)
            cache_dir.mkdir(parents=True)

            seg_file = cache_dir / "01_10-16-2026_Sprint_Demo_Marcus_PartnerAdvisory_segments.jsonl"
            seg_file.write_text(
                json.dumps({"chunk_idx": 1, "voice_id": "Voice A", "speaker": "Voice A", "text": "I will show you a confluence page"}) + "\n",
                encoding="utf-8",
            )
            (cache_dir / "session_manifest.json").write_text(
                json.dumps({
                    "meeting": "Sprint_Demo_Marcus_PartnerAdvisory",
                    "project": "project_beacon",
                    "attendees": ["Brandon Que", "Andy Kilinskis"],
                    "segments_jsonl": str(seg_file),
                }),
                encoding="utf-8",
            )

            _, resolved_cache, resolved_segs, _, slug, attendees, project, _ = _resolve_target_paths_from_dir(
                str(container_dir)
            )
            self.assertEqual(resolved_segs, seg_file)
            self.assertEqual(resolved_cache, cache_dir)
            self.assertEqual(project, "project_beacon")
            self.assertIn("Brandon Que", attendees)

    def test_end_to_end_reconciliation_of_sprint_demo_hints_and_line_407_homophone(self) -> None:
        turns: List[Dict[str, Any]] = [
            {
                "timestamp": "00:28:03",
                "speaker": "Andy Kilinskis",
                "voice_id": "Voice A",
                "text": "I don't have a live demo for you guys today, so I'll show you a Confluence page on what to expect for when this feature is released.",
            },
            {
                "timestamp": "00:28:25",
                "speaker": "Andy Kilinskis",
                "voice_id": "Voice A",
                "text": "Because the architecture review is one of the longer processes here at Apex Global.",
            },
            {
                "timestamp": "00:28:29",
                "speaker": "Andy Kilinskis",
                "voice_id": "Voice A",
                "text": "But based on the requirements and what it takes in, Sarah would know to start an architecture review by today. And she could start now. She'll run through the questions, ask the user for clarification, like how SAA does currently.",
            },
            {
                "timestamp": "00:28:54",
                "speaker": "Andy Kilinskis",
                "voice_id": "Voice A",
                "text": "When we're confident enough and the user says, yeah, we're ready to generate an architecture diagram, it'll ask you to approve before it starts.",
            },
            {
                "timestamp": "00:29:03",
                "speaker": "Andy Kilinskis",
                "voice_id": "Voice A",
                "text": "And once you do approve, it'll work out everything it needs to, and it'll generate an architecture diagram via the diagram generation tool.",
            },
        ]
        hints: List[Dict[str, Any]] = [
            {
                "hint_id": "hint_c3fe20bf",
                "speaker_name": "Brandon Que",
                "quote": "I will show you a confluence page",
                "status": "QUEUED",
            },
            {
                "hint_id": "hint_653ca732",
                "speaker_name": "Brandon Que",
                "quote": "once you do approve it will work out everything it needs to",
                "status": "QUEUED",
            },
        ]

        reconciled_turns, reconciled_hints = reconcile_queued_hints_against_turns(turns, hints)

        # 1. Both hints transition from QUEUED to RECONCILED
        self.assertTrue(all(h["status"] == "RECONCILED" for h in reconciled_hints))
        self.assertTrue(all(h["updated_segments_count"] >= 1 for h in reconciled_hints))

        # 2. All turns in the bounded Confluence SAA presentation window resolve to Brandon Que (including [00:28:29])
        for t in reconciled_turns:
            self.assertEqual(t["speaker"], "Brandon Que")

        # 3. Line 407 ([00:28:29]) ASR product homophone 'Sarah' is normalized to 'Sherpa'
        turn_2829 = reconciled_turns[2]
        self.assertIn("Sherpa would know to start an architecture review", turn_2829["text"])
        self.assertNotIn("Sarah", turn_2829["text"])

        # 4. Gate 1.10 passes cleanly on the resulting transcript without needing any 'Sarah Duffy' mapping
        f1_lines = ["# Forensic Transcript\n", "**Date:** 10/16/2026\n"]
        for t in reconciled_turns:
            f1_lines.append(f"### [{t['timestamp']}]\n\n**{t['speaker']}:** {t['text']}\n")
        f1_text = "\n".join(f1_lines)
        f2_text = "## Executive Recap\n\nBrandon Que (PartnerAdvisory, Software Engineer) demonstrated how Sherpa routes SAA architecture reviews.\n"

        roster_list = [s.strip() for s in SPRINT_DEMO_ATTENDEES_STR.split(",")]
        passed, failures = evaluate_referenced_entity_context_gate(
            f1_text=f1_text,
            f2_text=f2_text,
            roster_names=roster_list,
            entity_clarifications=[],
            project="project_beacon",
        )
        self.assertTrue(passed, f"Expected Gate 1.10 to pass, got failures: {failures}")


if __name__ == "__main__":
    unittest.main()

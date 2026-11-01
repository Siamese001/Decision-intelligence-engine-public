"""
tests/test_speaker_hint_turn_attribution_e2e.py

End-to-end test for Wave 3: Speaker Hint Reconciliation & Turn-Level Attribution.
Verifies:
1. extract_speaker_turns() and extract_ordered_dialogue_turns() match **[MM:SS]** Voice A: lines.
2. Turn-level quote hints attribute turns across all hinted speakers before cluster mapping.
3. Hints transition to RECONCILED with cryptographic/status updates in receipts.
4. resolve_meeting_container_dir resolves container from transcript_cache WAVs.
5. hydrate_live_container_artifacts hydrates turn-level hints even when voice_id is null.
6. speaker_hints*.json and speaker_map files are never copied into referenced_artifacts/.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.attachment_core.detector import (
    discover_attachments,
    stage_attachments_into_output,
)
from dual_pipeline.canonical_pipeline.u0_ingress import (
    hydrate_live_container_artifacts,
    _hydrate_speaker_artifacts,
)
from dual_pipeline.live_meeting_recorder_core.live_voice_registry import (
    LiveVoiceRegistry,
    VoiceCluster,
)
from dual_pipeline.live_meeting_recorder_core.speaker_hint_queue import (
    DurableSpeakerHintQueue,
    QueuedSpeakerHint,
)
from dual_pipeline.live_meeting_recorder_core.speaker_hints import (
    SpeakerHintManager,
)
from dual_pipeline.naming import resolve_meeting_container_dir
from dual_pipeline.speaker_attribution_core.attribution_engine import (
    AudioSpeakerAttributionEngine,
    apply_quote_hints_to_turns,
)
from dual_pipeline.speaker_attribution_core.candidate_scorer import (
    extract_ordered_dialogue_turns,
    extract_speaker_turns,
    load_speaker_hints,
)


class TestSpeakerHintTurnAttributionE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_hint_attrib_e2e_")
        self.temp_path = Path(self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_extract_speaker_turns_with_timestamps(self) -> None:
        """Verifies extract_speaker_turns matches leading **[MM:SS]** timestamps and formats."""
        sample_transcript = """
# Live Meeting Transcript
**[00:15]** Voice A: Good morning everyone, Daniel Sabet here.
**[00:30]** **Voice A**: Thanks Daniel, this is Jen Pan.
[00:45] Voice A: PartnerAdvisory team joining in as well.
## [01:00] Voice A: Let us review the slide deck.
**Speaker 1:** Here is another turn.
**[01:30]** Speaker 1: Closing thoughts.
"""
        turns = extract_speaker_turns("Voice A", sample_transcript)
        self.assertEqual(len(turns), 4)
        self.assertIn("Good morning everyone, Daniel Sabet here.", turns)
        self.assertIn("Thanks Daniel, this is Jen Pan.", turns)
        self.assertIn("PartnerAdvisory team joining in as well.", turns)
        self.assertIn("Let us review the slide deck.", turns)

        ordered = extract_ordered_dialogue_turns(sample_transcript)
        self.assertEqual(len(ordered), 6)
        self.assertEqual(ordered[0], ("Voice A", "Good morning everyone, Daniel Sabet here."))
        self.assertEqual(ordered[1], ("Voice A", "Thanks Daniel, this is Jen Pan."))
        self.assertEqual(ordered[2], ("Voice A", "PartnerAdvisory team joining in as well."))
        self.assertEqual(ordered[3], ("Voice A", "Let us review the slide deck."))
        self.assertEqual(ordered[4], ("Speaker 1", "Here is another turn."))
        self.assertEqual(ordered[5], ("Speaker 1", "Closing thoughts."))

    def test_turn_level_hint_attribution_e2e(self) -> None:
        """Verifies quote hints attribute individual turns across multiple speakers, preventing collapse."""
        sample_transcript = """
**[00:15]** Voice A: Good morning everyone, this is Daniel Sabet from NEA.
**[00:35]** Voice A: Thanks Daniel, this is Jen Pan from the PartnerAdvisory side.
**[00:55]** Voice A: PartnerAdvisory team here on the call, ready to review the agenda.
**[01:15]** Voice A: Perfect, Daniel let us walk through the architecture updates.
"""
        hints = [
            {
                "hint_id": "h1",
                "speaker_name": "Daniel Sabet (NEA)",
                "quote": "this is Daniel Sabet from NEA",
                "status": "QUEUED",
            },
            {
                "hint_id": "h2",
                "speaker_name": "PartnerAdvisoryTeam",
                "quote": "PartnerAdvisory team here on the call",
                "status": "QUEUED",
            },
        ]

        metadata = {
            "project": "project_lens",
            "meeting_name": "LENS Business meeting",
            "date": "10-14-2026",
            "speaker_hints": hints,
            "attendees": [
                {"name": "Jen Pan", "org": "PartnerAdvisory"},
                {"name": "Daniel Sabet", "org": "NEA"},
                {"name": "PartnerAdvisoryTeam", "org": "PartnerAdvisory"},
            ],
            "speaker_map": {"Voice A": "Jen Pan"},
        }

        engine = AudioSpeakerAttributionEngine()
        res = engine.analyze_and_resolve(sample_transcript, metadata, autonomous_if_decisive=True)

        # Check that individual turns were attributed to distinct speakers
        lines = res.resolved_transcript.strip().splitlines()
        self.assertIn("Daniel Sabet (NEA)", lines[0])
        self.assertIn("Jen Pan", lines[1])
        self.assertIn("PartnerAdvisoryTeam", lines[2])
        self.assertIn("Jen Pan", lines[3])

        # Confirm hints transitioned to RECONCILED
        h1 = next(h for h in metadata["speaker_hints"] if h["hint_id"] == "h1")
        h2 = next(h for h in metadata["speaker_hints"] if h["hint_id"] == "h2")
        self.assertEqual(h1.get("status"), "RECONCILED")
        self.assertEqual(h2.get("status"), "RECONCILED")

    def test_transcript_cache_wav_container_resolution_e2e(self) -> None:
        """Verifies resolve_meeting_container_dir resolves the live container from transcript_cache WAV path."""
        # Create output container
        container_dir = self.temp_path / "output" / "project_lens" / "10-14-2026" / "lens_business_meeting"
        container_dir.mkdir(parents=True, exist_ok=True)
        manifest_file = container_dir / "session_manifest.json"
        manifest_file.write_text(
            json.dumps({
                "project": "project_lens",
                "meeting": "LENS Business meeting",
                "date": "10-14-2026",
                "time": "163149",
                "attendees": ["PartnerAdvisoryTeam", "Daniel Sabet (NEA)"],
            }),
            encoding="utf-8",
        )
        hints_queue_file = container_dir / "speaker_hints_queue.json"
        hints_queue_file.write_text(
            json.dumps([
                {
                    "hint_id": "hint_1",
                    "speaker_name": "Daniel Sabet (NEA)",
                    "quote": "Good morning everyone",
                    "voice_id": None,
                    "status": "QUEUED",
                }
            ]),
            encoding="utf-8",
        )

        # Create staged master WAV in transcript_cache
        tc_dir = self.temp_path / "output" / "transcript_cache" / "project_lens" / "10-14-2026"
        tc_dir.mkdir(parents=True, exist_ok=True)
        wav_file = tc_dir / "00_10-14-2026_lens_business_meeting_master_1792087485.wav"
        wav_file.write_bytes(b"RIFF" + b"\x00" * 40)

        # Resolve container
        resolved = resolve_meeting_container_dir(wav_file)
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.resolve(), container_dir.resolve())

        # Hydrate live container artifacts
        meta: dict = {"input_path": str(wav_file)}
        hydrated = hydrate_live_container_artifacts(meta)
        self.assertIn("time", hydrated)
        self.assertIn("meeting_name", hydrated)
        self.assertIn("speaker_hints", hydrated)
        self.assertIn("turn_hints", meta)
        self.assertEqual(len(meta["turn_hints"]), 1)
        self.assertIsNone(meta["turn_hints"][0]["voice_id"])
        self.assertEqual(meta["turn_hints"][0]["speaker_name"], "Daniel Sabet (NEA)")

    def test_speaker_hints_excluded_from_referenced_artifacts_e2e(self) -> None:
        """Verifies speaker_hints*.json and speaker_map files are never detected as meeting attachments."""
        container_dir = self.temp_path / "meeting_container"
        container_dir.mkdir(parents=True, exist_ok=True)

        # Place hint files and speaker maps
        (container_dir / "speaker_hints.json").write_text("[]", encoding="utf-8")
        (container_dir / "speaker_hints_queue.json").write_text("[]", encoding="utf-8")
        (container_dir / "speaker_hint_conflicts.jsonl").write_text("", encoding="utf-8")
        (container_dir / "meeting_speaker_map.json").write_text("{}", encoding="utf-8")
        (container_dir / "speaker_map.json").write_text("{}", encoding="utf-8")

        # Place genuine attachment
        genuine_img = container_dir / "architecture_diagram.png"
        genuine_img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)

        # Place transcript
        transcript_file = container_dir / "transcript.md"
        transcript_file.write_text("# Meeting\nVoice A: Hello", encoding="utf-8")

        candidates = discover_attachments(meeting_output_dir=container_dir, transcript_path=transcript_file)
        detected_names = {p.name for p in candidates}

        self.assertIn("architecture_diagram.png", detected_names)
        self.assertNotIn("speaker_hints.json", detected_names)
        self.assertNotIn("speaker_hints_queue.json", detected_names)
        self.assertNotIn("speaker_hint_conflicts.jsonl", detected_names)
        self.assertNotIn("meeting_speaker_map.json", detected_names)
        self.assertNotIn("speaker_map.json", detected_names)

        # Stage and verify referenced_artifacts/
        stage_attachments_into_output(candidates, container_dir)
        ref_dir = container_dir / "referenced_artifacts"
        staged_names = {f.name for f in ref_dir.glob("*")} if ref_dir.exists() else set()
        self.assertIn("architecture_diagram.png", staged_names)
        self.assertNotIn("speaker_hints_queue.json", staged_names)
        self.assertNotIn("speaker_hints.json", staged_names)

    def test_hint_queue_disk_reload_and_voice_registry_binding(self) -> None:
        """Verifies DurableSpeakerHintQueue reloads out-of-band disk writes and binds voice_registry."""
        queue_path = self.temp_path / "speaker_hints_queue.json"
        segments_path = self.temp_path / "segments.jsonl"
        transcript_md = self.temp_path / "transcript.md"

        # Initialize voice registry
        voice_reg = LiveVoiceRegistry(attendees=["Daniel Sabet"])
        voice_reg.voice_clusters["Voice A"] = VoiceCluster(
            voice_id="Voice A",
            resolved_speaker="Unknown",
        )

        manager = SpeakerHintManager(
            output_dir=self.temp_path,
            attendees=["Daniel Sabet"],
            voice_registry=voice_reg,
        )

        queue = DurableSpeakerHintQueue(queue_path=queue_path)

        # Write segments to disk
        seg_data = {
            "chunk_idx": 0,
            "voice_id": "Voice A",
            "speaker": "Voice A",
            "text": "Hello, Daniel Sabet joining the discussion.",
            "start": 0.0,
            "end": 3.0,
        }
        segments_path.write_text(json.dumps(seg_data) + "\n", encoding="utf-8")
        transcript_md.write_text("**[00:00]** Voice A: Hello, Daniel Sabet joining the discussion.\n", encoding="utf-8")

        # Out-of-band write to queue file simulating separate process / CLI
        out_of_band_hints = [
            {
                "hint_id": "hint_ext_1",
                "speaker_name": "Daniel Sabet",
                "quote": "Daniel Sabet joining the discussion",
                "raw_input": "Daniel - 'Daniel Sabet joining the discussion'",
                "session_elapsed_seconds": 1.5,
                "created_at": "2026-10-15T00:00:00Z",
                "status": "QUEUED",
                "voice_id": None,
                "updated_segments_count": 0,
                "ledger_digest": None,
                "metadata": {},
            }
        ]
        queue_path.write_text(json.dumps(out_of_band_hints, indent=2), encoding="utf-8")

        # Reconcile pending hints
        reconciled = queue.reconcile_pending_hints(
            speaker_hint_manager=manager,
            segments_jsonl_path=segments_path,
            transcript_md_path=transcript_md,
            meeting_slug="test_meeting",
            voice_registry=voice_reg,
        )

        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0].status, "PASS")
        self.assertEqual(reconciled[0].voice_id, "Voice A")

        # Verify disk queue was reloaded and updated to RECONCILED
        reloaded_queue = DurableSpeakerHintQueue(queue_path=queue_path)
        self.assertEqual(len(reloaded_queue.get_reconciled_hints()), 1)
        self.assertEqual(reloaded_queue.get_reconciled_hints()[0].hint_id, "hint_ext_1")

        # Verify voice_registry was bound
        self.assertEqual(voice_reg.voice_clusters["Voice A"].resolved_speaker, "Daniel Sabet")


if __name__ == "__main__":
    unittest.main()

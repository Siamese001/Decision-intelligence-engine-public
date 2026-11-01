"""
Unit tests for In-Band Fenced IDE UI Progress Tracking.

Validates that:
1. render_terminal_dashboard produces clean ASCII progress tables and supports fenced formatting.
2. LiveUISynthesizer streams progress in-band without writing ephemeral .md files to disk.
3. get_current_fenced_dashboard emits valid fenced markdown blocks for in-turn reporting.
"""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.lens_progress_renderer import (
    DEFAULT_LENS_METADATA,
    get_initial_output_status,
    get_initial_stages,
    make_progress_bar,
    render_terminal_dashboard,
)
from tools.run_lens_synthesis_with_live_ui import LiveUISynthesizer


class LiveUIFencedProgressTests(unittest.TestCase):
    def test_make_progress_bar(self) -> None:
        bar_0 = make_progress_bar(0, length=10)
        self.assertEqual(bar_0, "░" * 10)

        bar_50 = make_progress_bar(50, length=10)
        self.assertEqual(bar_50, "█" * 5 + "░" * 5)

        bar_100 = make_progress_bar(100, length=10)
        self.assertEqual(bar_100, "█" * 10)

    def test_render_terminal_dashboard_unfenced(self) -> None:
        stages = get_initial_stages()
        output_status = get_initial_output_status()
        dashboard = render_terminal_dashboard(
            step_idx=2,
            total_steps=len(stages),
            current_stage_name="Executive Recap Verification",
            stages=stages,
            output_status=output_status,
            elapsed_sec=42.5,
            model_name="gpt-6-luna",
            reasoning_effort="high",
            meeting_dir=Path("/path/to/meeting"),
            fenced=False,
        )

        self.assertNotIn("```", dashboard)
        self.assertIn("PROJECT LENS THREE-OUTPUT SYNTHESIS", dashboard)
        self.assertIn("gpt-6-luna (high)", dashboard)
        self.assertIn("Overall Pipeline Progress:", dashboard)
        self.assertIn("PIPELINE STAGES EXECUTION MATRIX", dashboard)
        self.assertIn("Output 01: Forensic Transcript", dashboard)
        self.assertIn("00:42", dashboard)

    def test_render_terminal_dashboard_fenced(self) -> None:
        stages = get_initial_stages()
        output_status = get_initial_output_status()
        fenced_dash = render_terminal_dashboard(
            step_idx=6,
            total_steps=len(stages),
            current_stage_name="COMPLETE",
            stages=stages,
            output_status=output_status,
            elapsed_sec=84.0,
            model_name="gpt-6-luna",
            reasoning_effort="high",
            meeting_dir=Path("/path/to/meeting"),
            fenced=True,
        )

        self.assertTrue(fenced_dash.startswith("```text\n"))
        self.assertTrue(fenced_dash.endswith("\n```"))
        self.assertIn("PROJECT LENS THREE-OUTPUT SYNTHESIS", fenced_dash)

    def test_live_ui_synthesizer_zero_disk_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            target_artifact = tmp_path / "live_pipeline_progress.md"
            self.assertFalse(target_artifact.exists())

            synthesizer = LiveUISynthesizer(
                meeting_dir=tmp_path,
                model="gpt-6-luna",
                reasoning_effort="high",
                live_artifact_path=target_artifact,
                fenced=True,
            )

            # Exercise _sync_ui without explicit print_dashboard (must default to True)
            with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
                synthesizer._sync_ui(1, "Stage 1 Test")
                synthesizer._animate_substeps("output_1", 3, "Substeps Test", 1, "Output 01")
                output = mock_stdout.getvalue()

            # Zero disk writes invariant: target_artifact must NEVER be created on disk
            self.assertFalse(
                target_artifact.exists(),
                "Target live_pipeline_progress.md must NOT be created on disk (zero disk artifact invariant).",
            )
            self.assertIn("PROJECT LENS THREE-OUTPUT SYNTHESIS", output)
            self.assertIn("```text", output)
            self.assertEqual(synthesizer.output_status["output_1"]["gates_passed"], 3)
            self.assertEqual(synthesizer.output_status["output_1"]["pct"], 100)

    def test_live_heartbeat_emission_and_stop(self) -> None:
        from tools.lens_progress_renderer import LiveHeartbeat
        stream = io.StringIO()
        with LiveHeartbeat(desc="Test Model Judging", interval=0.05, file=stream):
            import time
            time.sleep(0.12)
        output = stream.getvalue()
        self.assertIn("[HEARTBEAT", output)
        self.assertIn("Test Model Judging", output)

    def test_get_current_fenced_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            synthesizer = LiveUISynthesizer(
                meeting_dir=Path(tmp_dir),
                model="gpt-6-luna",
                reasoning_effort="high",
                fenced=True,
            )
            fenced_block = synthesizer.get_current_fenced_dashboard(step_idx=3, stage_name="Canonical Signal")
            self.assertTrue(fenced_block.startswith("```text\n"))
            self.assertTrue(fenced_block.endswith("\n```"))
            self.assertIn("Overall Pipeline Progress:", fenced_block)

    def test_default_metadata_conformance(self) -> None:
        self.assertEqual(DEFAULT_LENS_METADATA["project"], "project_lens")
        self.assertEqual(DEFAULT_LENS_METADATA["meeting"], "Jake_Amit_Miles_PartnerAdvisory")
        self.assertEqual(DEFAULT_LENS_METADATA["classification"], "confidential")


if __name__ == "__main__":
    unittest.main()

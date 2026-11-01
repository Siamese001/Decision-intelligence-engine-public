"""
End-to-End Test Suite for UI Progress Bars and Percentage Complete.

Validates that long-running pipelines, test runners, and UI telemetry
consistently emit monotonic percentage progress, accurate stage descriptions,
elapsed time metrics, and execute cleanly end-to-end.
"""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.transcript_pipeline import _PIPELINE_STEPS, RawTranscriptPipeline
from dual_pipeline.ui_progress import StreamlitProgressCallback, UIProgressTracker
from tools.run_tests_with_progress import ProgressBarTestRunner


class UIProgressBarTests(unittest.TestCase):
    """End-to-end tests for UI progress bars and percentage completion."""

    def test_ui_progress_tracker_calculations(self) -> None:
        """Verify UIProgressTracker percentage, step counting, ETA, and formatting."""
        events = []

        def callback(step: int, total: int, pct: float, desc: str, metrics: dict) -> None:
            events.append((step, total, pct, desc, metrics))

        tracker = UIProgressTracker(total_steps=5, title="Test Run", callback=callback)
        self.assertEqual(tracker.percentage, 0.0)
        self.assertEqual(tracker.fraction, 0.0)
        self.assertIn("0/5", tracker.format_status())

        # Step 1
        pct1 = tracker.update(step=1, stage_description="Stage One")
        self.assertEqual(pct1, 20.0)
        self.assertEqual(tracker.fraction, 0.2)
        self.assertIn("20.0%", tracker.format_status())
        self.assertIn("Stage One", tracker.format_status())

        # Step 3
        pct3 = tracker.update(step=3, stage_description="Stage Three")
        self.assertEqual(pct3, 60.0)
        self.assertEqual(tracker.fraction, 0.6)

        # Finish at 100%
        summary = tracker.finish("Complete")
        self.assertEqual(tracker.percentage, 100.0)
        self.assertEqual(tracker.fraction, 1.0)
        self.assertIn("Test Run completed", summary)

        # Verify callback events
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0][0], 1)
        self.assertEqual(events[0][2], 20.0)
        self.assertEqual(events[1][0], 3)
        self.assertEqual(events[1][2], 60.0)
        self.assertEqual(events[2][0], 5)
        self.assertEqual(events[2][2], 100.0)

    def test_streamlit_progress_callback(self) -> None:
        """Verify StreamlitProgressCallback updates st.progress and status containers."""
        mock_pbar = MagicMock()
        mock_status = MagicMock()

        callback = StreamlitProgressCallback(
            progress_bar=mock_pbar,
            status_container=mock_status,
            title_prefix="Stage",
        )

        callback(current_step=3, total_steps=9, description="C0 Context Gate", extra_metrics={"custom": "val"})

        # Verify progress bar call: 3/9 is approx 0.3333
        mock_pbar.progress.assert_called_once()
        args, kwargs = mock_pbar.progress.call_args
        self.assertAlmostEqual(args[0], 3 / 9, places=4)
        self.assertIn("Stage 3/9 (33%): C0 Context Gate", kwargs.get("text", ""))

        # Verify status container write call
        mock_status.write.assert_called_once()
        write_text = mock_status.write.call_args[0][0]
        self.assertIn("[3/9]", write_text)
        self.assertIn("33%", write_text)
        self.assertIn("C0 Context Gate", write_text)
        self.assertIn("custom: val", write_text)

    def test_raw_transcript_pipeline_with_progress_callback_e2e(self) -> None:
        """Execute RawTranscriptPipeline in offline mode and verify all 9 progress stages."""
        pipeline = RawTranscriptPipeline(provider_type="offline")

        progress_events: list[tuple[int, int, str]] = []

        def on_pipeline_progress(step: int, total: int, desc: str) -> None:
            progress_events.append((step, total, desc))

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            mock_input_file = tmp_path / "meeting_transcript.txt"
            mock_input_file.write_text(
                "**Reid Schenk:** Let's review the decoupled SQLite WAL architecture.\n"
                "**Amit Ayer:** The compute fork guarantees zero biometric leakage.\n"
                "**Lead PM:** Sprint planning is scheduled for Monday, 09/14/2026.",
                encoding="utf-8",
            )

            metadata = {
                "meeting_name": "Architecture Sync Decoupled WAL",
                "date": "09/12/2026",
                "date_filename": "09-12-2026",
                "time": "10:00AM - 11:00AM ET",
                "classification": "internal",
                "audiences": ["internal_consulting"],
            }
            out_dir = tmp_path / "artifacts"

            result = pipeline.run(
                input_path=str(mock_input_file),
                metadata=metadata,
                output_dir=str(out_dir),
                progress_callback=on_pipeline_progress,
            )

            # 1. Verify all 9 stages executed and notified callback
            self.assertEqual(len(progress_events), _PIPELINE_STEPS)
            self.assertEqual(_PIPELINE_STEPS, 9)

            for idx, (step_idx, total_steps, stage_desc) in enumerate(progress_events, start=1):
                self.assertEqual(step_idx, idx)
                self.assertEqual(total_steps, 9)
                self.assertTrue(len(stage_desc) > 0)

            # Stage 1 must be U0, Stage 2 L1, Stage 9 X-gates
            self.assertIn("U0", progress_events[0][2])
            self.assertIn("L1", progress_events[1][2])
            self.assertIn("C0", progress_events[2][2])
            self.assertIn("L0", progress_events[3][2])
            self.assertIn("PA", progress_events[4][2])
            self.assertIn("L2-Stage01", progress_events[5][2])
            self.assertIn("L2-Stage02", progress_events[6][2])
            self.assertIn("L2-Stage03", progress_events[7][2])
            self.assertIn("X1-X3", progress_events[8][2])

            # 2. Verify returned artifacts and disk persistence
            self.assertIn("01_transcript", result)
            self.assertIn("02_recap", result)
            self.assertIn("03_graph", result)
            self.assertTrue(any(out_dir.glob("01_*_hitl_resolved_transcript.md")))
            self.assertTrue(any(out_dir.glob("02_*_executive_recap.md")))
            self.assertTrue(any(out_dir.glob("03_*_canonical_signal_projection.json")))

    def test_progress_bar_test_runner_callback_e2e(self) -> None:
        """Verify ProgressBarTestRunner dispatches live per-test callbacks with percentage complete."""
        class DummyTests(unittest.TestCase):
            def test_alpha(self) -> None:
                self.assertTrue(True)

            def test_beta(self) -> None:
                self.assertEqual(1 + 1, 2)

            def test_gamma(self) -> None:
                self.assertIn("a", "abc")

        suite = unittest.TestLoader().loadTestsFromTestCase(DummyTests)
        self.assertEqual(suite.countTestCases(), 3)

        callback_records = []

        def on_test_progress(current: int, total: int, test_name: str, metrics: dict) -> None:
            callback_records.append((current, total, test_name, metrics))

        stream = io.StringIO()
        runner = ProgressBarTestRunner(
            stream=stream,
            verbosity=1,
            progress_callback=on_test_progress,
        )
        result = runner.run(suite)

        self.assertTrue(result.wasSuccessful())
        self.assertEqual(result.passed_count, 3)
        self.assertEqual(len(callback_records), 3)

        # First test: 1/3 (33.3%)
        self.assertEqual(callback_records[0][0], 1)
        self.assertEqual(callback_records[0][1], 3)
        self.assertEqual(callback_records[0][3]["percentage"], 33.3)
        self.assertEqual(callback_records[0][3]["passed"], 1)

        # Second test: 2/3 (66.7%)
        self.assertEqual(callback_records[1][0], 2)
        self.assertEqual(callback_records[1][1], 3)
        self.assertEqual(callback_records[1][3]["percentage"], 66.7)
        self.assertEqual(callback_records[1][3]["passed"], 2)

        # Third test: 3/3 (100.0%)
        self.assertEqual(callback_records[2][0], 3)
        self.assertEqual(callback_records[2][1], 3)
        self.assertEqual(callback_records[2][3]["percentage"], 100.0)
        self.assertEqual(callback_records[2][3]["passed"], 3)


if __name__ == "__main__":
    unittest.main()

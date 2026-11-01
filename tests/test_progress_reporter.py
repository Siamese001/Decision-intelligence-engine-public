"""
Tests for PipelineProgressReporter dual-mode progress indicator.
"""
from __future__ import annotations

import io
import os
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.transcript_pipeline_core.progress import PipelineProgressReporter


class TestPipelineProgressReporter(unittest.TestCase):
    """Tests dual-mode progress reporting, TTY/non-TTY handling, and stream safety."""

    def test_nontty_line_delimited_output(self) -> None:
        """In non-TTY mode, every progress update must end with \\n and not contain orphan \\r."""
        stream = io.StringIO()
        reporter = PipelineProgressReporter(
            total=4,
            desc="Test Pipeline",
            file=stream,
            force_mode="stream",
        )

        with reporter:
            reporter.notify(1, "U0: ingestion boundary")
            reporter.notify(2, "L1-plan: DIARIZED_TRANSCRIPT")
            reporter.notify(3, "L0: deterministic router")
            reporter.notify(4, "Complete")

        output = stream.getvalue()
        lines = [line for line in output.splitlines() if line.strip()]

        self.assertEqual(len(lines), 4)
        # Verify no carriage returns present in stream mode
        self.assertNotIn("\r", output)
        # Verify complete un-truncated stage descriptions
        self.assertIn("U0: ingestion boundary", lines[0])
        self.assertIn("[1/4]  25%", lines[0])
        self.assertIn("L1-plan: DIARIZED_TRANSCRIPT", lines[1])
        self.assertIn("[2/4]  50%", lines[1])
        self.assertIn("L0: deterministic router", lines[2])
        self.assertIn("[3/4]  75%", lines[2])
        self.assertIn("Complete", lines[3])
        self.assertIn("[4/4] 100%", lines[3])

    def test_nontty_trailing_newline_on_exception(self) -> None:
        """When an exception occurs inside context manager, stream must be cleanly flushed."""
        stream = io.StringIO()

        try:
            with PipelineProgressReporter(total=5, file=stream, force_mode="stream") as reporter:
                reporter.notify(1, "Stage 1")
                raise RuntimeError("Simulated failure during stage 2")
        except RuntimeError:
            pass

        output = stream.getvalue()
        self.assertTrue(output.endswith("\n"))
        self.assertIn("Stage 1", output)

    def test_progress_callback_invoked(self) -> None:
        """Progress callback should receive step index, total, and description."""
        callback_mock = MagicMock()
        stream = io.StringIO()

        with PipelineProgressReporter(
            total=3,
            file=stream,
            force_mode="stream",
            progress_callback=callback_mock,
        ) as reporter:
            reporter.notify(1, "Step One")
            reporter.notify(2, "Step Two")

        self.assertEqual(callback_mock.call_count, 2)
        callback_mock.assert_any_call(1, 3, "Step One")
        callback_mock.assert_any_call(2, 3, "Step Two")

    def test_safe_write_avoids_collision(self) -> None:
        """write() must append clean line without mangling active progress stream."""
        stream = io.StringIO()
        with PipelineProgressReporter(total=2, file=stream, force_mode="stream") as reporter:
            reporter.notify(1, "Stage 1")
            reporter.write("Info: external event happened")
            reporter.notify(2, "Stage 2")

        output = stream.getvalue()
        lines = [line for line in output.splitlines() if line.strip()]
        self.assertEqual(len(lines), 3)
        self.assertIn("Stage 1", lines[0])
        self.assertIn("Info: external event happened", lines[1])
        self.assertIn("Stage 2", lines[2])

    @patch.dict(os.environ, {"NEE_PROGRESS_MODE": "plain"})
    def test_env_var_forces_non_tty(self) -> None:
        """NEE_PROGRESS_MODE=plain should override isatty to non-TTY."""
        stream = MagicMock()
        stream.isatty.return_value = True  # Mock TTY stream
        reporter = PipelineProgressReporter(total=2, file=stream)
        self.assertFalse(reporter.is_tty)
        self.assertIsNone(reporter.pbar)

    @patch.dict(os.environ, {"NEE_PROGRESS_MODE": "plain"})
    def test_pipeline_integration_with_progress_capture(self) -> None:
        """RawTranscriptPipeline under NEE_PROGRESS_MODE=plain should emit readable lines."""
        import tempfile

        from dual_pipeline.transcript_pipeline import RawTranscriptPipeline

        pipeline = RawTranscriptPipeline(provider_type="offline")
        transcript = (
            "00:00:01 Amit Ayer: We have decided to migrate from pods to co-hydrated staffing.\n"
            "00:00:15 Yan Gobeil: We will update the delivery architecture accordingly.\n"
        )
        metadata = {
            "project": "test_project",
            "meeting_name": "Test_Operating_Model",
            "date": "09-17-2026",
            "time": "10:00AM - 11:00AM ET",
            "attendees": ["Amit Ayer (PartnerAdvisory)", "Yan Gobeil (PartnerAdvisory)"],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            captured_steps = []

            def _track(step, total, desc):
                captured_steps.append((step, total, desc))

            result = pipeline.run(
                transcript,
                metadata=metadata,
                output_dir=tmpdir,
                progress_callback=_track,
            )
            self.assertIsNotNone(result)
            self.assertGreaterEqual(len(captured_steps), 6)
            # Verify stage descriptions are untruncated
            descriptions = [d for _, _, d in captured_steps]
            self.assertTrue(any("ingestion boundary" in d for d in descriptions))
            self.assertTrue(any("deterministic router" in d for d in descriptions))


if __name__ == "__main__":
    unittest.main()


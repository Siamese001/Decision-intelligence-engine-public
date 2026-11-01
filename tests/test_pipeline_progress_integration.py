"""
Unit tests for PipelineProgressReporter and SSOT Pipeline Progress Integration.

Validates that:
1. PipelineProgressReporter operates in non-TTY mode with clean, discrete, flushed lines.
2. The heartbeat context manager emits periodic progress lines and terminates cleanly.
3. progress_callback is dispatched correctly on each stage transition.
"""
from __future__ import annotations

import io
import time
import unittest
from unittest.mock import MagicMock

from dual_pipeline.transcript_pipeline_core.progress import PipelineProgressReporter


class PipelineProgressIntegrationTests(unittest.TestCase):
    def test_non_tty_discrete_logging_and_flush(self) -> None:
        stream = io.StringIO()
        reporter = PipelineProgressReporter(
            total=10,
            desc="Pipeline Test",
            unit="stage",
            file=stream,
            force_mode="stream",
        )

        reporter.notify(1, "Stage 1: Initialization")
        reporter.notify(2, "Stage 2: Validation")
        reporter.write("Info message during run")
        reporter.notify(3, "Stage 3: Extraction")
        reporter.close()

        output = stream.getvalue()
        lines = [line for line in output.splitlines() if line.strip()]

        self.assertIn("[Pipeline Test] [1/10]  10%", lines[0])
        self.assertIn("Stage 1: Initialization", lines[0])
        self.assertIn("[Pipeline Test] [2/10]  20%", lines[1])
        self.assertIn("Stage 2: Validation", lines[1])
        self.assertIn("Info message during run", lines[2])
        self.assertIn("[Pipeline Test] [3/10]  30%", lines[3])
        self.assertIn("Stage 3: Extraction", lines[3])

    def test_heartbeat_context_manager(self) -> None:
        stream = io.StringIO()
        reporter = PipelineProgressReporter(
            total=5,
            desc="Heartbeat Test",
            file=stream,
            force_mode="stream",
        )

        with reporter.heartbeat("Active Model Inference", interval=0.03):
            time.sleep(0.08)

        reporter.close()
        output = stream.getvalue()

        self.assertIn("[Heartbeat Test]", output)
        self.assertIn("Active Model Inference", output)
        self.assertIn("actively working...", output)

    def test_progress_callback_dispatch(self) -> None:
        stream = io.StringIO()
        callback_mock = MagicMock()

        reporter = PipelineProgressReporter(
            total=4,
            desc="Callback Test",
            file=stream,
            force_mode="stream",
            progress_callback=callback_mock,
        )

        reporter.notify(1, "U0: Ingress")
        reporter.notify(2, "L1: Cognition")
        reporter.close()

        self.assertEqual(callback_mock.call_count, 2)
        callback_mock.assert_any_call(1, 4, "U0: Ingress")
        callback_mock.assert_any_call(2, 4, "L1: Cognition")


if __name__ == "__main__":
    unittest.main()

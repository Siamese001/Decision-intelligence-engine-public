"""
tests/test_pipeline_progress_fenced.py

Unit tests for in-band fenced progress reporting and heartbeat hook integration.
Verifies that:
1. PipelineProgressReporter in non-TTY mode defaults to 'fenced'.
2. Progress notifications and heartbeats emit valid markdown-fenced text blocks.
3. Zero carriage returns (\r) are emitted in fenced mode to prevent log smearing.
4. Heartbeat hooks integrate cleanly with config.transport without upward imports.
"""
from __future__ import annotations

import io
import unittest

from config.transport import _HEARTBEAT_HOOK, set_heartbeat_hook
from dual_pipeline.transcript_pipeline_core.progress import PipelineProgressReporter


class TestPipelineProgressFenced(unittest.TestCase):
    def tearDown(self) -> None:
        set_heartbeat_hook(None)

    def test_default_mode_fenced_when_non_tty(self) -> None:
        buf = io.StringIO()
        reporter = PipelineProgressReporter(total=9, file=buf)
        self.assertEqual(reporter.mode, "fenced")
        self.assertFalse(reporter.is_tty)
        self.assertIsNone(reporter.pbar)

    def test_notify_emits_fenced_text_block(self) -> None:
        buf = io.StringIO()
        with PipelineProgressReporter(total=9, file=buf, force_mode="fenced") as reporter:
            reporter.notify(1, "U0: Raw Ingestion")
            output = buf.getvalue()

        self.assertIn("```text", output)
        self.assertIn("```\n", output)
        self.assertIn("[Pipeline Progress] [1/9]", output)
        self.assertIn("Active Stage: U0: Raw Ingestion", output)
        self.assertNotIn("\r", output)

    def test_heartbeat_emits_fenced_heartbeat_block(self) -> None:
        buf = io.StringIO()
        with PipelineProgressReporter(total=9, file=buf, force_mode="fenced") as reporter:
            reporter.heartbeat(45.2, "L2-Stage01 Forensic Normalization")
            output = buf.getvalue()

        self.assertIn("```text", output)
        self.assertIn("```\n", output)
        self.assertIn("Stage Running: L2-Stage01 Forensic Normalization (heartbeat: 00:45)", output)
        self.assertNotIn("\r", output)

    def test_transport_heartbeat_hook_integration(self) -> None:
        buf = io.StringIO()
        reporter = PipelineProgressReporter(total=9, file=buf, force_mode="fenced")
        self.assertIsNone(_HEARTBEAT_HOOK)

        with reporter:
            from config.transport import _HEARTBEAT_HOOK as active_hook
            self.assertIsNotNone(active_hook)
            # Invoke the active hook as execute_with_deadline_and_heartbeat would
            active_hook(30.0, "LLM Test Call")
            output = buf.getvalue()
            self.assertIn("Stage Running: LLM Test Call (heartbeat: 00:30)", output)

        from config.transport import _HEARTBEAT_HOOK as closed_hook
        self.assertIsNone(closed_hook)

    def test_plain_mode_emits_line_delimited_output(self) -> None:
        buf = io.StringIO()
        with PipelineProgressReporter(total=9, file=buf, force_mode="plain") as reporter:
            reporter.notify(3, "C0: Conformance Gate")
            output = buf.getvalue()

        self.assertNotIn("```", output)
        self.assertIn("[Pipeline Progress] [3/9]", output)
        self.assertIn("C0: Conformance Gate\n", output)
        self.assertNotIn("\r", output)


if __name__ == "__main__":
    unittest.main()

"""
tests/test_cli_fail_closed_on_missing_provider.py

Wave 1 Verification: Asserts that production CLI ingestion entrypoints fail closed,
purge '--provider offline' from public execution, default strictly to live inference,
and eliminate '--offline-external-l2' and '--offline-ingestion-reasoning'.
"""
import contextlib
import os
import unittest
from unittest.mock import MagicMock

from dual_pipeline.cli.ingest import ingest_canonical
from dual_pipeline.cli.parser import build_parser


@contextlib.contextmanager
def _isolated_production_env():
    keys = ["PYTEST_CURRENT_TEST", "NEE_ALLOW_TEST_FIXTURES", "RUNNING_TESTS_WITH_PROGRESS", "NEE_TEST_MODE"]
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


class TestCliFailClosedOnMissingProvider(unittest.TestCase):
    """Test suite asserting fail-closed behavior for CLI provider arguments."""

    def test_cli_rejects_offline_provider_in_production_standalone(self):
        """Verify that in production (outside pytest fixture override), '--provider offline' is rejected by parser."""
        with _isolated_production_env():
            parser = build_parser()
            with self.assertRaises(SystemExit) as exc_info:
                parser.parse_args([
                    "ingest-meeting",
                    "--input", "test.txt",
                    "--provider", "offline",
                ])
            # GovernedArgumentParser exits with code 1 on usage errors
            self.assertEqual(exc_info.exception.code, 1)

    def test_cli_defaults_to_live_llm(self):
        """Verify that omitting --provider defaults strictly to 'llm', never 'offline'."""
        with _isolated_production_env():
            parser = build_parser()
            args = parser.parse_args([
                "ingest-meeting",
                "--input", "test.txt",
            ])
            self.assertEqual(args.provider, "llm")

    def test_cli_purged_offline_reasoning_and_l2_flags(self):
        """Verify that --offline-external-l2 and --offline-ingestion-reasoning are purged from parser."""
        import io
        with _isolated_production_env():
            parser = build_parser()

            # 1. Verify --offline-ingestion-reasoning is rejected on ingest-meeting with exact reason
            stderr_buf = io.StringIO()
            with contextlib.redirect_stderr(stderr_buf):
                with self.assertRaises(SystemExit) as exc_info:
                    parser.parse_args([
                        "ingest-meeting",
                        "--input", "test.txt",
                        "--offline-ingestion-reasoning",
                    ])
            self.assertEqual(exc_info.exception.code, 1)
            self.assertIn("unrecognized arguments", stderr_buf.getvalue())
            self.assertIn("--offline-ingestion-reasoning", stderr_buf.getvalue())

            # 2. Verify --offline-external-l2 is rejected on ask with valid --question
            stderr_buf = io.StringIO()
            with contextlib.redirect_stderr(stderr_buf):
                with self.assertRaises(SystemExit) as exc_info:
                    parser.parse_args([
                        "ask",
                        "--question", "What is the architecture?",
                        "--offline-external-l2",
                    ])
            self.assertEqual(exc_info.exception.code, 1)
            self.assertIn("unrecognized arguments", stderr_buf.getvalue())
            self.assertIn("--offline-external-l2", stderr_buf.getvalue())

            # 3. Verify --offline-external-l2 is rejected on demo
            stderr_buf = io.StringIO()
            with contextlib.redirect_stderr(stderr_buf):
                with self.assertRaises(SystemExit) as exc_info:
                    parser.parse_args([
                        "demo",
                        "--package", "dummy_package.json",
                        "--offline-external-l2",
                    ])
            self.assertEqual(exc_info.exception.code, 1)
            self.assertIn("unrecognized arguments", stderr_buf.getvalue())
            self.assertIn("--offline-external-l2", stderr_buf.getvalue())

    def test_ingest_meeting_fails_closed_when_offline_attempted(self):
        """Verify that if an offline provider is somehow injected into args, ingest_canonical fails closed in production."""
        with _isolated_production_env():
            mock_args = MagicMock()
            mock_args.input = "test.txt"
            mock_args.source = None
            mock_args.metadata = None
            mock_args.bypass_speaker_hitl = False
            mock_args.provider = "offline"
            mock_args.model = None

            with self.assertRaises(ValueError) as exc_info:
                ingest_canonical(mock_args)
            self.assertIn("FAIL_CLOSED", str(exc_info.exception))
            self.assertIn("--provider offline", str(exc_info.exception))


if __name__ == "__main__":
    unittest.main()

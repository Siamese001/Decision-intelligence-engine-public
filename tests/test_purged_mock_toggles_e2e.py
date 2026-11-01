"""
tests/test_purged_mock_toggles_e2e.py

End-to-end lifecycle verification that mock toggles and silent bypasses
remain strictly purged across the canonical pipeline entrypoints.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestPurgedMockTogglesE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temp_dir.name)
        self.sample_txt = self.output_dir / "sample_meeting.txt"
        self.sample_txt.write_text(
            "Alice: We are reviewing the Q3 architecture.\nBob: Agreed, let's proceed.",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run_cli(self, args: list[str], env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT)
        # Remove any test fixture override flags
        env.pop("PYTEST_CURRENT_TEST", None)
        env.pop("NEE_ALLOW_TEST_FIXTURES", None)
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(
            [sys.executable, "-m", "dual_pipeline.cli", *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            env=env,
        )

    def test_e2e_cli_rejects_provider_offline_in_production(self) -> None:
        """Verify that nee ingest-meeting strictly rejects --provider offline in production CLI."""
        res = self._run_cli([
            "ingest-meeting",
            "--input", str(self.sample_txt),
            "--project", "project_nexus",
            "--meeting", "ArchReview",
            "--date", "09/26/2026",
            "--provider", "offline",
        ])
        self.assertNotEqual(res.returncode, 0)
        combined_output = res.stdout + res.stderr
        self.assertTrue(
            "invalid choice: 'offline'" in combined_output
            or "FAIL_CLOSED_NO_MOCK_IN_PRODUCTION" in combined_output
        )

    def test_e2e_cli_rejects_deleted_mock_flag(self) -> None:
        """Verify that --mock is unrecognized across all public CLI subcommands."""
        res = self._run_cli([
            "ingest-meeting",
            "--input", str(self.sample_txt),
            "--project", "project_nexus",
            "--meeting", "ArchReview",
            "--date", "09/26/2026",
            "--mock",
        ])
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("unrecognized arguments: --mock", res.stderr)

    def test_e2e_cli_rejects_deleted_allow_unjudged_flag(self) -> None:
        """Verify that --allow-unjudged-package is unrecognized across all public CLI subcommands."""
        res = self._run_cli([
            "intake-publish",
            "--package", str(self.sample_txt),
            "--allow-unjudged-package",
        ])
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("unrecognized arguments: --allow-unjudged-package", res.stderr)

    def test_e2e_cli_rejects_deleted_offline_reasoning_flag(self) -> None:
        """Verify that --offline-ingestion-reasoning is unrecognized across all public CLI subcommands."""
        res = self._run_cli([
            "ingest-meeting",
            "--input", str(self.sample_txt),
            "--project", "project_nexus",
            "--meeting", "ArchReview",
            "--date", "09/26/2026",
            "--offline-ingestion-reasoning",
        ])
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("unrecognized arguments: --offline-ingestion-reasoning", res.stderr)

    def test_e2e_pipeline_requires_live_credentials(self) -> None:
        """Verify that running ingest-meeting without LLM credentials halts gracefully with credential failure."""
        res = self._run_cli(
            [
                "ingest-meeting",
                "--input", str(self.sample_txt),
                "--project", "project_nexus",
                "--meeting", "ArchReview",
                "--date", "09/26/2026",
                "--provider", "llm",
                "--output-dir", str(self.output_dir / "out"),
            ],
            env_overrides={
                "SSOT_ENV_NO_OVERRIDE": "1",
                "OPENAI_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
                "GEMINI_API_KEY": "",
            },
        )
        self.assertNotEqual(res.returncode, 0)
        combined_output = res.stdout + res.stderr
        # Must fail because API keys are missing, never silently succeed via mock fallback
        self.assertTrue(
            "API key" in combined_output
            or "credential" in combined_output.lower()
            or "Authentication" in combined_output
            or "FAIL_CLOSED" in combined_output
            or "RuntimeError" in combined_output
            or "ValueError" in combined_output
        )


if __name__ == "__main__":
    unittest.main()

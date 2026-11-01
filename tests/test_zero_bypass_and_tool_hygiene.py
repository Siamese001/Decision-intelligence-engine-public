"""Unit tests for Zero Bypass and Tool Hygiene (Wave 4).

Verifies:
1. Every bypass key (bypass_speaker_hitl, bypass_hitl, etc.) fails closed with ValueError across CanonicalAgenticPipeline.
2. Prompts in governance and config directories contain zero bypass instructions.
3. Metadata JSON inputs contain zero bypass keys.
4. LiveUISynthesizer in tools/run_lens_synthesis_with_live_ui.py delegates to CanonicalAgenticPipeline.
5. TranscriptIngestionPipelineRunner is marked deprecated.
6. tools/lint_u0_hitl_invariants.py exits with code 0.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from unittest.mock import patch

from dual_pipeline.canonical_pipeline import CanonicalAgenticPipeline
from dual_pipeline.core import ROOT
from tools.lint_u0_hitl_invariants import (
    FORBIDDEN_BYPASS_KEYS,
    lint_metadata_file,
    lint_prompt_file,
)
from tools.run_lens_synthesis_with_live_ui import LiveUISynthesizer


class TestZeroBypassAndToolHygiene(unittest.TestCase):
    """Enforces zero tolerance for synthetic bypass keys and ensures unified runner hygiene."""

    def test_all_forbidden_bypass_keys_fail_closed_in_canonical_pipeline(self) -> None:
        """Any metadata declaring a forbidden bypass key must fail closed with ValueError."""
        pipeline = CanonicalAgenticPipeline(provider_type="offline")
        for key in FORBIDDEN_BYPASS_KEYS:
            with self.subTest(key=key):
                with self.assertRaises(ValueError) as ctx:
                    pipeline.run("dummy.txt", metadata={key: True})
                self.assertIn("HITL cannot be removed, disabled, or bypassed", str(ctx.exception))

    def test_prompts_contain_zero_bypass_tokens(self) -> None:
        """Every markdown prompt file in governance/ and config/ must contain zero bypass instructions."""
        prompt_dirs = [ROOT / "governance" / "decision_intelligence", ROOT / "config" / "prompts"]
        violations = []
        for pdir in prompt_dirs:
            if pdir.is_dir():
                for pf in pdir.rglob("*.md"):
                    violations.extend(lint_prompt_file(pf))
        self.assertEqual(violations, [], f"Found bypass instructions in prompt files: {violations}")

    def test_input_metadata_files_contain_zero_bypass_keys(self) -> None:
        """Every input metadata file in inputs/transcripts/ must contain zero bypass keys."""
        input_dir = ROOT / "inputs" / "transcripts"
        violations = []
        if input_dir.is_dir():
            for mf in input_dir.glob("*.json"):
                violations.extend(lint_metadata_file(mf))
        self.assertEqual(violations, [], f"Found bypass keys in metadata inputs: {violations}")

    def test_live_ui_synthesizer_delegates_to_canonical_pipeline(self) -> None:
        """LiveUISynthesizer must instantiate and run CanonicalAgenticPipeline."""
        with patch.object(CanonicalAgenticPipeline, "run") as mock_run:
            mock_run.return_value = {
                "status": "PASS",
                "artifacts": {"01_transcript": "out1.md", "02_recap": "out2.md", "03_graph": "out3.json"},
                "intake_package": "pkg.json",
                "execution_receipt": "rcpt.json",
            }
            synthesizer = LiveUISynthesizer(
                meeting_dir=ROOT / "output" / "project_lens" / "09-18-2026" / "Jake_Amit_Miles_PartnerAdvisory",
                model="gpt-5.6-sol",
                fenced=True,
            )
            result = synthesizer.execute()
            self.assertEqual(result["status"], "PASS")
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args[1]
            self.assertEqual(call_kwargs["judge_model"], "gpt-5.6-sol")
            self.assertIsNotNone(call_kwargs.get("progress_callback"))

    def test_pipeline_runner_deprecation_docstring(self) -> None:
        """TranscriptIngestionPipelineRunner module must declare deprecation status."""
        import dual_pipeline.pipeline_runner as pr
        self.assertIn("DEPRECATED", pr.__doc__)
        self.assertIn("CanonicalAgenticPipeline", pr.__doc__)

    def test_lint_u0_hitl_invariants_script_passes(self) -> None:
        """tools/lint_u0_hitl_invariants.py must exit with code 0 across the codebase."""
        cmd = [sys.executable, str(ROOT / "tools" / "lint_u0_hitl_invariants.py")]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(
            proc.returncode,
            0,
            f"tools/lint_u0_hitl_invariants.py failed with output:\n{proc.stdout}\n{proc.stderr}",
        )


if __name__ == "__main__":
    unittest.main()

"""
tests/test_agentic_pipeline_single_entrypoint.py

Verifies that all pipeline execution paths and CLI entrypoints share the exact
same canonical prompt SSOT, duration resolution, and agentic framework:
U0 -> L1 -> L0 -> C0 -> PA -> L2 -> X1-X3 (exit) -> evals.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.prompts.registry import CANONICAL_PROMPT_REGISTRY
from dual_pipeline.transcript_metadata_helper import scaffold_meeting_metadata
from dual_pipeline.transcript_stages_core.constants import _load_stage_prompt


class TestAgenticPipelineSingleEntrypoint(unittest.TestCase):
    def test_recap_prompt_is_unified_ssot(self) -> None:
        """
        Verify that _load_stage_prompt('recap') and CANONICAL_PROMPT_REGISTRY.get_prompt('STAGE_02_RECAP')
        point to the exact same unified prompt content containing the mandatory quality floors.
        """
        prompt_from_loader = _load_stage_prompt("recap")
        prompt_from_registry = CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_02_RECAP")
        self.assertEqual(prompt_from_loader, prompt_from_registry)

        # Must enforce the canonical quality bar
        self.assertIn("Archetype-Adaptive Rigor", prompt_from_loader)
        self.assertIn("Duration-Scaled Core Narrative Prose Targets & Ceilings", prompt_from_loader)
        self.assertIn("Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown", prompt_from_loader)
        self.assertIn("500–850 WORDS PER DRIVER", prompt_from_loader)
        self.assertIn("Client Sentiment & Friction Preservation", prompt_from_loader)
        self.assertIn("Zero-Context Comprehensibility & Logical Narrative Arc", prompt_from_loader)

    def test_scaffold_meeting_metadata_resolves_duration_automatically(self) -> None:
        """Verify that scaffold_meeting_metadata computes duration_minutes from time_str or timestamps."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("""
            **harper Hoffman:** [00:00] First turn.
            **Amit Ayer:** [26:56] Concluding discussion.
            """)
            f.flush()
            temp_path = Path(f.name)

        try:
            meta = scaffold_meeting_metadata(
                input_path=temp_path,
                time_str="04:32 PM - 04:59 PM ET",
                project="project_lens",
                meeting_name="Project LENS Phase II Catchup",
            )
            self.assertEqual(meta.get("duration_minutes"), 27)
            self.assertEqual(meta.get("meeting_duration_minutes"), 27)
            self.assertIn("Duration: ~27m", meta.get("time", ""))
        finally:
            temp_path.unlink(missing_ok=True)

    def test_transcript_cli_enriches_metadata(self) -> None:
        """Verify that dual_pipeline/transcript_cli.py enriches metadata using scaffold_meeting_metadata."""
        from dual_pipeline.transcript_cli import main

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("**Alice Smith:** [00:00] Hi.\n**Bob Jones:** [15:00] Bye.\n")
            f.flush()
            src_path = Path(f.name)

        try:
            with patch("dual_pipeline.transcript_cli.CanonicalAgenticPipeline") as mock_pipeline_cls:
                mock_inst = MagicMock()
                mock_inst.run.return_value = {"artifacts": {}}
                mock_pipeline_cls.return_value = mock_inst

                with tempfile.TemporaryDirectory() as out_dir:
                    exit_code = main(["--source", str(src_path), "--output-dir", out_dir])
                    self.assertEqual(exit_code, 0)
                    mock_inst.run.assert_called_once()
                    _, called_meta, _ = mock_inst.run.call_args[0]
                    # Verify metadata contains duration and scaffolding
                    self.assertEqual(called_meta.get("duration_minutes"), 15)
        finally:
            src_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()

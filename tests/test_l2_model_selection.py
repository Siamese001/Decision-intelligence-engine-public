"""Unit tests for L2 multi-model selection and synthesizer delegation.

Verifies:
1. LLMTranscriptSynthesizer initialization and per-stage model bindings.
2. D01 defaults to gpt-5.6-luna (80% cost reduction vs gpt-5.6-sol).
3. D02, D03, Agentic default to gemini-3.8-flash (98% cost reduction).
4. Explicit model overrides are respected per stage.
5. Synthesizer routing switches cleanly between OpenAI and Google backends.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.transcript_stages_core.synthesizers import LLMTranscriptSynthesizer  # noqa: E402


class TestL2ModelSelection(unittest.TestCase):
    """Test suite for stage-specific model selection and synthesis routing."""

    def _make_synth(self, **kwargs) -> LLMTranscriptSynthesizer:
        from config.credentials import OpenAICredentials
        stub_creds = OpenAICredentials(
            api_key="test-key",
            is_mock=False,
            model=kwargs.get("model", "gpt-5.6-luna"),
            reasoning_effort="high",
            masked_key="[TEST_STUB]",
            source="test_injection",
        )
        return LLMTranscriptSynthesizer(_client=MagicMock(), _creds=stub_creds, **kwargs)

    def test_default_stage_model_bindings(self) -> None:
        """Verify default stage assignments when no explicit models are passed."""
        synth = self._make_synth()
        self.assertEqual(synth.d01_model, "gpt-5.6-luna")
        self.assertEqual(synth.d02_model, "gemini-3.8-flash")
        self.assertEqual(synth.d03_model, "gemini-3.8-flash")
        self.assertEqual(synth.agentic_model, "gemini-3.8-flash")

    def test_explicit_stage_model_overrides(self) -> None:
        """Verify individual stage models can be explicitly overridden."""
        synth = self._make_synth(
            model="gpt-5.6-sol",
            d01_model="gpt-6-luna",
            d02_model="gpt-5.6-luna",
            d03_model="gemini-3.8-flash",
            agentic_model="gemini-3.5-flash-lite",
        )
        self.assertEqual(synth.d01_model, "gpt-6-luna")
        self.assertEqual(synth.d02_model, "gpt-5.6-luna")
        self.assertEqual(synth.d03_model, "gemini-3.8-flash")
        self.assertEqual(synth.agentic_model, "gemini-3.5-flash-lite")

    @patch("dual_pipeline.transcript_stages_core.recap_modular_dag.call_gemini_generate_content")
    def test_recap_routes_to_gemini_when_model_is_gemini(
        self, mock_call_gemini: MagicMock
    ) -> None:
        """Verify synthesize_recap delegates to Google when d02_model is gemini-*."""
        mock_call_gemini.return_value = (
            "# Executive Recap\n\n"
            + ("Meeting narrative detailed content " * 220)
            + "\n\n| Item | Owner |\n| --- | --- |\n| Task | Amit |\n"
        )

        synth = self._make_synth(d02_model="gemini-3.8-flash")
        result = synth.synthesize_recap("System prompt", "User transcript")

        self.assertIn("Executive Recap", result)
        self.assertGreaterEqual(mock_call_gemini.call_count, 1)
        for c in mock_call_gemini.call_args_list:
            c_kwargs = c[1] if c[1] else {}
            c_args = c[0]
            model_passed = c_kwargs.get("model", c_args[0] if c_args else "")
            self.assertEqual(model_passed, "gemini-3.8-flash")

    @patch("dual_pipeline.transcript_stages_core.external_synthesizer.call_gemini_generate_content")
    def test_graph_routes_to_gemini_with_json_mode(
        self, mock_call_gemini: MagicMock
    ) -> None:
        """Verify synthesize_graph delegates to Google with json_mode=True."""
        mock_call_gemini.return_value = '{"nodes": [], "edges": []}'

        synth = self._make_synth(d03_model="gemini-3.8-flash")
        result = synth.synthesize_graph("System prompt", "User transcript")

        self.assertEqual(result, '{"nodes": [], "edges": []}')
        mock_call_gemini.assert_called_once()
        self.assertTrue(mock_call_gemini.call_args[1].get("json_mode", False))

    @patch("dual_pipeline.transcript_stages_core.external_synthesizer.call_gemini_generate_content")
    def test_agentic_leverage_routes_to_gemini(
        self, mock_call_gemini: MagicMock
    ) -> None:
        """Verify synthesize_agentic_leverage delegates to Google with json_mode=True."""
        mock_call_gemini.return_value = '{"leverage_nodes": []}'

        synth = self._make_synth(agentic_model="gemini-3.8-flash")
        nodes = synth.synthesize_agentic_leverage("System prompt", "User transcript")

        self.assertEqual(nodes, [])
        mock_call_gemini.assert_called_once()


if __name__ == "__main__":
    unittest.main()

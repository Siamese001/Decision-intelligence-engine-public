"""Unit tests for query_router_core.project_router covering all code paths and edge cases."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.query_router_core.project_router import determine_project_directory


class DynamicRouterTests(unittest.TestCase):
    """Test suite for determine_project_directory."""

    def test_explicit_valid_project_in_metadata_bypasses_llm(self) -> None:
        """When metadata['project'] is already in KNOWN_PROJECTS, return it immediately."""
        metadata = {
            "project": "project_nexus",
            "meeting_name": "Nexus Architecture Review",
        }
        # Even with no API key, explicit metadata should return the project
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-key"}, clear=False):
            with patch("openai.OpenAI") as mock_openai:
                result = determine_project_directory(metadata)
                self.assertEqual(result, "project_nexus")
                mock_openai.assert_not_called()

    def test_missing_api_key_returns_none(self) -> None:
        """When OPENAI_API_KEY is not set and project is not in metadata, return None."""
        metadata = {"meeting_name": "Unknown Strategic Meeting"}
        with patch.dict(os.environ, {}, clear=True):
            result = determine_project_directory(metadata)
            self.assertIsNone(result)

    def test_llm_selects_valid_known_project(self) -> None:
        """When LLM returns a valid project from KNOWN_PROJECTS, return it."""
        metadata = {
            "meeting_name": "Cloud Strategy Infrastructure Review",
            "audiences": ["nee-engineering"],
        }
        mock_completion = MagicMock()
        mock_completion.choices = [
            MagicMock(message=MagicMock(content=json.dumps({"project": "enterprise_architecture"})))
        ]

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with patch("openai.OpenAI") as mock_openai_cls:
                mock_client = MagicMock()
                mock_openai_cls.return_value = mock_client
                mock_client.chat.completions.create.return_value = mock_completion

                result = determine_project_directory(
                    metadata,
                    transcript_snippet="Discussing EA standards and governance...",
                    client=mock_client,
                )
                self.assertEqual(result, "enterprise_architecture")
                mock_client.chat.completions.create.assert_called_once()
                call_kwargs = mock_client.chat.completions.create.call_args[1]
                self.assertEqual(call_kwargs["model"], "gpt-6-luna")
                self.assertEqual(call_kwargs["reasoning_effort"], "low")

    def test_llm_returns_invalid_project_fails_closed(self) -> None:
        """When LLM returns an unknown project not in KNOWN_PROJECTS, fail-closed to None."""
        metadata = {"meeting_name": "Some Random Meeting"}
        mock_completion = MagicMock()
        mock_completion.choices = [
            MagicMock(message=MagicMock(content=json.dumps({"project": "completely_invented_project"})))
        ]

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with patch("openai.OpenAI") as mock_openai_cls:
                mock_client = MagicMock()
                mock_openai_cls.return_value = mock_client
                mock_client.chat.completions.create.return_value = mock_completion

                result = determine_project_directory(metadata, client=mock_client)
                self.assertIsNone(result)

    def test_llm_exception_is_handled_gracefully(self) -> None:
        """When an exception occurs during the LLM call, catch and return None."""
        metadata = {"meeting_name": "Failing Call"}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with patch("openai.OpenAI") as mock_openai_cls:
                mock_client = MagicMock()
                mock_openai_cls.return_value = mock_client
                mock_client.chat.completions.create.side_effect = RuntimeError("API connection timeout")

                result = determine_project_directory(metadata, client=mock_client)
                self.assertIsNone(result)

    def test_snippet_is_truncated_to_1000_chars(self) -> None:
        """Snippet passed to LLM is safely truncated to first 1000 characters."""
        metadata = {"meeting_name": "Long Meeting"}
        long_snippet = "x" * 2500
        mock_completion = MagicMock()
        mock_completion.choices = [
            MagicMock(message=MagicMock(content=json.dumps({"project": "regai"})))
        ]

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with patch("openai.OpenAI") as mock_openai_cls:
                mock_client = MagicMock()
                mock_openai_cls.return_value = mock_client
                mock_client.chat.completions.create.return_value = mock_completion

                result = determine_project_directory(
                    metadata,
                    transcript_snippet=long_snippet,
                    client=mock_client,
                )
                self.assertEqual(result, "regai")
                call_args = mock_client.chat.completions.create.call_args[1]
                user_msg = call_args["messages"][1]["content"]
                self.assertIn("Snippet:\n" + ("x" * 1000), user_msg)
                self.assertNotIn("x" * 1001, user_msg)

    def test_deterministic_prefix_resolution_bypasses_llm(self) -> None:
        """Meeting titles with explicit project prefixes route deterministically without LLM."""
        test_cases = [
            ("NEA Agentic Governance & AI Optimization Architecture Review", "nea"),
            ("NEA: Candidate Screening Calibration", "nea"),
            ("NEA_Chakra_Operating_Model", "nea"),
            ("Project Lens Phase 2 SOW", "project_lens"),
            ("Project Beacon Sprint 4 Architecture", "project_beacon"),
            ("Project Atlas Planning", "project_atlas"),
            ("APEX_TRANS Tech Services Group Interview", "apex_trans"),
            ("Conduit Platform Review", "conduit"),
            ("EA AI Policy Strategy", "enterprise_architecture"),
            ("Enterprise Architecture Sync", "enterprise_architecture"),
        ]
        with patch("openai.OpenAI") as mock_openai:
            for title, expected_proj in test_cases:
                meta = {"meeting_name": title}
                res = determine_project_directory(meta)
                self.assertEqual(res, expected_proj, f"Failed for title: '{title}'")
            mock_openai.assert_not_called()

    def test_dynamic_router_shim_purged(self) -> None:
        """Verify that the deprecated dual_pipeline.dynamic_router facade has been purged."""
        import importlib
        with self.assertRaises(ImportError):
            importlib.import_module("dual_pipeline.dynamic_router")


if __name__ == "__main__":
    unittest.main()

"""
tests/test_copilot_offline_graph_adversarial.py

Adversarial and boundary test suite for Wave 1 copilot offline graph query mode.
Covers:
1. Boundary payload: Empty strings, zero-length request, whitespace-only topics.
2. Malformed input: Non-existent engagement IDs, invalid intent combinations.
3. Invariant bypass: Bypassing active meeting checks with conflicting flags.
"""
from __future__ import annotations

import argparse
import tempfile
import unittest

from dual_pipeline.cli.copilot_handlers import handle_copilot


class TestCopilotOfflineGraphAdversarial(unittest.TestCase):
    """Adversarial and edge-case test suite for copilot offline mode."""

    def test_boundary_empty_and_whitespace_request(self) -> None:
        """Category 1: Boundary payload - Whitespace and empty request strings."""
        args = argparse.Namespace(
            project="project_lens",
            intent="gaps",
            request="   ",
            topic="",
            json=True,
        )
        res = handle_copilot(args)
        # Should gracefully evaluate gaps for project scope without crashing
        self.assertEqual(res.get("status"), "PASS")
        self.assertEqual(res.get("context_mode"), "GRAPH_ONLY")

    def test_malformed_nonexistent_project_scope(self) -> None:
        """Category 2: Malformed input - Non-existent project slug in graph."""
        args = argparse.Namespace(
            project="project_fictional_nonexistent_9999",
            intent="gaps",
            request="open architectural decisions",
            json=True,
        )
        res = handle_copilot(args)
        # Gracefully fails or returns zero matching assertions, never crashes
        self.assertIn(res.get("status"), ("PASS", "FAIL"))
        if res.get("status") == "FAIL":
            self.assertIn(res.get("error"), ("ZERO_MATCHING_ASSERTIONS", "SCOPE_FILTERED", "PARSER_NO_TOPIC"))

    def test_invariant_bypass_live_with_no_session(self) -> None:
        """Category 3: Invariant bypass - Declaring --active when no daemon is active."""
        args = argparse.Namespace(
            intent="next-questions",
            active=True,
            json=True,
        )
        res = handle_copilot(args)
        # Must fail closed with NO_SESSION_FOUND, blocking bypass
        self.assertEqual(res.get("status"), "FAIL")
        self.assertEqual(res.get("error"), "NO_SESSION_FOUND")

    def test_boundary_missing_target_for_respond_intent(self) -> None:
        """Category 1: Boundary payload - Missing target speaker for respond intent."""
        args = argparse.Namespace(
            intent="respond",
            target="",
            project="project_lens",
            json=True,
        )
        res = handle_copilot(args)
        self.assertEqual(res.get("status"), "FAIL")
        self.assertEqual(res.get("error"), "MISSING_TARGET")


if __name__ == "__main__":
    unittest.main()

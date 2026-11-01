"""
tests/end_to_end/test_copilot_offline_graph_e2e.py

End-to-End verification of Wave 1: Copilot offline graph query mode.
Verifies that graph-centric intents (gaps, graph-context) and queries with
explicit project scope can evaluate directly against the active knowledge graph
without requiring a live audio session or live transcript turns.
"""
from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.cli.copilot_handlers import handle_copilot


class TestCopilotOfflineGraphE2E(unittest.TestCase):
    """E2E lifecycle suite for offline graph-grounded copilot queries."""

    def test_offline_gaps_intent_succeeds_without_live_session(self) -> None:
        """Verifies --intent gaps with project scope executes offline and cites assertions."""
        args = argparse.Namespace(
            project="project_lens",
            intent="gaps",
            request="provide open design decisions to close out LENS",
            json=True,
        )
        res = handle_copilot(args)
        self.assertEqual(res.get("status"), "PASS", f"Copilot offline gaps failed: {res}")
        self.assertEqual(res.get("command"), "copilot")
        self.assertEqual(res.get("intent"), "gaps")
        self.assertEqual(res.get("context_mode"), "GRAPH_ONLY")
        cited_ids = res.get("cited_assertion_ids", [])
        self.assertGreaterEqual(len(cited_ids), 1, "Expected >=1 cited assertion in offline gaps mode")

    def test_offline_graph_context_intent_succeeds(self) -> None:
        """Verifies --intent graph-context with topic query returns grounded assertions."""
        args = argparse.Namespace(
            project="project_lens",
            intent="graph-context",
            topic="LENS",
            request="what is the scope of project LENS",
            json=True,
        )
        res = handle_copilot(args)
        self.assertEqual(res.get("status"), "PASS", f"Copilot offline graph-context failed: {res}")
        self.assertEqual(res.get("context_mode"), "GRAPH_ONLY")
        cited_ids = res.get("cited_assertion_ids", [])
        self.assertGreaterEqual(len(cited_ids), 1, "Expected >=1 cited assertion in graph-context")

    def test_live_prompter_nbq_still_fails_without_live_context(self) -> None:
        """Verifies that live-only intents (nbq) still fail closed with NO_LIVE_CONTEXT on empty dirs."""
        with tempfile.TemporaryDirectory() as td:
            args = argparse.Namespace(
                intent="nbq",
                meeting_dir=td,
                json=True,
            )
            res = handle_copilot(args)
            self.assertEqual(res.get("status"), "FAIL")
            self.assertEqual(res.get("error"), "NO_LIVE_CONTEXT")


if __name__ == "__main__":
    unittest.main()

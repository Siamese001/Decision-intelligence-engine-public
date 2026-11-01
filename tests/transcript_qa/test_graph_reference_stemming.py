#!/usr/bin/env python3
"""Unit tests for plural/singular stemming and category id resolution in graph reference validator."""
from __future__ import annotations

import unittest
from dual_pipeline.transcript_qa_core.graph_reference_validator import (
    _extract_tokens,
    check_semantic_evidence_entailment,
)


class TestGraphReferenceStemming(unittest.TestCase):
    """Verifies that simple plurals and singular tokens match across proposal and evidence pairs."""

    def test_extract_tokens_includes_singular_stem(self) -> None:
        tokens = _extract_tokens("systems alerts cameras feeds")
        self.assertIn("systems", tokens)
        self.assertIn("system", tokens)
        self.assertIn("alerts", tokens)
        self.assertIn("alert", tokens)
        self.assertIn("cameras", tokens)
        self.assertIn("camera", tokens)
        self.assertIn("feeds", tokens)
        self.assertIn("feed", tokens)

    def test_semantic_entailment_matches_singular_to_plural(self) -> None:
        data = {
            "evidence_index": {
                "ev-test-01": {
                    "text": "The camera in the substation detected smoke immediately.",
                    "exact_text": "The camera in the substation detected smoke.",
                }
            },
            "graph_mutations": {
                "proposal_updates": [
                    {
                        "proposal_id": "proposal:test-smoke-detection",
                        "description": "Deploy AI optical cameras across high-risk corridors.",
                        "rationale": "Improve early wildfire detection.",
                        "evidence_ids": ["ev-test-01"],
                    }
                ]
            },
        }
        passed, failures = check_semantic_evidence_entailment(data)
        self.assertTrue(passed, f"Expected entailment to pass but got: {failures}")
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()

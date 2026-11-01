#!/usr/bin/env python3
"""Focused contract checks for the Sherpa semantic-correction package."""
from __future__ import annotations

import copy
import unittest
from pathlib import Path

from dual_pipeline.core import read_json, seal_artifact
from dual_pipeline.normalization import (
    SEMANTIC_CORRECTION_CANDIDATE_KEY,
    SEMANTIC_CORRECTION_PACKAGE_KEY,
    normalize_markdown_source,
)
from dual_pipeline.strategic_ingestion import STRATEGIC_CANDIDATE_PROFILES_KEY

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = (
    ROOT
    / "exports"
    / "sherpa-aws-a2a-architecture"
    / "context-retrieval"
    / "sherpa-a2a-semantic-correction-2026-07-29.package.json"
)


class SherpaSemanticCorrectionTests(unittest.TestCase):
    def test_normalization_preserves_all_exact_correction_bindings(self) -> None:
        package = read_json(PACKAGE)
        bundle = normalize_markdown_source(package, run_context="focused-test")

        self.assertEqual(12, len(bundle["candidates"]))
        self.assertEqual(
            {"source_chunk": 10, "action": 2},
            {
                kind: sum(
                    candidate["candidate_type"] == kind
                    for candidate in bundle["candidates"]
                )
                for kind in ("source_chunk", "action")
            },
        )
        governance = bundle["extensions"][SEMANTIC_CORRECTION_CANDIDATE_KEY]
        self.assertEqual(12, len(governance))
        self.assertTrue(all(item["force_hitl"] is True for item in governance.values()))
        self.assertEqual(
            {"extraction-run-1b7e8fa0aa3b5faaf41589d88370"},
            {item["replaces_run_id"] for item in governance.values()},
        )
        self.assertEqual(
            1, sum(item["retire_only"] for item in governance.values())
        )

    def test_normalization_rejects_an_unbound_chunk(self) -> None:
        package = copy.deepcopy(read_json(PACKAGE))
        controls = package["extensions"][SEMANTIC_CORRECTION_PACKAGE_KEY]["controls"]
        controls.pop(next(iter(controls)))
        package = seal_artifact(package)

        with self.assertRaisesRegex(
            ValueError, "controls must bind every exact chunk"
        ):
            normalize_markdown_source(package, run_context="focused-test")

    def test_semantic_replacement_can_add_an_orthogonal_strategic_profile(self) -> None:
        package = copy.deepcopy(read_json(PACKAGE))
        first = package["chunks"][0]["chunk_id"]
        package["chunks"] = [package["chunks"][0]]
        controls = package["extensions"][SEMANTIC_CORRECTION_PACKAGE_KEY]["controls"]
        package["extensions"][SEMANTIC_CORRECTION_PACKAGE_KEY]["controls"] = {
            first: controls[first]
        }
        package["extensions"]["com.partner_advisory.nee.strategic-profile-controls"] = {
            first: {
                "knowledge_role": "design_principle",
                "modality": "normative",
                "authority_scope": "source_position",
                "applicability_scope": "nee_specific",
                "realization_state": "not_applicable",
            }
        }
        package = seal_artifact(package)

        bundle = normalize_markdown_source(package, run_context="focused-test")
        candidate_id = bundle["candidates"][0]["candidate_id"]
        self.assertIn(
            candidate_id,
            bundle["extensions"][SEMANTIC_CORRECTION_CANDIDATE_KEY],
        )
        self.assertEqual(
            "design_principle",
            bundle["extensions"][STRATEGIC_CANDIDATE_PROFILES_KEY][candidate_id][
                "knowledge_role"
            ],
        )


if __name__ == "__main__":
    unittest.main()

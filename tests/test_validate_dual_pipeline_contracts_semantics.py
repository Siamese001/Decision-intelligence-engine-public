#!/usr/bin/env python3
"""Focused unit tests for semantic validation fail-closed invariants.

Directly tests the highest-complexity hotspot in the repository:
`validate_semantics()` in `tools/validate_dual_pipeline_contracts.py`
(Cyclomatic Complexity: 245, Cognitive Complexity: 3810).
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.validate_dual_pipeline_contracts as contracts

VALID = ROOT / "contracts" / "dual_pipeline" / "fixtures" / "valid"


def load_fixture(name: str) -> dict:
    return json.loads((VALID / name).read_text(encoding="utf-8"))


class ValidateSemanticsHotspotTests(unittest.TestCase):
    def test_adjudication_packet_valid_semantics_passes(self) -> None:
        """Baseline valid adjudication packet passes semantic validation."""
        artifact = load_fixture("graph-label-adjudication-packet.json")
        contracts.validate_semantics(artifact)

    def test_adjudication_packet_rejects_mismatched_pair_binding(self) -> None:
        """Tampered graph_sha256 in review plan must trigger SEMANTIC_ADJUDICATION_PAIR_BINDING."""
        artifact = load_fixture("graph-label-adjudication-packet.json")
        artifact["review_plan"]["base_graph_sha256"] = "f" * 64

        with self.assertRaises(contracts.ValidationFailure) as ctx:
            contracts.validate_semantics(artifact)
        self.assertEqual(ctx.exception.issue.code, "SEMANTIC_ADJUDICATION_PAIR_BINDING")

    def test_adjudication_packet_rejects_summary_queue_count_mismatch(self) -> None:
        """Mismatched total_queue_count must trigger SEMANTIC_ADJUDICATION_SUMMARY."""
        artifact = load_fixture("graph-label-adjudication-packet.json")
        artifact["summary"]["total_queue_count"] += 1

        with self.assertRaises(contracts.ValidationFailure) as ctx:
            contracts.validate_semantics(artifact)
        self.assertEqual(ctx.exception.issue.code, "SEMANTIC_ADJUDICATION_SUMMARY")

    def test_adjudication_packet_rejects_record_count_mismatch(self) -> None:
        """Mismatch between record_count and len(record_ids) triggers SEMANTIC_ADJUDICATION_RECORD_COUNT."""
        artifact = load_fixture("graph-label-adjudication-packet.json")
        # Remove an ID so len(record_ids) != record_count while summary matches record_count
        artifact["queues"][0]["record_ids"].pop()

        with self.assertRaises(contracts.ValidationFailure) as ctx:
            contracts.validate_semantics(artifact)
        self.assertEqual(ctx.exception.issue.code, "SEMANTIC_ADJUDICATION_RECORD_COUNT")

    def test_adjudication_packet_rejects_invalid_review_tier_binding(self) -> None:
        """Tier 1 with dual_review_required=True must trigger SEMANTIC_ADJUDICATION_REVIEW_TIER."""
        artifact = load_fixture("graph-label-adjudication-packet.json")
        artifact["queues"][0]["dual_review_required"] = True

        with self.assertRaises(contracts.ValidationFailure) as ctx:
            contracts.validate_semantics(artifact)
        self.assertEqual(ctx.exception.issue.code, "SEMANTIC_ADJUDICATION_REVIEW_TIER")

    def test_normalized_candidate_bundle_rejects_dangling_evidence_id(self) -> None:
        """Unknown evidence ID in candidate triggers SEMANTIC_DANGLING_REFERENCE."""
        artifact = load_fixture("normalized-candidate-bundle.json")
        artifact["candidates"][0]["evidence_ids"].append("evidence-nonexistent-999")

        with self.assertRaises(contracts.ValidationFailure) as ctx:
            contracts.validate_semantics(artifact)
        self.assertEqual(ctx.exception.issue.code, "SEMANTIC_DANGLING_REFERENCE")

    def test_architecture_diagram_rejects_dangling_component_ref(self) -> None:
        """Unknown component ref in relationship triggers SEMANTIC_DANGLING_REFERENCE."""
        artifact = load_fixture("architecture-diagram.json")
        artifact["relationships"][0]["from_component_ref"] = "component-missing-xyz"

        with self.assertRaises(contracts.ValidationFailure) as ctx:
            contracts.validate_semantics(artifact)
        self.assertEqual(ctx.exception.issue.code, "SEMANTIC_DANGLING_REFERENCE")

    def test_copilot_intake_rejects_duplicate_speaker_ref(self) -> None:
        """Duplicate speaker_ref in copilot-intake triggers SEMANTIC_DUPLICATE_ID."""
        artifact = load_fixture("copilot-meeting.json")
        duplicate_speaker = copy.deepcopy(artifact["speakers"][0])
        artifact["speakers"].append(duplicate_speaker)

        with self.assertRaises(contracts.ValidationFailure) as ctx:
            contracts.validate_semantics(artifact)
        self.assertEqual(ctx.exception.issue.code, "SEMANTIC_DUPLICATE_ID")

    def test_retrieval_evidence_report_rejects_duplicate_requirement_id(self) -> None:
        """Duplicate requirement_id in retrieval-evidence-report triggers SEMANTIC_DUPLICATE_ID."""
        artifact = load_fixture("retrieval-evidence-report.json")
        duplicate_outcome = copy.deepcopy(artifact["requirement_outcomes"][0])
        artifact["requirement_outcomes"].append(duplicate_outcome)

        with self.assertRaises(contracts.ValidationFailure) as ctx:
            contracts.validate_semantics(artifact)
        self.assertEqual(ctx.exception.issue.code, "SEMANTIC_DUPLICATE_ID")


if __name__ == "__main__":
    unittest.main()

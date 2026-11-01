"""tests.test_scheduled_query_coverage_repair

Wave 4 Track 4C test suite verifying requirement-level diagnostic records,
separation of missing evidence / retrieval omission / policy restriction / malformed requirements,
false-block repair, and preservation of legitimate policy guards.
"""
from __future__ import annotations

import unittest

from dual_pipeline.query import compile_query_coverage_manifest
from dual_pipeline.query_core.diagnostics import (
    CoverageDiagnosticCategory,
    RequirementDisposition,
    diagnose_coverage_requirement,
)


def _plan(query_type: str, lanes: list[str]) -> dict[str, object]:
    return {
        "run_id": "run-scheduled-query-test",
        "query_type": query_type,
        "allowed_lanes": lanes,
        "materiality": "high",
        "primary_entities": ["Project Beacon"],
        "evidence_requirements": ["Approved internal facts"],
        "subquestions": ["What patterns recur?"],
        "canonical_digest": {"value": "2" * 64},
    }


class ScheduledQueryCoverageRepairTests(unittest.TestCase):
    def test_formatting_directive_does_not_false_block_valid_query(self) -> None:
        """Acceptance criterion: One reproduced false block becomes a grounded valid answer without lowered thresholds."""
        question = (
            "1. A2A protocol patterns\n"
            "2. Provide concise executive bullet points\n"
            "3. PostgreSQL write-back controls"
        )
        manifest = compile_query_coverage_manifest(
            question=question,
            validated_request_digest="1" * 64,
            plan=_plan("cross_workstream_synthesis", ["approved"]),
            active={"graph_snapshot": "snap", "graph_sha256": "3" * 64},
            authorization_scope_digest="4" * 64,
        )

        labels = [row["requirement_label"] for row in manifest["requirements"]]
        # "Provide concise executive bullet points" must be excluded as formatting!
        self.assertNotIn("Provide concise executive bullet points", labels)
        self.assertIn("A2A protocol patterns", labels)
        self.assertIn("PostgreSQL write-back controls", labels)

    def test_requirement_diagnostics_categories(self) -> None:
        """Acceptance criterion: SOURCE_COVERAGE_INCOMPLETE alone is not a bug classification."""
        # 1. Output presentation formatting
        diag_fmt = diagnose_coverage_requirement({"requirement_id": "req-1", "label": "Executive bullet points summary"})
        self.assertEqual(diag_fmt.category, CoverageDiagnosticCategory.MALFORMED_REQUIREMENT)
        self.assertEqual(diag_fmt.disposition, RequirementDisposition.FORMATTING_DIRECTIVE_EXCLUDED)
        self.assertTrue(diag_fmt.is_defect)
        self.assertIsNotNone(diag_fmt.remediation_code_delta)

        # 2. Policy restriction
        diag_pol = diagnose_coverage_requirement(
            {"requirement_id": "req-2", "label": "Confidential compensation details"},
            policy_restrictions=["compensation"],
        )
        self.assertEqual(diag_pol.category, CoverageDiagnosticCategory.POLICY_RESTRICTION)
        self.assertEqual(diag_pol.disposition, RequirementDisposition.GENUINE_GAP)
        self.assertFalse(diag_pol.is_defect)

        # 3. Retrieval omission (evidence present in corpus)
        corpus = [{"doc_id": "doc-99", "text": "PostgreSQL write-back controls ensure WAL sync"}]
        diag_omiss = diagnose_coverage_requirement(
            {"requirement_id": "req-3", "label": "PostgreSQL write-back controls"},
            corpus_evidence=corpus,
        )
        self.assertEqual(diag_omiss.category, CoverageDiagnosticCategory.RETRIEVAL_OMISSION)
        self.assertEqual(diag_omiss.disposition, RequirementDisposition.RECOVERABLE_VIA_SUPPLEMENTAL)
        self.assertTrue(diag_omiss.is_defect)
        self.assertEqual(diag_omiss.evidence_refs, ["doc-99"])

        # 4. Genuinely missing source evidence
        diag_missing = diagnose_coverage_requirement(
            {"requirement_id": "req-4", "label": "Quantum encryption teleportation"},
            corpus_evidence=corpus,
        )
        self.assertEqual(diag_missing.category, CoverageDiagnosticCategory.MISSING_SOURCE_EVIDENCE)
        self.assertEqual(diag_missing.disposition, RequirementDisposition.GENUINE_GAP)
        self.assertFalse(diag_missing.is_defect)

    def test_genuine_gaps_remain_strictly_blocked(self) -> None:
        """Acceptance criterion: Genuine gaps and restrictions remain blocked; no widened access or fabricated evidence."""
        diag = diagnose_coverage_requirement(
            {"requirement_id": "req-gap", "label": "Unpublished proprietary secret"},
            policy_restrictions=["proprietary secret"],
        )
        self.assertEqual(diag.disposition, RequirementDisposition.GENUINE_GAP)
        self.assertFalse(diag.is_defect)
        self.assertEqual(diag.evidence_refs, [])

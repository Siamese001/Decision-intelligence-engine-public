"""
tests/test_unified_qa_and_reconciliation.py

Verifies Wave 2: Harmonized QA Gates, Micro-Judges & Reconciler Budget Convergence:
1. Enforces CANONICAL_MUTATION_BUDGET = 30 across all linters, gates, and reconcilers.
2. Reconciler passes a 26-mutation deliverable (eliminating the task-1547 blocker).
3. Reconciler fails closed on 31+ mutations with 'exceeds maximum budget of 30'.
4. UnifiedPipelineQAEvaluator handles Full Meeting, Notes, and Document modes deterministically.
5. Micro-judge diff extraction extracts actionable directives on failure.
"""
from __future__ import annotations

import json
import unittest

from dual_pipeline.canonical_pipeline.evaluator import (
    CANONICAL_MUTATION_BUDGET,
    UnifiedPipelineQAEvaluator,
)
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.llm_judge_core.reconciler import CrossArtifactReconciler
from dual_pipeline.remediation_core.diff_injector import RemediationDiffInjector


class TestUnifiedQAAndReconciliation(unittest.TestCase):
    """Test suite validating unified QA evaluators and harmonized budget threshold."""

    def test_mutation_budget_ssot(self) -> None:
        """Verify CANONICAL_MUTATION_BUDGET is strictly 30."""
        self.assertEqual(CANONICAL_MUTATION_BUDGET, 30)

    def test_reconciler_permits_26_mutations(self) -> None:
        """
        Verify that 26 mutations (the Chakra count that failed in task-1547)
        now passes reconciliation without budget violations.
        """
        reconciler = CrossArtifactReconciler()
        entities = [
            {"entity_id": f"system:service_{i}", "entity_type": "system", "name": f"Service {i}"}
            for i in range(16)
        ]
        relationships = [
            {"subject": f"system:service_{i}", "predicate": "integrates_with", "object": f"system:service_{i+1}"}
            for i in range(10)
        ]
        # Total mutations = 16 + 10 = 26
        graph_data = {
            "schema_version": "5.7",
            "graph_mutations": {
                "entity_upserts": entities,
                "relationship_signals": relationships,
                "action_updates": [],
            },
        }
        failures = reconciler.validate_mutation_budget(graph_data)
        # Should NOT contain budget failure
        budget_failures = [f for f in failures if "exceeds maximum budget" in f]
        self.assertEqual(budget_failures, [], f"Expected 26 mutations to pass, got: {budget_failures}")

    def test_reconciler_rejects_31_mutations(self) -> None:
        """Verify that 31 mutations fails closed with budget ceiling of 30."""
        reconciler = CrossArtifactReconciler()
        entities = [
            {"entity_id": f"system:service_{i}", "entity_type": "system", "name": f"Service {i}"}
            for i in range(20)
        ]
        relationships = [
            {"subject": f"system:service_{i}", "predicate": "integrates_with", "object": f"system:service_{i+1}"}
            for i in range(11)
        ]
        # Total mutations = 20 + 11 = 31
        graph_data = {
            "schema_version": "5.7",
            "graph_mutations": {
                "entity_upserts": entities,
                "relationship_signals": relationships,
                "action_updates": [],
            },
        }
        failures = reconciler.validate_mutation_budget(graph_data)
        budget_failures = [f for f in failures if "exceeds maximum budget" in f]
        self.assertEqual(len(budget_failures), 1)
        self.assertIn("exceeds maximum budget of 30", budget_failures[0])

    def test_notes_mode_qa_evaluation(self) -> None:
        """Verify UnifiedPipelineQAEvaluator in MEETING_NOTES mode marks Gate 1 as skipped."""
        synth = SynthesisResult(
            output_1=None,
            output_2="# Executive Recap\n\n## Meeting Metadata\n- Date: 09/23/2026\n\n## Strategic Synthesis\nNotes content.",
            output_3=json.dumps({
                "schema_version": "5.7",
                "graph_mutations": {
                    "entity_upserts": [{"entity_id": "project:nexus", "entity_type": "project", "name": "Nexus"}],
                    "relationship_signals": [],
                    "action_updates": [],
                },
            }),
            attempt=1,
        )
        metadata = {"meeting_name": "Notes Test", "date": "09/23/2026", "notes_mode": True}
        auth, qa_rep, judge_rep, final_dec = UnifiedPipelineQAEvaluator.evaluate(
            modality=CanonicalModality.MEETING_NOTES,
            synthesis_result=synth,
            source_text="Sample notes text",
            metadata=metadata,
            mock=True,
        )

        # Output 01 gate must be skipped
        g1 = qa_rep.get("gate_1") or qa_rep.get("output_01_forensic_transcript", {})
        self.assertEqual(g1.get("status"), "SKIPPED")
        self.assertEqual(g1.get("reason"), "NOT_APPLICABLE_NOTES_MODE")

    def test_document_mode_qa_evaluation(self) -> None:
        """Verify UnifiedPipelineQAEvaluator in DOCUMENT mode marks Gates 1 & 2 as skipped."""
        synth = SynthesisResult(
            output_1=None,
            output_2=None,
            output_3=json.dumps({
                "schema_version": "5.7",
                "graph_mutations": {
                    "entity_upserts": [{"entity_id": "system:nexus_core", "entity_type": "system", "name": "Nexus Core"}],
                    "relationship_signals": [],
                    "action_updates": [],
                },
            }),
            attempt=1,
        )
        metadata = {"title": "Doc Test", "date": "09/23/2026", "is_document": True}
        auth, qa_rep, judge_rep, final_dec = UnifiedPipelineQAEvaluator.evaluate(
            modality=CanonicalModality.DOCUMENT,
            synthesis_result=synth,
            source_text="# Architecture Spec\nAuthor: Amit Ayer",
            metadata=metadata,
            mock=True,
        )

        g1 = qa_rep.get("gate_1") or qa_rep.get("output_01_forensic_transcript", {})
        g2 = qa_rep.get("gate_2") or qa_rep.get("output_02_executive_recap", {})
        self.assertEqual(g1.get("status"), "SKIPPED")
        self.assertEqual(g2.get("status"), "SKIPPED")

    def test_diff_injector_directive_extraction(self) -> None:
        """Verify RemediationDiffInjector extracts actionable directives from judge/QA reports."""
        qa_report = {
            "output_03_canonical_signal_projection": {
                "failures": ["Total graph mutations (31) exceeds maximum budget of 30."]
            }
        }
        judge_report = {
            "output_3_evaluation": {
                "actionable_remediation_diff": "Prune peripheral entities to fit within budget of 30."
            }
        }
        directive = RemediationDiffInjector.extract_directive("output_3", judge_report, qa_report)
        self.assertIn("Prune peripheral entities", directive)


if __name__ == "__main__":
    unittest.main()

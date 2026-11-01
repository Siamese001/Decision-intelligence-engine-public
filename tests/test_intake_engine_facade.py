"""Unit tests for dual_pipeline.intake_engine facade.

Verifies complete re-export coverage, symbol parity, and backward compatibility.
"""
from __future__ import annotations

import unittest

import dual_pipeline.intake_engine as intake_facade


class TestIntakeEngineFacade(unittest.TestCase):
    def test_core_classes_exported(self) -> None:
        self.assertTrue(hasattr(intake_facade, "IntakeEngine"))
        self.assertTrue(hasattr(intake_facade, "HybridRetriever"))
        self.assertTrue(hasattr(intake_facade, "RunRecorder"))
        self.assertTrue(hasattr(intake_facade, "SemanticCache"))
        self.assertTrue(hasattr(intake_facade, "AuthorizationContext"))

    def test_core_functions_exported(self) -> None:
        expected_functions = [
            "build_ingestion_plan",
            "build_dispositions",
            "build_routes",
            "build_graph_change_set",
            "build_hitl_batches",
            "build_ingestion_decision_explanation",
            "build_ingestion_decision_ledger",
            "render_ingestion_decision_ledger_markdown",
            "compare_candidates",
            "hydrate_comparison_context",
            "candidate_snapshot_profiles",
            "candidate_strategic_profile",
            "normalize_evidence_envelope",
            "require_creatable_node_type",
            "resolve_bundle",
            "build_planned_graph_delta",
        ]
        for fn in expected_functions:
            self.assertTrue(
                callable(getattr(intake_facade, fn, None)),
                f"Expected function {fn} to be exported and callable from intake_engine facade",
            )

    def test_constants_and_keys_exported(self) -> None:
        expected_constants = [
            "INTAKE_STAGES",
            "INGESTION_DECISION_LEDGER_JSON",
            "INGESTION_DECISION_LEDGER_MARKDOWN",
            "NEW_INTERNAL_ENTITY_KEY",
            "RETIRE_ASSERTION_KEY",
            "SUPERSEDES_ASSERTION_KEY",
            "SEMANTIC_CORRECTION_CANDIDATE_KEY",
        ]
        for c in expected_constants:
            self.assertTrue(
                hasattr(intake_facade, c),
                f"Expected constant {c} to be exported from intake_engine facade",
            )

    def test_a2a_finalization_symbols_exported(self) -> None:
        self.assertTrue(hasattr(intake_facade, "VerifiedA2AFinalization"))
        self.assertTrue(callable(getattr(intake_facade, "verify_finalized_a2a_package_bytes", None)))
        self.assertTrue(callable(getattr(intake_facade, "stage_ingress_finalization_evidence", None)))


if __name__ == "__main__":
    unittest.main()

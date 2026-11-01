"""Unit tests for L1 Post-L2 Review Contracts & Deterministic Verifier."""
from __future__ import annotations

import unittest

from dual_pipeline.core import digest
from dual_pipeline.l1_deterministic_checker import run_l1_deterministic_checks
from dual_pipeline.l1_review_contracts import (
    DeterministicCheckItem,
    DeterministicReasonCode,
    L1PostL2ReviewContract,
    L1ReviewVerdict,
)
from dual_pipeline.sealed_l2 import seal_l2_output


class TestL1ReviewContracts(unittest.TestCase):
    """Test suite for L1PostL2ReviewContract and integrity methods."""

    def test_review_contract_creation_and_integrity(self) -> None:
        check1 = DeterministicCheckItem(name="disposition", passed=True, details="primary")
        check2 = DeterministicCheckItem(name="schema", passed=True, details="valid")

        contract = L1PostL2ReviewContract.create(
            run_id="run-001",
            query_id="query-101",
            plan_id="plan-202",
            sealed_artifact_id="sealed-303",
            verdict=L1ReviewVerdict.SUFFICIENT,
            deterministic_passed=True,
            deterministic_checks=[check1, check2],
            semantic_passed=True,
            semantic_reasoning={"objective_satisfied": True, "rationale": "complete"},
        )

        self.assertEqual(contract.verdict, L1ReviewVerdict.SUFFICIENT)
        self.assertTrue(contract.deterministic_passed)
        self.assertTrue(contract.semantic_passed)
        self.assertEqual(len(contract.deterministic_checks), 2)
        self.assertIn("canonical_digest", contract.to_dict())
        self.assertTrue(contract.verify_integrity())

    def test_review_contract_tamper_detection(self) -> None:
        contract = L1PostL2ReviewContract.create(
            run_id="run-002",
            query_id="query-102",
            plan_id="plan-203",
            sealed_artifact_id="sealed-304",
            verdict=L1ReviewVerdict.SUFFICIENT,
            deterministic_passed=True,
            deterministic_checks=[],
            semantic_passed=True,
        )
        d = contract.to_dict()
        # Tamper with verdict
        d["verdict"] = "INSUFFICIENT_REPLAN"
        self.assertNotEqual(digest({k: v for k, v in d.items() if k != "canonical_digest"}), d["canonical_digest"]["value"])


class TestL1DeterministicChecker(unittest.TestCase):
    """Test suite for run_l1_deterministic_checks."""

    def setUp(self) -> None:
        self.plan = {
            "plan_id": "plan-001",
            "query_type": "opportunity_nba",
            "acceptance_criteria": {"min_citations": 1},
        }
        self.evidence_packet = {
            "packet_digest": "digest123",
            "approved": [
                {"record": {"record_id": "rec-101", "title": "PDLC Offering"}},
                {"record": {"record_id": "rec-102", "title": "Architecture Blueprint"}},
            ],
            "candidate": [],
            "external_public": [],
        }
        self.answer = {
            "query_type": "opportunity_nba",
            "opportunities": [
                {
                    "opportunity": "Modernize PDLC",
                    "why_prioritize": "Critical path",
                    "citations": ["rec-101"],
                }
            ],
        }
        self.sealed_l2 = seal_l2_output(
            run_id="run-001",
            query_id="query-101",
            l2_decision_artifact_id="l2d-001",
            l2_disposition="primary",
            l2_model="gpt-5.6-sol",
            l2_answer=self.answer,
            l2_provider_call_count=1,
        )

    def test_valid_observation_passes_all_checks(self) -> None:
        passed, checks = run_l1_deterministic_checks(
            plan=self.plan,
            sealed_l2=self.sealed_l2,
            answer=self.answer,
            evidence_packet=self.evidence_packet,
            graph_hash_before="hash_a",
            graph_hash_after="hash_a",
        )
        self.assertTrue(passed, f"Checks failed: {[c.to_dict() for c in checks if not c.passed]}")
        check_names = {c.name for c in checks}
        self.assertIn("l2_execution_disposition", check_names)
        self.assertIn("sealed_artifact_integrity", check_names)
        self.assertIn("answer_presence", check_names)
        self.assertIn("output_schema_validity", check_names)
        self.assertIn("citations_resolution", check_names)
        self.assertIn("graph_immutability", check_names)
        self.assertIn("plan_acceptance_criteria", check_names)

    def test_failed_l2_disposition_fails_check(self) -> None:
        sealed_failed = dict(self.sealed_l2)
        sealed_failed["l2_disposition"] = "failed"
        # Re-sign
        sealed_failed["canonical_digest"] = {
            "algorithm": "sha256",
            "scope": "artifact_without_canonical_digest",
            "value": digest({k: v for k, v in sealed_failed.items() if k != "canonical_digest"}),
        }

        passed, checks = run_l1_deterministic_checks(
            plan=self.plan,
            sealed_l2=sealed_failed,
            answer=self.answer,
            evidence_packet=self.evidence_packet,
        )
        self.assertFalse(passed)
        failed_checks = [c for c in checks if not c.passed]
        self.assertTrue(any(c.reason_code == DeterministicReasonCode.L2_EXECUTION_FAILED.value for c in failed_checks))

    def test_tampered_sealed_digest_fails_integrity(self) -> None:
        tampered_sealed = dict(self.sealed_l2)
        tampered_sealed["canonical_digest"] = {
            "algorithm": "sha256",
            "scope": "artifact_without_canonical_digest",
            "value": "0000000000000000000000000000000000000000000000000000000000000000",
        }
        passed, checks = run_l1_deterministic_checks(
            plan=self.plan,
            sealed_l2=tampered_sealed,
            answer=self.answer,
            evidence_packet=self.evidence_packet,
        )
        self.assertFalse(passed)
        failed_checks = [c for c in checks if not c.passed]
        self.assertTrue(any(c.reason_code == DeterministicReasonCode.CANONICAL_DIGEST_MISMATCH.value for c in failed_checks))

    def test_missing_required_schema_fields_fails(self) -> None:
        bad_answer = {"query_type": "opportunity_nba"}  # missing 'opportunities'
        passed, checks = run_l1_deterministic_checks(
            plan=self.plan,
            sealed_l2=self.sealed_l2,
            answer=bad_answer,
            evidence_packet=self.evidence_packet,
        )
        self.assertFalse(passed)
        failed_checks = [c for c in checks if not c.passed]
        self.assertTrue(any(c.reason_code == DeterministicReasonCode.REQUIRED_FIELDS_MISSING.value for c in failed_checks))

    def test_unresolved_citations_fail(self) -> None:
        answer_with_hallucinated_cite = {
            "query_type": "opportunity_nba",
            "opportunities": [
                {
                    "opportunity": "Modernize PDLC",
                    "why_prioritize": "Critical path",
                    "citations": ["rec-999-unresolved"],
                }
            ],
        }
        passed, checks = run_l1_deterministic_checks(
            plan=self.plan,
            sealed_l2=self.sealed_l2,
            answer=answer_with_hallucinated_cite,
            evidence_packet=self.evidence_packet,
        )
        self.assertFalse(passed)
        failed_checks = [c for c in checks if not c.passed]
        self.assertTrue(any(c.reason_code == DeterministicReasonCode.CITATIONS_UNRESOLVED.value for c in failed_checks))

    def test_ungrounded_claims_in_map_fail(self) -> None:
        claim_map = {
            "claims": [
                {"claim_text": "PDLC is ready", "status": "grounded"},
                {"claim_text": "Unverified claim", "status": "unsupported"},
            ]
        }
        passed, checks = run_l1_deterministic_checks(
            plan=self.plan,
            sealed_l2=self.sealed_l2,
            answer=self.answer,
            evidence_packet=self.evidence_packet,
            claim_evidence_map=claim_map,
        )
        self.assertFalse(passed)
        failed_checks = [c for c in checks if not c.passed]
        self.assertTrue(any(c.reason_code == DeterministicReasonCode.CLAIMS_UNGROUNDED.value for c in failed_checks))

    def test_graph_mutation_fails_immutability(self) -> None:
        passed, checks = run_l1_deterministic_checks(
            plan=self.plan,
            sealed_l2=self.sealed_l2,
            answer=self.answer,
            evidence_packet=self.evidence_packet,
            graph_hash_before="hash_alpha",
            graph_hash_after="hash_beta_mutated",
        )
        self.assertFalse(passed)
        failed_checks = [c for c in checks if not c.passed]
        self.assertTrue(any(c.reason_code == DeterministicReasonCode.GRAPH_MUTATION_VIOLATION.value for c in failed_checks))

    def test_unmet_plan_acceptance_criteria_fails(self) -> None:
        plan_strict = dict(self.plan)
        plan_strict["acceptance_criteria"] = {"min_citations": 5}
        passed, checks = run_l1_deterministic_checks(
            plan=plan_strict,
            sealed_l2=self.sealed_l2,
            answer=self.answer,
            evidence_packet=self.evidence_packet,
        )
        self.assertFalse(passed)
        failed_checks = [c for c in checks if not c.passed]
        self.assertTrue(any(c.reason_code == DeterministicReasonCode.ACCEPTANCE_CRITERIA_UNMET.value for c in failed_checks))


if __name__ == "__main__":
    unittest.main()

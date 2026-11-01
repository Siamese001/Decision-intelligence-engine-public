"""
tests/test_module_budget_policy_contract.py

Wave 1 contract and candidate policy qualification test suite.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestModuleBudgetPolicyContract(unittest.TestCase):
    def setUp(self) -> None:
        self.schema_path = ROOT / "contracts" / "governance" / "module_budget_policy.schema.json"
        self.candidate_path = ROOT / "reports" / "module_budget_policy" / "w1" / "candidate_policy.json"
        self.inventory_path = ROOT / "reports" / "module_budget_policy" / "w1" / "surface_inventory.json"
        self.decision_path = ROOT / "reports" / "module_budget_policy" / "w1" / "policy_decision.json"
        self.baseline_path = ROOT / "reports" / "module_budget_policy" / "w1" / "comparison_baseline.json"
        self.cohort_path = ROOT / "reports" / "module_budget_review_50_prs" / "cohort.json"

    def test_deliverable_artifacts_exist(self) -> None:
        self.assertTrue(self.schema_path.is_file(), f"Missing schema: {self.schema_path}")
        self.assertTrue(self.candidate_path.is_file(), f"Missing candidate policy: {self.candidate_path}")
        self.assertTrue(self.inventory_path.is_file(), f"Missing inventory: {self.inventory_path}")
        self.assertTrue(self.decision_path.is_file(), f"Missing decision: {self.decision_path}")
        self.assertTrue(self.baseline_path.is_file(), f"Missing baseline: {self.baseline_path}")
        self.assertTrue(self.cohort_path.is_file(), f"Missing cohort: {self.cohort_path}")

    def test_candidate_policy_contract_schema(self) -> None:
        candidate = json.loads(self.candidate_path.read_text(encoding="utf-8"))
        schema = json.loads(self.schema_path.read_text(encoding="utf-8"))

        for req in schema["required"]:
            self.assertIn(req, candidate, f"Candidate policy missing required schema field: {req}")

        self.assertEqual(candidate["schema_version"], "1.0.0")
        self.assertEqual(candidate["status"], "DRAFT_CANDIDATE")

    def test_boundary_definitions_and_ceilings(self) -> None:
        candidate = json.loads(self.candidate_path.read_text(encoding="utf-8"))
        boundaries = candidate["boundaries"]
        self.assertEqual(boundaries["clean_ceiling"], 400)
        self.assertEqual(boundaries["warning_ceiling_standard"], 650)
        self.assertEqual(boundaries["warning_ceiling_declarative"], 800)

        rules = candidate["boundary_rules"]
        self.assertIn("up_to_400", rules)
        self.assertEqual(rules["up_to_400"]["verdict"], "PASS")
        self.assertFalse(rules["up_to_400"]["warning"])
        self.assertFalse(rules["up_to_400"]["blocking"])

        self.assertIn("401_to_650_ordinary", rules)
        self.assertEqual(rules["401_to_650_ordinary"]["verdict"], "WARNING")
        self.assertTrue(rules["401_to_650_ordinary"]["warning"])
        self.assertFalse(rules["401_to_650_ordinary"]["blocking"])
        self.assertEqual(rules["401_to_650_ordinary"]["max_cognitive_complexity"], 15)

        self.assertIn("651_to_800_declarative", rules)
        self.assertEqual(rules["651_to_800_declarative"]["verdict"], "WARNING")
        self.assertTrue(rules["651_to_800_declarative"]["declarative_validation_required"])

        self.assertIn("above_warning_range", rules)
        self.assertEqual(rules["above_warning_range"]["verdict"], "BLOCK")
        self.assertTrue(rules["above_warning_range"]["blocking"])

        self.assertIn("structural_violation_invariance", rules)
        self.assertTrue(rules["structural_violation_invariance"]["blocking"])
        self.assertFalse(rules["structural_violation_invariance"]["waivable_by_size_exception"])

        self.assertIn("parsing_and_policy_evidence_error", rules)
        self.assertTrue(rules["parsing_and_policy_evidence_error"]["fail_closed"])

    def test_active_production_enforcement_remains_unchanged(self) -> None:
        policy_registry_path = ROOT / "governance" / "policy_registry.yaml"
        content = policy_registry_path.read_text(encoding="utf-8")
        self.assertIn('id: "INV-BUDGET-FILE-LINES"', content)
        self.assertIn("threshold: 400", content)

        linter_path = ROOT / "tools" / "lint_file_budgets.py"
        linter_content = linter_path.read_text(encoding="utf-8")
        self.assertIn("DEFAULT_MAX_LINES = 400", linter_content)

    def test_surface_inventory_coverage(self) -> None:
        inventory = json.loads(self.inventory_path.read_text(encoding="utf-8"))
        self.assertTrue(inventory.get("complete_coverage_attestation", False))
        self.assertEqual(inventory.get("unresolved_consumers"), [])
        consumers = inventory.get("consumers", [])
        self.assertGreaterEqual(len(consumers), 10)

        consumer_ids = {c["consumer_id"] for c in consumers}
        expected_ids = {
            "file_linter",
            "module_budget_checker",
            "staged_checker",
            "readiness_gate_3",
            "scheduled_audit",
            "wave_gates",
            "ingestion_acceptance_helpers",
            "generated_guidance",
            "active_skills",
            "direct_line_count_tests",
        }
        for eid in expected_ids:
            self.assertIn(eid, consumer_ids, f"Surface inventory missing expected consumer: {eid}")

    def test_baseline_112_reconciliation(self) -> None:
        baseline_report = json.loads(self.baseline_path.read_text(encoding="utf-8"))
        rec = baseline_report["baseline_reconciliation_112"]
        self.assertEqual(rec["total_recorded_modules"], 112)
        self.assertEqual(rec["retained_oversized_count"], 105)
        self.assertEqual(rec["already_compliant_count"], 6)
        self.assertEqual(rec["removed_count"], 1)
        self.assertEqual(rec["renamed_or_invalid_count"], 0)
        self.assertEqual(
            rec["retained_oversized_count"] + rec["already_compliant_count"] + rec["removed_count"],
            112
        )

    def test_historical_50_pr_cohort_metrics(self) -> None:
        cohort_data = json.loads(self.cohort_path.read_text(encoding="utf-8"))
        cohort = cohort_data["cohort"]
        self.assertEqual(len(cohort), 50)

        corrective = [c for c in cohort if c["classification"] == "CORRECTIVE_REMEDIATION"]
        self.assertEqual(len(corrective), 8)
        self.assertEqual([c["index"] for c in corrective], [4, 26, 34, 38, 39, 43, 44, 48])

        feature_integrated = [c for c in cohort if c["classification"] == "FEATURE_INTEGRATED_SIZE_CHANGE"]
        self.assertEqual(len(feature_integrated), 2)
        self.assertEqual([c["index"] for c in feature_integrated], [15, 17])

        union_affected = [c for c in cohort if c["classification"] != "ROUTINE_OR_FEATURE"]
        self.assertEqual(len(union_affected), 10)


if __name__ == "__main__":
    unittest.main()

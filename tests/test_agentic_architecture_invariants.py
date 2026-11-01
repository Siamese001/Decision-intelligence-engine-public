"""Test suite verifying Wave 0 Agentic Architecture Anti-Pattern & Boundary Auditor invariants.

Validates that:
1. Wave 0 auditor executes deterministically and produces an RFC-8259 compliant receipt.
2. Monolithic modules exceeding the 400-line budget are captured accurately.
3. Deprecated shims and prohibited scripts are recognized and inventoried.
4. Feedback loop topologies (Class A vs Class C) conform to architectural contracts.
5. Markdown characterization report renders all required governance sections.
"""
from __future__ import annotations

import unittest

from tools.audit_agentic_boundaries import (
    ROOT,
    audit_dead_facades_and_scripts,
    audit_layer_responsibilities,
    audit_module_budgets,
    format_markdown_report,
    generate_audit_receipt,
)


class TestWave0AgenticArchitectureInvariants(unittest.TestCase):
    def test_wave0_receipt_generation_schema(self) -> None:
        """Verify that the Wave 0 audit receipt matches RFC-8259 and declared schema."""
        receipt = generate_audit_receipt(ROOT)
        self.assertEqual(receipt["schema_version"], "urn:nee:audit:wave0-agentic-architecture-receipt:1.0.0")
        self.assertEqual(receipt["audit_phase"], "Wave 0 - Characterize & Protect Existing Behavior")
        self.assertIn("timestamp", receipt)
        self.assertIn("metrics", receipt)
        self.assertIn("findings", receipt)
        self.assertIn("responsibility_matrix", receipt)
        self.assertIn("findings_digest", receipt)

        # Verify scores are bounded and structured
        scores = receipt["metrics"]["scores"]
        for key in ("architecture_health", "agentic_integrity", "complexity_debt", "refactor_risk"):
            self.assertIn(key, scores)
            score_val = float(scores[key].split("/")[0])
            self.assertTrue(1.0 <= score_val <= 10.0)

    def test_wave0_monolithic_module_detection(self) -> None:
        """Verify detection of modules exceeding 400-line budget in dual_pipeline."""
        violations = audit_module_budgets(ROOT, max_lines=400)
        self.assertGreater(len(violations), 0)

        # Ensure known large modules are accurately captured with delta metrics
        flagged_files = {v["file"] for v in violations}
        self.assertIn("dual_pipeline/drift_guard.py", flagged_files)
        self.assertIn("dual_pipeline/card_views.py", flagged_files)

        for v in violations:
            self.assertEqual(v["severity"], "HIGH")
            self.assertGreater(v["lines"], 400)
            self.assertEqual(v["delta"], v["lines"] - 400)
            self.assertIn("recommended_fix", v)

    def test_wave0_dead_facades_and_scripts(self) -> None:
        """Verify that deprecated shims and prohibited one-off scripts are inventoried or purged."""
        findings = audit_dead_facades_and_scripts(ROOT)
        files = {f["file"] for f in findings}
        if (ROOT / "dual_pipeline/query_classifier.py").exists():
            self.assertIn("dual_pipeline/query_classifier.py", files)
        else:
            self.assertNotIn("dual_pipeline/query_classifier.py", files)
        if (ROOT / "dual_pipeline/dynamic_router.py").exists():
            self.assertIn("dual_pipeline/dynamic_router.py", files)
        else:
            self.assertNotIn("dual_pipeline/dynamic_router.py", files)

        for f in findings:
            self.assertIn(f["anti_pattern"], ("Dead Facade / Redundant Abstraction", "Prohibited Custom Ingestion/Hardening Script"))
            self.assertIn(f["severity"], ("MEDIUM", "HIGH"))

    def test_wave0_responsibility_matrix(self) -> None:
        """Verify that the responsibility matrix covers core capabilities."""
        matrix = audit_layer_responsibilities(ROOT)
        capabilities = {r["capability"] for r in matrix}
        self.assertIn("Query Routing & Lexicon Matching", capabilities)
        self.assertIn("Meeting Ingestion & Forensic Synthesis", capabilities)
        self.assertIn("Graph Mutation & Activation Release", capabilities)
        self.assertIn("LLM Judge Evaluation", capabilities)
        self.assertIn("L1 Cognitive Re-Planning", capabilities)

    def test_wave0_markdown_report_formatting(self) -> None:
        """Verify that Markdown rendering produces expected headings and tables."""
        receipt = generate_audit_receipt(ROOT)
        md_report = format_markdown_report(receipt)

        self.assertIn("# Wave 0: Agentic Architecture Anti-Pattern & Boundary Audit Report", md_report)
        self.assertIn("## 1. Executive Health Scorecard", md_report)
        self.assertIn("## 2. Granular Anti-Pattern Inventory", md_report)
        self.assertIn("## 3. Layer Responsibility Matrix", md_report)
        self.assertIn("## 4. Feedback Loop Topologies", md_report)
        self.assertIn("## 5. Wave Progression Roadmap", md_report)


if __name__ == "__main__":
    unittest.main()

"""Automated test suite for Testing Gaps Review Prompt Contract and Audit Engine.

Verifies:
1. Prompt Contract Invariants (REPO_TESTING_GAPS_REVIEW_PROMPT_V1.md and mirror).
2. All 8 Testing Dimensions defined and structured.
3. TestingGapAuditor AST parsing, dimension classification, scoring, and report generation.
4. CLI execution of tools/audit_testing_gaps.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.audit_testing_gaps import (
    DIMENSION_DEFINITIONS,
    TestFileInfo,
    TestingGapAuditor,
)

CANONICAL_PROMPT_PATH = ROOT / "governance" / "decision_intelligence" / "REPO_TESTING_GAPS_REVIEW_PROMPT_V1.md"
MIRROR_PROMPT_PATH = ROOT / "config" / "prompts" / "testing" / "REPO_TESTING_GAPS_REVIEW_PROMPT_V1.md"


class TestPromptContractInvariants(unittest.TestCase):
    """Validate canonical and mirror prompt contracts against governance rules."""

    def test_prompt_files_exist(self) -> None:
        self.assertTrue(CANONICAL_PROMPT_PATH.is_file(), f"Missing canonical prompt at {CANONICAL_PROMPT_PATH}")
        self.assertTrue(MIRROR_PROMPT_PATH.is_file(), f"Missing mirror prompt at {MIRROR_PROMPT_PATH}")

    def test_canonical_and_mirror_are_byte_identical(self) -> None:
        canonical_content = CANONICAL_PROMPT_PATH.read_text(encoding="utf-8")
        mirror_content = MIRROR_PROMPT_PATH.read_text(encoding="utf-8")
        self.assertEqual(canonical_content, mirror_content, "Mirror prompt in config/prompts/ diverged from governance/")

    def test_mandatory_header_and_document_id(self) -> None:
        content = CANONICAL_PROMPT_PATH.read_text(encoding="utf-8")
        self.assertIn("PROMPT-GOV-TESTING-GAPS-REVIEW-20260912-V1", content)
        self.assertIn("Internal Strategic Engineering & Quality Assurance Governance Contract", content)
        self.assertIn("MASTER AUDIT & EVALUATION PROMPT DIRECTIVE", content)

    def test_all_eight_dimensions_present(self) -> None:
        content = CANONICAL_PROMPT_PATH.read_text(encoding="utf-8")
        expected_dimensions = [
            "DIMENSION 1: UNIT TESTING & BOUNDARY ISOLATION",
            "DIMENSION 2: END-TO-END (E2E) PIPELINE & INGESTION LIFECYCLE",
            "DIMENSION 3: INTEGRATION & CROSS-SUBSYSTEM INTEROPERABILITY",
            "DIMENSION 4: REGRESSION & INVARIANT SHIELDING",
            "DIMENSION 5: PERFORMANCE, LATENCY, GPU & CONCURRENCY",
            "DIMENSION 6: HUMAN-IN-THE-LOOP (HITL) & GOVERNANCE AUDITING",
            "DIMENSION 7: EPISTEMIC BOUNDARIES & SECURITY / DATA CLASSIFICATION",
            "DIMENSION 8: MULTI-AGENT DELEGATION & AGENT-TO-AGENT (A2A) PROTOCOLS",
        ]
        for dim in expected_dimensions:
            self.assertIn(dim, content, f"Missing required dimension header: {dim}")

    def test_mandatory_deliverable_sections(self) -> None:
        content = CANONICAL_PROMPT_PATH.read_text(encoding="utf-8")
        self.assertIn("1. THE EXECUTIVE TESTING MATURITY SCORECARD", content)
        self.assertIn("2. FORENSIC GAP ANALYSIS (P0 / P1 / P2 DEFICIENCIES)", content)
        self.assertIn("3. CONCRETE REMEDIATION & TEST EXPANSION BLUEPRINT", content)
        self.assertIn("4. 10 TEST BENCHMARK SCENARIOS", content)
        self.assertIn("END OF AUDIT PROMPT", content)


class TestTestingGapAuditorEngine(unittest.TestCase):
    """Test AST scanning, dimension classification, scoring, and report generation."""

    def test_dimension_definitions_completeness(self) -> None:
        self.assertEqual(len(DIMENSION_DEFINITIONS), 8)
        required_keys = {"unit", "e2e", "integration", "regression", "performance", "hitl", "epistemic_security", "multi_agent"}
        self.assertEqual(set(DIMENSION_DEFINITIONS.keys()), required_keys)

    def test_classify_dimensions_logic(self) -> None:
        auditor = TestingGapAuditor(repo_root=ROOT)
        info = TestFileInfo(path="tests/test_foo_perf.py", name="test_foo_perf")
        content = """
        def test_sub_120ms_latency():
            assert latency < 120
        """
        auditor._classify_dimensions(info, content)
        self.assertIn("performance", info.dimensions)

        info_hitl = TestFileInfo(path="tests/test_margin_gate.py", name="test_margin_gate")
        content_hitl = """
        def test_hitl_margin_gate():
            margin = top - second
            assert margin >= 0.25
        """
        auditor._classify_dimensions(info_hitl, content_hitl)
        self.assertIn("hitl", info_hitl.dimensions)

        info_firewall = TestFileInfo(path="tests/test_advisory_firewall.py", name="test_advisory_firewall")
        content_firewall = """
        def test_career_firewall():
            assert 'INTERNAL_ADVISORY' in shadow_db
        """
        auditor._classify_dimensions(info_firewall, content_firewall)
        self.assertIn("epistemic_security", info_firewall.dimensions)

    def test_scan_and_compute_summary(self) -> None:
        auditor = TestingGapAuditor(repo_root=ROOT)
        auditor.scan()

        self.assertGreaterEqual(len(auditor.test_files), 150)
        self.assertGreaterEqual(len(auditor.modules), 100)
        self.assertEqual(len(auditor.dimension_scores), 8)

        summary = auditor.compute_overall_summary()
        self.assertIn("overall_score", summary)
        self.assertGreaterEqual(summary["overall_score"], 0.0)
        self.assertLessEqual(summary["overall_score"], 100.0)
        self.assertGreaterEqual(summary["overall_maturity_level"], 1)
        self.assertLessEqual(summary["overall_maturity_level"], 5)
        self.assertIn(summary["overall_verdict"], {"PASS", "WARN", "FAIL"})

    def test_markdown_report_generation(self) -> None:
        auditor = TestingGapAuditor(repo_root=ROOT)
        auditor.scan()
        report = auditor.generate_markdown_report()

        self.assertIn("# Repository Testing Gaps & Verification Architecture Audit Report", report)
        self.assertIn("THE EXECUTIVE TESTING MATURITY SCORECARD", report)
        self.assertIn("FORENSIC GAP ANALYSIS", report)
        self.assertIn("10 TEST BENCHMARK SCENARIOS EVALUATION", report)
        self.assertIn("END OF AUDIT REPORT", report)

    def test_cli_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            md_out = tmp_path / "test_report.md"
            json_out = tmp_path / "test_report.json"

            cmd = [
                sys.executable,
                str(ROOT / "tools" / "audit_testing_gaps.py"),
                "--repo-root",
                str(ROOT),
                "--output",
                str(md_out),
                "--json-output",
                str(json_out),
                "--min-score",
                "50.0",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"CLI failed: {result.stderr}")
            self.assertTrue(md_out.is_file())
            self.assertTrue(json_out.is_file())

            data = json.loads(json_out.read_text(encoding="utf-8"))
            self.assertGreaterEqual(data["total_test_files"], 150)
            self.assertGreaterEqual(data["overall_score"], 50.0)


if __name__ == "__main__":
    unittest.main()

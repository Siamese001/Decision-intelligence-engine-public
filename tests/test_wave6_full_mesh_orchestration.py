"""
Wave 6 Acceptance Test Suite: End-to-End A2A Orchestration, CI Progress Gates & Release Authority.
Validates:
  1. The Seven Non-Negotiable Engineering Laws across Pipelines A -> B -> C -> D.
  2. Production of the 3 Canonical Delivery Artifacts (01, 02, 03) under project_nexus/MM-DD-YYYY/.
  3. Pre-push automatic evaluation gate integration with tqdm progress bars.
  4. Mandatory GraphDB mutation breakdown table and active generation hash transition.
  5. Figma 4K visual governance verification.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from evals.evaluate_full_mesh_a_to_d_live import FullMeshAtoDLiveEvaluator
from tools.run_automatic_evaluation_gates import run_gate


class TestSevenNonNegotiableLaws(unittest.TestCase):
    """Verifies each of the Seven Non-Negotiable Engineering Laws across the full mesh."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.output_dir = Path(self.temp_dir) / "eval_sandbox"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.evaluator = FullMeshAtoDLiveEvaluator(
            model="gpt-5.6-luna",
            reasoning_effort="high",
            mock_mode=True,
            output_dir=self.output_dir,
        )
        self.mesh_result = self.evaluator.run_full_mesh_pipeline()
        self.envelope = self.mesh_result["audit_envelope"]

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_law_1_physical_compute_fork(self) -> None:
        """Law 1: Physical compute fork before extraction partitions client vs advisory data."""
        self.assertTrue(self.envelope["laws_verified"]["Law 1 (Physical Compute Fork)"])

    def test_law_2_internal_advisory_zero_egress(self) -> None:
        """Law 2: Governed INTERNAL_ADVISORY namespace remains local with zero cloud egress."""
        self.assertTrue(self.envelope["laws_verified"]["Law 2 (INTERNAL_ADVISORY Zero Egress)"])

    def test_law_3_untrusted_fast_path_badge(self) -> None:
        """Law 3: Ephemeral live STT context receives [UNTRUSTED_FAST_PATH] badge and is read-only."""
        self.assertTrue(self.envelope["laws_verified"]["Law 3 (Untrusted Fast Path Badge)"])

    def test_law_4_immutable_claim_check_pointers(self) -> None:
        """Law 4: Immutable provenance first via ClaimCheckPointer bounded SHA256 digests."""
        self.assertTrue(self.envelope["laws_verified"]["Law 4 (Immutable Claim-Check Pointers)"])

    def test_law_5_multidimensional_soft_quarantine(self) -> None:
        """Law 5: Multi-dimensional soft-quarantine preserves candidate nodes without destructive deletion."""
        self.assertTrue(self.envelope["laws_verified"]["Law 5 (Multi-Dimensional Soft-Quarantine)"])

    def test_law_6_pre_retrieval_policy_gatekeeper(self) -> None:
        """Law 6: Pre-retrieval policy gatekeeper suppresses advisory records from client-safe queries."""
        self.assertTrue(self.envelope["laws_verified"]["Law 6 (Pre-Retrieval Policy Gatekeeper)"])

    def test_law_7_invariant_pipeline_sequencing(self) -> None:
        """Law 7: Invariant pipeline sequencing (Evidence -> Canonical Graph Delta -> Executive Briefing)."""
        self.assertTrue(self.envelope["laws_verified"]["Law 7 (Invariant Pipeline Sequencing)"])


class TestThreeCanonicalArtifacts(unittest.TestCase):
    """Verifies generation, content, and formatting of Artifacts 01, 02, and 03."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.output_dir = Path(self.temp_dir) / "eval_sandbox"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.evaluator = FullMeshAtoDLiveEvaluator(
            model="gpt-5.6-luna",
            reasoning_effort="high",
            mock_mode=True,
            output_dir=self.output_dir,
        )
        self.mesh_result = self.evaluator.run_full_mesh_pipeline()
        self.artifacts = self.mesh_result["audit_envelope"]["delivered_artifacts"]

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_artifact_01_hitl_resolved_transcript(self) -> None:
        """Output 01: Forensic transcript with speaker attribution and Ambiguity Lock Register."""
        p = Path(self.artifacts["01_hitl_resolved_transcript"])
        self.assertTrue(p.is_file())
        content = p.read_text(encoding="utf-8")
        self.assertIn("Output 01: Forensic Resolved Transcript", content)
        self.assertIn("09/12/2026", content)
        self.assertIn("Ambiguity Lock Register", content)
        self.assertIn("Content Digest", content)

    def test_artifact_02_executive_recap(self) -> None:
        """Output 02: Email-ready executive recap with strict MM/DD/YYYY dates and actionable next steps."""
        p = Path(self.artifacts["02_executive_recap"])
        self.assertTrue(p.is_file())
        content = p.read_text(encoding="utf-8")
        self.assertIn("Output 02: Executive Recap", content)
        self.assertIn("09/12/2026", content)
        self.assertIn("Meeting Objective", content)
        self.assertIn("Confirmed Decisions", content)
        self.assertIn("Actionable Next Steps", content)
        self.assertIn("Apex Global Enterprises", content)
        self.assertIn("PartnerAdvisory", content)

    def test_artifact_03_canonical_signal_projection(self) -> None:
        """Output 03: Canonical graph delta projection with closed kind enum and classification passthrough."""
        p = Path(self.artifacts["03_canonical_signal_projection"])
        self.assertTrue(p.is_file())
        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], "1.0.0")
        self.assertEqual(data["date"], "09/12/2026")
        self.assertIn("canonical_mutations", data)
        self.assertIn("evidence_statements", data)
        for stmt in data["evidence_statements"]:
            self.assertIn(stmt["kind"], ("summary", "decision", "risk", "dependency", "technical", "strategic"))
            self.assertEqual(stmt["observed_at"], "09/12/2026")


class TestGraphDBMutationBreakdownTable(unittest.TestCase):
    """Verifies mandatory GraphDB mutation breakdown table and active generation transition."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.output_dir = Path(self.temp_dir) / "eval_sandbox"
        self.evaluator = FullMeshAtoDLiveEvaluator(mock_mode=True, output_dir=self.output_dir)
        self.mesh_result = self.evaluator.run_full_mesh_pipeline()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_granular_delta_table_structure(self) -> None:
        """Verifies Granular Graph DB Delta Table accounts for all mandated tables and sidecars."""
        table = self.mesh_result["graph_db_delta_table"]
        self.assertIn("### 1. Granular Graph DB Delta (Table-by-Table)", table)
        for tbl in (
            "`node`",
            "`source`",
            "`source_version`",
            "`extraction_run`",
            "`evidence_item`",
            "`assertion`",
            "`assertion_evidence`",
            "`search_document`",
            "`search_fts`",
            "`audit_event`",
            "`dense_vector` (sidecar)",
            "`sparse_weight` (sidecar)",
            "`colbert_token` (sidecar)",
            "`retrieval_record` (sidecar)",
        ):
            self.assertIn(tbl, table, f"Missing table in delta summary: {tbl}")

    def test_itemized_assertions_table(self) -> None:
        """Verifies Itemized Assertions Table contains subject, predicate, claim, and status."""
        table = self.mesh_result["itemized_assertions_table"]
        self.assertIn("### 2. Itemized Assertions", table)
        self.assertIn("Assertion ID", table)
        self.assertIn("Subject Node", table)
        self.assertIn("Predicate", table)
        self.assertIn("CONFIRMED", table)

    def test_active_hash_transition(self) -> None:
        """Verifies active generation and hash transition certifies positive mutation."""
        trans = self.mesh_result["hash_transition"]
        self.assertEqual(trans["state"], "ACTIVATED")
        self.assertTrue(trans["positive_operations_verified"])
        self.assertNotEqual(trans["graph_sha256_before"], trans["graph_sha256_after"])
        self.assertNotEqual(trans["sidecar_sha256_before"], trans["sidecar_sha256_after"])


class TestPrePushAutomaticEvaluationGateWithProgress(unittest.TestCase):
    """Verifies automatic pre-push gate runner with Full Mesh live integration."""

    def test_run_automatic_gates_with_full_mesh(self) -> None:
        """Executes run_gate with include_full_mesh=True and verifies overall PASS status."""
        report = run_gate(
            repository_root=ROOT,
            include_full_mesh=True,
            progress=False,
        )
        self.assertEqual(report["status"], "PASS")
        self.assertIn("full_mesh_evaluation", report)
        self.assertEqual(report["full_mesh_evaluation"]["status"], "PASS")
        self.assertTrue(report["full_mesh_evaluation"]["figma_4k_qa_passed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

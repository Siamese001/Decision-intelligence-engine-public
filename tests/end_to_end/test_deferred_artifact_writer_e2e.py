"""End-to-end test verifying DeferredArtifactWriter (Wave 7) in QueryEngine lifecycle.

Verifies:
1. QueryEngine ask() with defer_artifacts=True defers intermediate writes and flushes all artifacts to disk upon completion.
2. All mandatory forensic stage artifacts exist in run_dir and output_dir with valid JSON contents and formatting.
3. Parity between defer_artifacts=True and defer_artifacts=False baseline query execution.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.cli.engine import _backend  # noqa: E402
from dual_pipeline.cli.model_resolution import auto_resolve_backend_config  # noqa: E402
from dual_pipeline.core import read_json  # noqa: E402
from dual_pipeline.query import QueryEngine  # noqa: E402
from tests.l2_fakes import FakeProviderFactory  # noqa: E402


@pytest.mark.e2e
class TestDeferredArtifactWriterE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        runtime_root = ROOT / ".local" / "dual_pipeline"
        if not runtime_root.exists():
            runtime_root = ROOT.parent.parent / ".local" / "dual_pipeline"
        cls.runtime_root = runtime_root
        active_pair_path = runtime_root / "active_pair.json"
        if not active_pair_path.is_file():
            cls.has_active_pair = False
            return

        cls.has_active_pair = True
        cls.active_pair = read_json(active_pair_path)
        cls.graph_path = runtime_root / cls.active_pair["graph_path"]

    def setUp(self) -> None:
        if not self.has_active_pair or not self.graph_path.is_file():
            self.skipTest("Active runtime pair not available for E2E artifact writer test")
        self.temp_dirs: list[tempfile.TemporaryDirectory[str]] = []
        self.created_run_dirs: list[Path] = []

    def tearDown(self) -> None:
        for run_dir in self.created_run_dirs:
            if run_dir.exists():
                shutil.rmtree(run_dir, ignore_errors=True)
        for td in self.temp_dirs:
            td.cleanup()

    def _get_backend(self) -> Any:
        args = argparse.Namespace(
            runtime_root=self.runtime_root,
            model_path="BAAI/bge-m3",
            model_manifest=None,
            model_revision=None,
            embedding_backend="bge-m3-dev",
            allow_dev_bge_m3=True,
            allow_internal_operational=False,
            internal_operational_policy=None,
        )
        auto_resolve_backend_config(args)
        return _backend(args)

    def test_e2e_query_with_deferred_artifacts_persists_all_files(self) -> None:
        """Verify that running QueryEngine with defer_artifacts=True flushes all artifacts to disk."""
        backend = self._get_backend()
        td = tempfile.TemporaryDirectory()
        self.temp_dirs.append(td)
        output_dir = Path(td.name) / "query_output"

        engine = QueryEngine(
            self.runtime_root,
            self.graph_path,
            backend,
            provider_factory=FakeProviderFactory(),
            defer_artifacts=True,
        )
        engine.mock_mode = True

        question = "What is the architectural governance and investment decision for Project Nexus?"
        user_id = f"test-deferred-{time.time_ns()}"

        res = engine.ask(
            question,
            query_type="cross_workstream_synthesis",
            destination="NEE_DECISION_INTELLIGENCE",
            delivery_profile="client_executive",
            materiality="high",
            audience="internal_consulting,nee-engineering",
            user_id=user_id,
            output_dir=output_dir,
        )

        run_id = res.get("run_id")
        self.assertIsNotNone(run_id)
        run_dir = self.runtime_root / "runs" / str(run_id)
        self.created_run_dirs.append(run_dir)

        self.assertIn(res.get("status"), {"COMPLETED", "ROUTED", "BLOCKED"})

        # Verify all staged intermediate and terminal artifacts were flushed to run_dir
        expected_run_artifacts = [
            "runtime_binding.json",
            "u0_validation.json",
            "query_destination_decision.json",
            "query_plan.json",
            "query_coverage_manifest.json",
            "route_decision.json",
            "retrieval_evidence_report.json",
            "retrieval_evidence.md",
            "evidence_packet.json",
            "x_gates.json",
            "query_summary.json",
            "answer.md",
        ]
        for artifact_name in expected_run_artifacts:
            path = run_dir / artifact_name
            self.assertTrue(path.is_file(), f"Missing expected artifact {artifact_name} in {run_dir}")
            self.assertGreater(path.stat().st_size, 0, f"Artifact {artifact_name} is empty")
            if artifact_name.endswith(".json"):
                data = read_json(path)
                self.assertIsInstance(data, dict, f"Artifact {artifact_name} did not contain valid JSON dict")

        # Verify output_dir artifacts mirrored accurately
        self.assertTrue(output_dir.is_dir())
        expected_output_artifacts = [
            "runtime_binding.json",
            "answer.json",
            "query_plan.json",
            "retrieval_evidence_report.json",
            "evidence_packet.json",
            "retrieval_evidence.md",
            "answer.md",
        ]
        for artifact_name in expected_output_artifacts:
            path = output_dir / artifact_name
            self.assertTrue(path.is_file(), f"Missing mirrored artifact {artifact_name} in {output_dir}")
            self.assertGreater(path.stat().st_size, 0)

    def test_e2e_deferred_vs_immediate_parity(self) -> None:
        """Verify identical query result parity between defer_artifacts=True and False."""
        backend = self._get_backend()

        engine_deferred = QueryEngine(
            self.runtime_root,
            self.graph_path,
            backend,
            provider_factory=FakeProviderFactory(),
            defer_artifacts=True,
        )
        engine_deferred.mock_mode = True

        engine_immediate = QueryEngine(
            self.runtime_root,
            self.graph_path,
            backend,
            provider_factory=FakeProviderFactory(),
            defer_artifacts=False,
        )
        engine_immediate.mock_mode = True

        question = "What is the architectural governance and investment decision for Project Nexus?"

        # Unique user IDs to avoid cache replay
        uid_def = f"parity-def-{time.time_ns()}"
        uid_imm = f"parity-imm-{time.time_ns()}"

        res_def = engine_deferred.ask(
            question,
            query_type="cross_workstream_synthesis",
            destination="NEE_DECISION_INTELLIGENCE",
            delivery_profile="client_executive",
            materiality="high",
            audience="internal_consulting,nee-engineering",
            user_id=uid_def,
        )
        if res_def.get("run_id"):
            self.created_run_dirs.append(self.runtime_root / "runs" / str(res_def["run_id"]))

        res_imm = engine_immediate.ask(
            question,
            query_type="cross_workstream_synthesis",
            destination="NEE_DECISION_INTELLIGENCE",
            delivery_profile="client_executive",
            materiality="high",
            audience="internal_consulting,nee-engineering",
            user_id=uid_imm,
        )
        if res_imm.get("run_id"):
            self.created_run_dirs.append(self.runtime_root / "runs" / str(res_imm["run_id"]))

        # Parity assertions
        self.assertEqual(res_def.get("status"), res_imm.get("status"))
        self.assertEqual(res_def.get("destination"), res_imm.get("destination"))
        self.assertEqual(res_def.get("eligibility_status"), res_imm.get("eligibility_status"))
        self.assertEqual(res_def.get("approved_record_count"), res_imm.get("approved_record_count"))
        self.assertEqual(res_def.get("dense_executed"), res_imm.get("dense_executed"))
        self.assertEqual(res_def.get("sparse_executed"), res_imm.get("sparse_executed"))


if __name__ == "__main__":
    unittest.main()

"""
tests/test_uwg_rejects_mock_mutations.py

Verifies that Universal Write Gateway, release policy gates, and publish helpers
fail closed against attempts to mutate the knowledge graph using mock synthesis,
offline providers, or short-circuited evaluations.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.canonical_pipeline.publish_helper import publish_graph_if_requested
from dual_pipeline.release_policy import verify_authoritative_release_gate
from dual_pipeline.uwg_core.gateway import UniversalWriteGateway


class TestUWGRejectsMockMutations(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.out_p = Path(self.temp_dir.name)
        self.intake_pkg_path = self.out_p / "intake_package.json"
        self.intake_pkg_path.write_text(
            json.dumps({
                "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
                "package_id": "pkg-test-mock-001",
            }),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_publish_helper_rejects_offline_provider_metadata(self) -> None:
        metadata = {"provider": "offline", "project": "project_nexus"}
        with self.assertRaises(RuntimeError) as ctx:
            publish_graph_if_requested(
                mutate_graph=True,
                intake_pkg_path=self.intake_pkg_path,
                metadata=metadata,
                written={"03_graph": str(self.out_p / "03_graph.json")},
                directive_id="dir-test",
                reviewer="tester",
                rationale="test",
                embedding_backend="bge-m3-dev",
            )
        self.assertIn("MOCK_SYNTHESIS_NOT_AUTHORITATIVE", str(ctx.exception))

    def test_publish_helper_rejects_is_mock_metadata(self) -> None:
        metadata = {"is_mock": True, "project": "project_nexus"}
        with self.assertRaises(RuntimeError) as ctx:
            publish_graph_if_requested(
                mutate_graph=True,
                intake_pkg_path=self.intake_pkg_path,
                metadata=metadata,
                written={"03_graph": str(self.out_p / "03_graph.json")},
                directive_id="dir-test",
                reviewer="tester",
                rationale="test",
                embedding_backend="bge-m3-dev",
            )
        self.assertIn("MOCK_SYNTHESIS_NOT_AUTHORITATIVE", str(ctx.exception))

    def test_publish_helper_rejects_short_circuit_decision(self) -> None:
        decision_file = self.out_p / "final_release_decision.json"
        decision_file.write_text(
            json.dumps({"short_circuit": True, "short_circuit_reason": "DETERMINISTIC_QA_COLLAPSE"}),
            encoding="utf-8",
        )
        metadata = {"provider": "llm", "project": "project_nexus"}
        with self.assertRaises(RuntimeError) as ctx:
            publish_graph_if_requested(
                mutate_graph=True,
                intake_pkg_path=self.intake_pkg_path,
                metadata=metadata,
                written={"03_graph": str(self.out_p / "03_graph.json")},
                directive_id="dir-test",
                reviewer="tester",
                rationale="test",
                embedding_backend="bge-m3-dev",
            )
        self.assertIn("MOCK_SYNTHESIS_NOT_AUTHORITATIVE", str(ctx.exception))

    def test_publish_helper_rejects_mock_judge_evaluation(self) -> None:
        (self.out_p / "final_release_decision.json").write_text(
            json.dumps({"status": "PASS", "decision": "RELEASE"}),
            encoding="utf-8",
        )
        judge_file = self.out_p / "llm_judge_evaluation.json"
        judge_file.write_text(
            json.dumps({"evaluator_model": "mock", "is_mock": True}),
            encoding="utf-8",
        )
        metadata = {"provider": "llm", "project": "project_nexus"}
        with self.assertRaises(RuntimeError) as ctx:
            publish_graph_if_requested(
                mutate_graph=True,
                intake_pkg_path=self.intake_pkg_path,
                metadata=metadata,
                written={"03_graph": str(self.out_p / "03_graph.json")},
                directive_id="dir-test",
                reviewer="tester",
                rationale="test",
                embedding_backend="bge-m3-dev",
            )
        self.assertIn("MOCK_SYNTHESIS_NOT_AUTHORITATIVE", str(ctx.exception))

    def test_release_policy_gate_rejects_short_circuit_decision(self) -> None:
        decision_file = self.out_p / "final_release_decision.json"
        decision_file.write_text(
            json.dumps({
                "authoritative_write_authorized": True,
                "short_circuit": True,
                "short_circuit_reason": "DETERMINISTIC_QA_COLLAPSE",
                "output_3_llm_score_pass": True,
            }),
            encoding="utf-8",
        )
        judge_file = self.out_p / "llm_judge_evaluation.json"
        judge_file.write_text(
            json.dumps({
                "short_circuit": True,
                "short_circuit_reason": "DETERMINISTIC_QA_COLLAPSE",
            }),
            encoding="utf-8",
        )
        with self.assertRaises(RuntimeError) as ctx:
            verify_authoritative_release_gate([self.intake_pkg_path])
        self.assertIn("MOCK_SYNTHESIS_NOT_AUTHORITATIVE", str(ctx.exception))
        self.assertIn("Evaluation was short-circuited", str(ctx.exception))

    def test_release_policy_gate_rejects_mock_judge(self) -> None:
        decision_file = self.out_p / "final_release_decision.json"
        decision_file.write_text(
            json.dumps({
                "authoritative_write_authorized": True,
                "output_3_llm_score_pass": True,
            }),
            encoding="utf-8",
        )
        judge_file = self.out_p / "llm_judge_evaluation.json"
        judge_file.write_text(
            json.dumps({
                "evaluator_model": "mock",
                "is_mock": True,
            }),
            encoding="utf-8",
        )
        with self.assertRaises(RuntimeError) as ctx:
            verify_authoritative_release_gate([self.intake_pkg_path])
        self.assertIn("MOCK_SYNTHESIS_NOT_AUTHORITATIVE", str(ctx.exception))
        self.assertIn("Judge report is from a mock provider", str(ctx.exception))

    def test_uwg_gateway_process_commit_rejects_mock(self) -> None:
        mock_gateway = MagicMock(spec=UniversalWriteGateway)
        mock_gateway._dispatch_learning_request.return_value = None
        mock_gateway._assert_not_mock.side_effect = UniversalWriteGateway._assert_not_mock
        # Call the unbound method on a dummy or invoke process_commit_request logic
        request = {"is_mock": True, "run_id": "test-mock-run"}
        with self.assertRaises(RuntimeError) as ctx:
            UniversalWriteGateway.process_commit_request(mock_gateway, request)
        self.assertIn("MOCK_SYNTHESIS_NOT_AUTHORITATIVE", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

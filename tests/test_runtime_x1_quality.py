"""End-to-end query controls for challenger and X1 judge routing."""
from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from dual_pipeline.core import digest, read_json
from dual_pipeline.query import QueryEngine
from dual_pipeline.release import ReleaseManager
from dual_pipeline.x1_quality import (
    QualityProviderRequest,
    QualityProviderResponse,
    QualityUsage,
    X1QualityMode,
    load_x1_quality_config,
)
from tests.embedding_fixture import BgeM3TestBackend
from tests.l2_fakes import FakeProviderFactory

ROOT = Path(__file__).resolve().parents[1]


class RuntimeQualityProvider:
    provider_id = "google-test-double"

    def __init__(self, factory: "RuntimeQualityFactory", model_policy) -> None:
        self.factory = factory
        self.model_policy = model_policy

    def review(self, request: QualityProviderRequest) -> QualityProviderResponse:
        self.factory.requests.append((self.model_policy, request))
        if request.role.value == "challenger":
            output = {
                "verdict": "PASS",
                "defect_codes": [],
                "evidence_refs": [],
                "confidence": "high",
            }
        else:
            output = self.factory.judge_output(request)
        return QualityProviderResponse(
            output=output,
            response_digest=digest(output),
            model=self.model_policy.model,
            usage=QualityUsage(input_tokens=25, output_tokens=10, total_tokens=35),
            latency_ms=4.0,
        )


class RuntimeQualityFactory:
    def __init__(self, *, judge_verdict: str | list[str]) -> None:
        self.judge_verdicts = (
            list(judge_verdict)
            if isinstance(judge_verdict, list)
            else [judge_verdict]
        )
        self.requests: list[tuple[Any, QualityProviderRequest]] = []

    def __call__(self, model_policy):
        return RuntimeQualityProvider(self, model_policy)

    def judge_output(self, request: QualityProviderRequest) -> dict[str, Any]:
        verdict = self.judge_verdicts.pop(0)
        if verdict == "PASS":
            return {
                "verdict": "PASS",
                "defect_codes": [],
                "evidence_refs": [],
                "confidence": "high",
            }
        record_id = request.evidence_packet["approved"][0]["record"]["record_id"]
        return {
            "verdict": verdict,
            "defect_codes": ["CLIENT_JARGON"],
            "evidence_refs": [record_id],
            "confidence": "high",
        }


class RuntimeX1QualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="nee-runtime-x1-quality-")
        cls.runtime = Path(cls.temp.name) / "runtime"
        cls.graph = ROOT / "graph" / "nee_graph.sqlite"
        cls.backend = BgeM3TestBackend()
        ReleaseManager(cls.runtime, cls.graph).bootstrap(cls.backend)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def _ask(
        self,
        *,
        mode: X1QualityMode,
        judge_verdict: str | list[str],
        most_complex: bool = False,
    ):
        config = replace(
            load_x1_quality_config(),
            mode=mode,
            provider_egress_allowed=True,
        )
        quality_factory = RuntimeQualityFactory(judge_verdict=judge_verdict)
        question = (
            (
                "Synthesize the future-state architecture, functional design, technical "
                "design, A2A patterns, modularity, loose coupling, governance, security, "
                "observability, durable state, failure handling, and extension model. "
            )
            * 8
            + f" Test quality route {str(judge_verdict).lower()}."
            if most_complex
            else (
                "Explain the governed Project Beacon architecture and its decision "
                f"rationale for test quality route {str(judge_verdict).lower()}."
            )
        )
        result = QueryEngine(
            self.runtime,
            self.graph,
            self.backend,
            provider_factory=FakeProviderFactory(),
            x1_quality_config=config,
            quality_provider_factory=quality_factory,
        ).ask(
            question,
            query_type=(
                "cross_workstream_synthesis"
                if most_complex
                else "relationship_explanation"
            ),
            materiality="high",
            audience="internal_consulting,nee-engineering",
        )
        return result, self.runtime / "runs" / result["run_id"], quality_factory

    def test_shadow_judge_records_a_defect_but_does_not_block_delivery(self) -> None:
        result, run_dir, factory = self._ask(
            mode=X1QualityMode.SHADOW,
            judge_verdict="REPAIRABLE_FAIL",
        )
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("X3D_ALLOW_FINISH", result["x3_outcome"])
        self.assertEqual(1, result["challenger_call_count"])
        self.assertEqual(1, result["judge_call_count"])
        self.assertEqual(3, result["total_external_model_call_count"])
        self.assertEqual("REPAIRABLE_FAIL", result["x1_quality"]["judge"]["verdict"])
        self.assertTrue(result["x1_quality"]["judge_release_passed"])
        self.assertEqual(
            ["gemini-3.8-flash", "gemini-3.8-flash"],
            [policy.model for policy, _ in factory.requests],
        )
        gates = read_json(run_dir / "x_gates.json")
        self.assertEqual("PASS", gates["X1"]["status"])
        self.assertFalse(gates["X1"]["runtime_quality"]["judge_required_for_release"])
        self.assertTrue((run_dir / "challenger_review.json").is_file())
        self.assertTrue((run_dir / "judge_review.json").is_file())

    def test_enforced_judge_routes_nonpass_to_hitl(self) -> None:
        result, run_dir, factory = self._ask(
            mode=X1QualityMode.ENFORCED,
            judge_verdict="ESCALATE_HITL",
        )
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("X3B_ESCALATE_HITL", result["x3_outcome"])
        self.assertEqual(1, result["challenger_call_count"])
        self.assertEqual(1, result["judge_call_count"])
        self.assertFalse(result["x1_quality"]["judge_release_passed"])
        self.assertEqual("ESCALATE_HITL", result["x1_quality"]["judge"]["verdict"])
        self.assertEqual(2, len(factory.requests))
        gates = read_json(run_dir / "x_gates.json")
        self.assertEqual("FAIL", gates["X1"]["status"])
        self.assertTrue(gates["X3"]["judge_enforcement_failed"])
        self.assertEqual("X3B_ESCALATE_HITL", gates["X3"]["outcome"])

    def test_enhanced_repairable_verdict_gets_one_repair_and_one_rejudge(self) -> None:
        result, run_dir, factory = self._ask(
            mode=X1QualityMode.ENFORCED,
            judge_verdict=["REPAIRABLE_FAIL", "PASS"],
            most_complex=True,
        )
        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("X3D_ALLOW_FINISH", result["x3_outcome"])
        self.assertEqual("enhanced", result["assurance_profile"])
        self.assertEqual("gpt-5.6-luna", result["selected_model"])
        self.assertEqual(2, result["external_l2_call_count"])
        self.assertEqual(1, result["content_repair_call_count"])
        self.assertEqual(2, result["judge_call_count"])
        self.assertEqual(1, result["challenger_call_count"])
        self.assertEqual(5, result["total_external_model_call_count"])
        self.assertTrue(result["x1_quality"]["judge_release_passed"])
        repair = read_json(run_dir / "content_repair.json")
        self.assertEqual("PASS", repair["status"])
        self.assertEqual("CONTENT_REPAIR", repair["call"]["call_purpose"])
        self.assertEqual("gpt-5.6-luna", repair["call"]["model"])
        self.assertEqual("max", repair["call"]["reasoning_effort"])
        self.assertEqual(
            ["gemini-3.8-flash", "gemini-3.8-flash", "gemini-3.8-flash"],
            [policy.model for policy, _ in factory.requests],
        )
        gates = read_json(run_dir / "x_gates.json")
        self.assertEqual("PASS", gates["X1"]["status"])
        self.assertEqual("PASS", gates["X1"]["runtime_quality"]["content_repair_status"])

    def test_most_complex_second_repairable_verdict_escalates_without_a_second_repair(self) -> None:
        result, run_dir, factory = self._ask(
            mode=X1QualityMode.ENFORCED,
            judge_verdict=["REPAIRABLE_FAIL", "REPAIRABLE_FAIL"],
            most_complex=True,
        )
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("X3B_ESCALATE_HITL", result["x3_outcome"])
        self.assertEqual(1, result["content_repair_call_count"])
        self.assertEqual(2, result["judge_call_count"])
        self.assertFalse(result["x1_quality"]["judge_release_passed"])
        self.assertEqual(3, len(factory.requests))
        repair = read_json(run_dir / "content_repair.json")
        self.assertEqual("PASS", repair["status"])


if __name__ == "__main__":
    unittest.main()

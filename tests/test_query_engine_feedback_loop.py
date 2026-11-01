"""Integration tests for L1 -> L2 -> L1 feedback loop in QueryEngine."""
from __future__ import annotations

import dataclasses
import tempfile
import unittest
from pathlib import Path
from typing import Any

from dual_pipeline.core import digest, read_json
from dual_pipeline.l1_review_contracts import L1ReviewVerdict
from dual_pipeline.l2 import CallPurpose, L2ProviderRequest
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


class TestQualityProvider:
    provider_id = "test-quality-provider"

    def __init__(self, factory: "TestQualityFactory", model_policy: Any) -> None:
        self.factory = factory
        self.model_policy = model_policy

    def review(self, request: QualityProviderRequest) -> QualityProviderResponse:
        output = {
            "verdict": "PASS",
            "defect_codes": [],
            "evidence_refs": [],
            "confidence": "high",
        }
        return QualityProviderResponse(
            output=output,
            response_digest=digest(output),
            model=self.model_policy.model,
            usage=QualityUsage(input_tokens=20, output_tokens=10, total_tokens=30),
            latency_ms=2.0,
        )


class TestQualityFactory:
    def __call__(self, model_policy: Any) -> TestQualityProvider:
        return TestQualityProvider(self, model_policy)


class QueryEngineFeedbackLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory(prefix="nee-query-feedback-test-")
        cls.runtime = Path(cls.temp.name) / "runtime"
        cls.graph = ROOT / "graph" / "nee_graph.sqlite"
        cls.backend = BgeM3TestBackend()
        ReleaseManager(cls.runtime, cls.graph).bootstrap(cls.backend)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def _build_engine(self, provider_factory: FakeProviderFactory) -> QueryEngine:
        config = dataclasses.replace(
            load_x1_quality_config(),
            mode=X1QualityMode.SHADOW,
            provider_egress_allowed=True,
        )
        return QueryEngine(
            self.runtime,
            self.graph,
            self.backend,
            provider_factory=provider_factory,
            x1_quality_config=config,
            quality_provider_factory=TestQualityFactory(),
        )

    def test_single_pass_sufficient_query_proceeds_to_gates(self) -> None:
        """Complete observation on first attempt passes post-L2 review with zero replans."""
        engine = self._build_engine(FakeProviderFactory())
        question = "Explain the governed Project Beacon architecture and its decision rationale."
        result = engine.ask(
            question,
            query_type="relationship_explanation",
            materiality="high",
            audience="internal_consulting,nee-engineering",
            user_id="user-single-pass",
        )
        self.assertEqual("COMPLETED", result["status"])
        run_dir = self.runtime / "runs" / result["run_id"]

        review_file = run_dir / "l1_post_l2_review.json"
        self.assertTrue(review_file.is_file(), "l1_post_l2_review.json must be written to run_dir")
        review_data = read_json(review_file)
        self.assertEqual(L1ReviewVerdict.SUFFICIENT.value, review_data["verdict"])
        self.assertTrue(review_data["deterministic_passed"])
        self.assertTrue(review_data["semantic_passed"])
        self.assertEqual(0, review_data["semantic_reasoning"]["iteration"])

    def test_insufficient_answer_triggers_replanning_and_reexecution(self) -> None:
        """Partial answer triggers L1 replan, updates plan, re-executes, and succeeds."""
        attempt_count = 0

        def dynamic_response(request: L2ProviderRequest, factory: FakeProviderFactory) -> dict[str, Any]:
            nonlocal attempt_count
            attempt_count += 1
            out = factory.valid_output(request)
            if attempt_count == 1:
                # First attempt: partial answer omitting architecture
                out["rationale"] = "Partial summary omitting architecture and missing key details across workstreams."
                return out
            # Second attempt: comprehensive answer addressing the re-plan focus
            return out

        provider_factory = FakeProviderFactory(
            overrides={CallPurpose.PRIMARY: [dynamic_response, dynamic_response]}
        )
        engine = self._build_engine(provider_factory)
        question = "Explain the governed Project Beacon architecture and its decision rationale."
        result = engine.ask(
            question,
            query_type="relationship_explanation",
            materiality="high",
            audience="internal_consulting,nee-engineering",
            user_id="user-replan-pass",
        )
        self.assertEqual("COMPLETED", result["status"])
        run_dir = self.runtime / "runs" / result["run_id"]

        # Verify iteration 0 review recorded the need for replan
        iter0_file = run_dir / "l1_post_l2_review_iter_0.json"
        self.assertTrue(iter0_file.is_file(), "Iter 0 review artifact must exist")
        iter0_data = read_json(iter0_file)
        self.assertEqual(L1ReviewVerdict.INSUFFICIENT_REPLAN.value, iter0_data["verdict"])

        # Verify replan plan artifact was generated
        replan_file = run_dir / "replan_plan_iter_1.json"
        self.assertTrue(replan_file.is_file(), "Replan plan must be written for iteration 1")
        replan_plan = read_json(replan_file)
        self.assertEqual(1, replan_plan.get("replan_iteration"))

        # Verify final review passed on iteration 1
        final_file = run_dir / "l1_post_l2_review.json"
        final_data = read_json(final_file)
        self.assertEqual(L1ReviewVerdict.SUFFICIENT.value, final_data["verdict"])
        self.assertEqual(1, final_data["semantic_reasoning"]["iteration"])

    def test_loop_budget_exhaustion_halts_at_max_iterations(self) -> None:
        """Consistently insufficient answers halt at max_iterations bound (2)."""
        def persistent_partial(request: L2ProviderRequest, factory: FakeProviderFactory) -> dict[str, Any]:
            out = factory.valid_output(request)
            out["rationale"] = "Partial summary omitting architecture and missing key details across workstreams."
            return out

        provider_factory = FakeProviderFactory(
            overrides={
                CallPurpose.PRIMARY: [
                    persistent_partial,
                    persistent_partial,
                    persistent_partial,
                    persistent_partial,
                ]
            }
        )
        engine = self._build_engine(provider_factory)
        question = "Explain the governed Project Beacon architecture and its decision rationale."
        result = engine.ask(
            question,
            query_type="relationship_explanation",
            materiality="high",
            audience="internal_consulting,nee-engineering",
            user_id="user-budget-exhaustion",
        )
        run_dir = self.runtime / "runs" / result["run_id"]

        iter0_file = run_dir / "l1_post_l2_review_iter_0.json"
        iter1_file = run_dir / "l1_post_l2_review_iter_1.json"
        iter2_file = run_dir / "l1_post_l2_review_iter_2.json"
        self.assertTrue(iter0_file.is_file())
        self.assertTrue(iter1_file.is_file())
        self.assertTrue(iter2_file.is_file())

        # Final review should reflect iteration 2
        iter2_data = read_json(iter2_file)
        self.assertEqual(2, iter2_data["semantic_reasoning"]["iteration"])
        self.assertEqual(L1ReviewVerdict.INSUFFICIENT_REPLAN.value, iter2_data["verdict"])

    def test_contract_canonical_digest_verification(self) -> None:
        """Verifies review contract satisfies RFC-8259 canonical digest integrity."""
        engine = self._build_engine(FakeProviderFactory())
        question = "Explain the governed Project Beacon architecture."
        result = engine.ask(
            question,
            query_type="relationship_explanation",
            materiality="high",
            audience="internal_consulting,nee-engineering",
            user_id="user-replan-pass",
        )
        run_dir = self.runtime / "runs" / result["run_id"]
        review_data = read_json(run_dir / "l1_post_l2_review.json")

        digest_entry = review_data["canonical_digest"]
        self.assertEqual("sha256", digest_entry["algorithm"])
        body_to_hash = {k: v for k, v in review_data.items() if k != "canonical_digest"}
        self.assertEqual(digest(body_to_hash), digest_entry["value"])


if __name__ == "__main__":
    unittest.main()

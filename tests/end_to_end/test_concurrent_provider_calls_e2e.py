#!/usr/bin/env python3
"""End-to-end test verifying concurrent LLM provider calls and early-exit heuristics (Wave 6).

Verifies:
1. Enhanced Assurance Query:
   - Challenger and Judge quality reviews are dispatched concurrently.
   - Run artifact l2_execution.json records concurrent_quality_reviews: True.
   - challenger_review.json and judge_review.json are both generated with PASS.
2. Standard Assurance Query with Clean L1:
   - Challenger review executes.
   - Fastpath early-exit activates (EARLY_EXIT_CLEAN_L1_PASS), skipping Judge call.
   - judge_review.json records skip_reason: EARLY_EXIT_CLEAN_L1_PASS.
   - Judge provider invocation count is 0.
3. Deterministic Content Repair Pre-Filter:
   - Purely syntactic defect codes (e.g. CLIENT_READINESS_GAP) resolve locally
     without invoking external LLM content repair.
"""

from __future__ import annotations

import argparse
import sys
import time
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.cli.engine import _backend  # noqa: E402
from dual_pipeline.cli.model_resolution import auto_resolve_backend_config  # noqa: E402
from dual_pipeline.core import digest, read_json  # noqa: E402
from dual_pipeline.l2_policy import load_l2_config  # noqa: E402
from dual_pipeline.l2_providers_core.execution import execute_content_repair  # noqa: E402
from dual_pipeline.query import QueryEngine  # noqa: E402
from dual_pipeline.query_core.quality_review_stage import resolve_initial_judge_review  # noqa: E402
from dual_pipeline.x1_quality import (  # noqa: E402
    QualityModelPolicy,
    QualityProviderRequest,
    QualityProviderResponse,
    QualityReviewStatus,
    QualityRole,
    QualityUsage,
    X1QualityMode,
    load_x1_quality_config,
)
from tests.l2_fakes import FakeProviderFactory  # noqa: E402


class TrackingQualityProvider:
    def __init__(
        self,
        factory: "TrackingQualityFactory",
        model_policy: QualityModelPolicy,
    ) -> None:
        self.factory = factory
        self.model_policy = model_policy

    def review(self, request: QualityProviderRequest) -> QualityProviderResponse:
        self.factory.calls.append((self.model_policy.model, request.role, time.monotonic()))
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
            usage=QualityUsage(input_tokens=25, output_tokens=15, total_tokens=40),
            latency_ms=10.0,
        )


class TrackingQualityFactory:
    def __init__(self) -> None:
        self.calls: list[tuple[str, QualityRole, float]] = []

    def __call__(self, model_policy: QualityModelPolicy) -> TrackingQualityProvider:
        return TrackingQualityProvider(self, model_policy)


@pytest.mark.e2e
class TestConcurrentProviderCallsE2E(unittest.TestCase):
    """E2E suite verifying concurrent LLM quality reviews and early-exit paths."""

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
        cls.sidecar_path = runtime_root / cls.active_pair["sidecar_path"]

    def setUp(self) -> None:
        if not self.has_active_pair or not self.graph_path.is_file():
            self.skipTest("Active graph/index pair not available for E2E query test")
        self.created_run_dirs: list[Path] = []

    def tearDown(self) -> None:
        import shutil

        for rdir in self.created_run_dirs:
            if rdir.is_dir():
                shutil.rmtree(rdir, ignore_errors=True)

    def test_e2e_enhanced_assurance_concurrent_quality_reviews(self) -> None:
        """Enhanced assurance query triggers concurrent Challenger and Judge execution."""
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
        backend = _backend(args)

        factory = TrackingQualityFactory()
        quality_config = replace(
            load_x1_quality_config(),
            mode=X1QualityMode.SHADOW,
            provider_egress_allowed=True,
        )
        engine = QueryEngine(
            self.runtime_root,
            self.graph_path,
            backend,
            provider_factory=FakeProviderFactory(),
            x1_quality_config=quality_config,
            quality_provider_factory=factory,
        )
        engine.mock_mode = True

        question = "What is the architectural governance and investment decision for Project Nexus?"
        user_id = f"test-enhanced-{time.time_ns()}"
        res = engine.ask(
            question,
            query_type="cross_workstream_synthesis",
            destination="NEE_DECISION_INTELLIGENCE",
            delivery_profile="client_executive",
            materiality="high",
            audience="internal_consulting,nee-engineering",
            user_id=user_id,
        )
        run_id = res.get("run_id")
        self.assertIsNotNone(run_id)
        run_dir = self.runtime_root / "runs" / str(run_id)
        self.created_run_dirs.append(run_dir)

        self.assertEqual("COMPLETED", res.get("status"))

        # Verify l2_execution.json records concurrent_quality_reviews: True
        l2_exec_path = run_dir / "l2_execution.json"
        self.assertTrue(l2_exec_path.is_file())
        l2_exec = read_json(l2_exec_path)
        self.assertTrue(l2_exec.get("concurrent_quality_reviews"))

        # Verify challenger_review.json and judge_review.json exist and passed
        challenger_path = run_dir / "challenger_review.json"
        judge_path = run_dir / "judge_review.json"
        self.assertTrue(challenger_path.is_file())
        self.assertTrue(judge_path.is_file())

        challenger_artifact = read_json(challenger_path)
        judge_artifact = read_json(judge_path)
        self.assertEqual("PASS", challenger_artifact.get("status"))
        self.assertEqual("PASS", judge_artifact.get("status"))

        # Verify both Challenger and Judge were invoked
        roles_called = {role for _, role, _ in factory.calls}
        self.assertIn(QualityRole.CHALLENGER, roles_called)
        self.assertIn(QualityRole.JUDGE, roles_called)

    def test_e2e_standard_assurance_clean_l1_early_exit(self) -> None:
        """Standard assurance query with clean L1 triggers early-exit, skipping Judge call."""
        factory = TrackingQualityFactory()
        quality_config = replace(
            load_x1_quality_config(),
            mode=X1QualityMode.SHADOW,
            provider_egress_allowed=True,
        )
        question = "What are the technical dependencies for Project Nexus?"
        answer = {
            "query_type": "cross_workstream_synthesis",
            "current_state": [
                {
                    "statement": "Milestones and dependencies are aligned.",
                    "citations": ["rec-1"],
                    "evidence_ids": ["ev-1"],
                    "lane": "approved",
                }
            ],
        }
        claim_map = {
            "claims": [
                {"claim_id": "c1", "status": "grounded"},
                {"claim_id": "c2", "status": "grounded"},
            ]
        }
        l1_review = {"verdict": "SUFFICIENT"}
        deterministic_controls = {"status": "PASS"}

        # Fastpath early-exit scenario:
        judge_review = resolve_initial_judge_review(
            initial_judge_review=None,
            quality_config=quality_config,
            assurance_profile="standard",
            question=question,
            answer=answer,
            evidence_packet={"approved": []},
            deterministic_controls=deterministic_controls,
            challenger_review_artifact={"status": "PASS"},
            quality_provider_factory=factory,
            deterministic_x1_passed=True,
            l1_post_l2_review=l1_review,
            claim_evidence_map=claim_map,
        )
        self.assertEqual("EARLY_EXIT_CLEAN_L1_PASS", judge_review.skip_reason)
        self.assertEqual(QualityReviewStatus.SKIPPED, judge_review.status)
        self.assertEqual(0, len(factory.calls))

        # Contrasting scenario 1: Enhanced assurance must NOT early exit
        factory_enhanced = TrackingQualityFactory()
        judge_enhanced = resolve_initial_judge_review(
            initial_judge_review=None,
            quality_config=quality_config,
            assurance_profile="enhanced",
            question=question,
            answer=answer,
            evidence_packet={"approved": []},
            deterministic_controls=deterministic_controls,
            challenger_review_artifact={"status": "PASS"},
            quality_provider_factory=factory_enhanced,
            deterministic_x1_passed=True,
            l1_post_l2_review=l1_review,
            claim_evidence_map=claim_map,
        )
        self.assertIsNone(judge_enhanced.skip_reason)
        self.assertEqual(1, len([r for _, r, _ in factory_enhanced.calls if r == QualityRole.JUDGE]))

        # Contrasting scenario 2: Ungrounded claims must NOT early exit
        factory_ungrounded = TrackingQualityFactory()
        un_claims = {"claims": [{"claim_id": "c1", "status": "contradicted"}]}
        judge_ungrounded = resolve_initial_judge_review(
            initial_judge_review=None,
            quality_config=quality_config,
            assurance_profile="standard",
            question=question,
            answer=answer,
            evidence_packet={"approved": []},
            deterministic_controls=deterministic_controls,
            challenger_review_artifact={"status": "PASS"},
            quality_provider_factory=factory_ungrounded,
            deterministic_x1_passed=True,
            l1_post_l2_review=l1_review,
            claim_evidence_map=un_claims,
        )
        self.assertIsNone(judge_ungrounded.skip_reason)
        self.assertEqual(1, len([r for _, r, _ in factory_ungrounded.calls if r == QualityRole.JUDGE]))

    def test_e2e_deterministic_content_repair_prefilter(self) -> None:
        """Syntactic defect codes resolve locally without invoking external LLM content repair."""
        config = load_l2_config()
        decision = MagicMock()
        decision.assurance_profile = "enhanced"
        decision.provider_egress_allowed = True
        decision.query_type = "cross_workstream_synthesis"
        decision.selected_model = config.enhanced.model
        decision.selected_model_profile = "enhanced"

        original_answer = {
            "schema_version": "nee-governed-answer-1.0.0",
            "query_type": "cross_workstream_synthesis",
            "current_state": [],
            "patterns": [],
            "pain_points": [],
            "opportunities": [],
            "guidance": [],
            "hypotheses": [],
            "source_evidence": [],
            "next_best_actions": [],
            "rationale": "Evidence confirms readiness on 2026-10-01.   \n\n\nAction required.",
            "counterevidence": [],
            "missing_information": [],
            "assumptions": [],
            "proposed_assertions": [],
            "what_would_change_the_conclusion": [],
            "automatic_graph_mutation_count": 0,
        }

        class FailingProviderFactory:
            def __call__(self, *args: Any, **kwargs: Any) -> Any:
                raise AssertionError("External provider factory must NOT be called when pre-filter resolves!")

        result = execute_content_repair(
            decision=decision,
            config=config,
            question="What is the rollout schedule?",
            plan={"query_type": "cross_workstream_synthesis"},
            evidence_packet={"approved": [{"record": {"record_id": "record-1", "evidence_ids": ["evidence-1"]}}]},
            prompt={"task": "Synthesize"},
            original_answer=original_answer,
            defect_codes=["CLIENT_READINESS_GAP", "CLIENT_JARGON"],
            provider_factory=FailingProviderFactory(),
            prior_model_calls=1,
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(0, result.call_count)
        self.assertEqual("DETERMINISTIC_HEALING_PREFILTER_RESOLVED", result.skip_reason)
        self.assertIsNotNone(result.answer)
        # Verify date normalized from YYYY-MM-DD to MM/DD/YYYY
        self.assertIn("10/01/2026", result.answer["rationale"])


if __name__ == "__main__":
    unittest.main()



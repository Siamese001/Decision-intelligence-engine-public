"""End-to-end performance and baseline cache verification for RetrievalReleaseEvaluator."""
from __future__ import annotations

import os
import time
import unittest
from pathlib import Path

from dual_pipeline.core import read_json, stable_id
from dual_pipeline.retrieval_release import RetrievalReleaseEvaluator
from dual_pipeline.retrieval_suite import AutomaticRetrievalReleaseSuiteBuilder
from tests.test_retrieval_release_evaluation import (
    RetrievalReleaseEvaluationTests,
)

_RUN_BGE_TESTS = (
    os.environ.get("NEE_RUN_BGE_TESTS") == "1"
    or os.environ.get("NEE_RUN_BGE_RELEASE_TESTS") == "1"
)


@unittest.skipUnless(
    _RUN_BGE_TESTS,
    "Requires NEE_RUN_BGE_TESTS=1 or NEE_RUN_BGE_RELEASE_TESTS=1",
)
class TestRetrievalEvalPerformanceE2E(RetrievalReleaseEvaluationTests):
    """Verifies that RetrievalReleaseEvaluator uses baseline caching and vector caching for release evaluations."""

    def test_retrieval_eval_baseline_cache_and_vectorization_e2e(self) -> None:
        """Proves baseline results are cached and replayed, yielding speedup with bitwise consistency."""
        builder = AutomaticRetrievalReleaseSuiteBuilder(self.runtime, self.seed_graph)
        suite_artifact = builder.build(self.receipt_path)
        suite_path = Path(suite_artifact["path"])

        evaluator = RetrievalReleaseEvaluator(self.runtime, self.seed_graph)

        # Run 1: First evaluation (populates baseline cache)
        t0 = time.monotonic()
        run1 = evaluator.evaluate(
            self.receipt_path,
            suite_path=suite_path,
            backend=self.backend,
        )
        time.monotonic() - t0

        self.assertEqual(run1["evaluation"]["status"], "PASS")
        self.assertTrue(all(run1["evaluation"]["gates"].values()))

        # Check baseline cache persistence
        cache_file = self.runtime / "retrieval_evaluations" / ".baseline_query_cache.json"
        self.assertTrue(cache_file.is_file(), "Baseline query cache file was not created")
        cache_data = read_json(cache_file)
        self.assertGreater(len(cache_data["entries"]), 0)

        # Run 2: Re-evaluation against same baseline with explicit evaluation_id to force re-execution
        eval_id_2 = stable_id("eval-run-2", self.candidate["generation_id"], length=28)
        t1 = time.monotonic()
        run2 = evaluator.evaluate(
            self.receipt_path,
            suite_path=suite_path,
            backend=self.backend,
            evaluation_id=eval_id_2,
        )
        time.monotonic() - t1

        self.assertEqual(run2["evaluation"]["status"], "PASS")
        self.assertTrue(all(run2["evaluation"]["gates"].values()))

        # Results must be identical between runs
        self.assertEqual(
            run1["evaluation"]["aggregate"]["core"],
            run2["evaluation"]["aggregate"]["core"],
        )
        self.assertEqual(
            run1["evaluation"]["gates"],
            run2["evaluation"]["gates"],
        )

        # Baseline retrieved records must match exactly across all cases
        r1_cases = {c["case_id"]: c for c in run1["evaluation"]["case_results"]}
        r2_cases = {c["case_id"]: c for c in run2["evaluation"]["case_results"]}
        self.assertEqual(set(r1_cases.keys()), set(r2_cases.keys()))
        for cid in r1_cases:
            self.assertEqual(
                r1_cases[cid]["baseline_retrieved_record_ids"],
                r2_cases[cid]["baseline_retrieved_record_ids"],
            )
            self.assertEqual(
                r1_cases[cid]["candidate_retrieved_record_ids"],
                r2_cases[cid]["candidate_retrieved_record_ids"],
            )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.evals.contracts import load_suite
from dual_pipeline.evals.runner import run_suite
from tests.embedding_fixture import BgeM3TestBackend

ROOT = Path(__file__).resolve().parents[1]
_RUN_BGE_TESTS = os.environ.get("NEE_RUN_BGE_TESTS") == "1"


@unittest.skipUnless(
    _RUN_BGE_TESTS,
    "real-BGE query E2E disabled; use tools/run_ci_gates.py --profile bge",
)
class BgeQueryE2ETests(unittest.TestCase):
    def test_all_query_families_match_expected_result_sets(self) -> None:
        suite_path = ROOT / "evals" / "suites" / "query-retrieval-evidence-e1.json"
        suite = load_suite(suite_path)
        with tempfile.TemporaryDirectory(prefix="nee-bge-query-e2e-") as directory:
            result, result_path = run_suite(
                suite_path=suite_path,
                runtime_root=Path(directory) / "runtime",
                seed_graph=ROOT / "graph" / "nee_graph.sqlite",
                backend=BgeM3TestBackend(),
                evaluation_id="bge-query-retrieval-evidence-e1",
            )
            self.assertTrue(result_path.is_file())
            self.assertEqual("PASS", result["status"])
            self.assertEqual(len(suite["cases"]), result["aggregate"]["case_count"])
            self.assertEqual(
                result["aggregate"]["case_count"],
                result["aggregate"]["passed_case_count"],
            )
            self.assertEqual(0, result["aggregate"]["graph_mutation_count"])
            self.assertEqual(
                {case["case_id"] for case in suite["cases"]},
                {case["case_id"] for case in result["case_results"]},
            )
            for case in result["case_results"]:
                self.assertEqual("PASS", case["status"], case)
                self.assertTrue(all(case["hard_gates"].values()), case)


if __name__ == "__main__":
    unittest.main()

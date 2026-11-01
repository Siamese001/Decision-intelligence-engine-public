"""Tests for benchmark evaluator nomenclature and shadow facade elimination.

Verifies:
1. Redundant root shadow facade `pipeline_c_retrieval.py` is eliminated, and `pipeline_c_retrieval`
   package resolves cleanly.
2. Canonical domain benchmark evaluators (`AcousticBenchmarkEvaluator`, `ExtractionBenchmarkEvaluator`,
   `RetrievalBenchmarkEvaluator`, `DecisionBenchmarkEvaluator`) are exported and identical to
   their legacy aliases.
3. Domain benchmark scorecards match legacy scorecards.
"""
import unittest
from pathlib import Path

from dual_pipeline.evals.pipelines import (
    AcousticBenchmarkEvaluator,
    AcousticBenchmarkScorecard,
    DecisionBenchmarkEvaluator,
    DecisionBenchmarkScorecard,
    ExtractionBenchmarkEvaluator,
    ExtractionBenchmarkScorecard,
    PipelineADEvalMasterReport,
    PipelineAEvalScorecard,
    PipelineAEvaluator,
    PipelineBEvalScorecard,
    PipelineBEvaluator,
    PipelineCEvalScorecard,
    PipelineCEvaluator,
    PipelineDEvalScorecard,
    PipelineDEvaluator,
    RetrievalBenchmarkEvaluator,
    RetrievalBenchmarkScorecard,
    UnifiedBenchmarkMasterReport,
)


class TestBenchmarkEvaluatorNomenclature(unittest.TestCase):
    """Verify canonical benchmark evaluator nomenclature and aliases."""

    def test_root_shadow_facade_eliminated(self) -> None:
        """Root file `pipeline_c_retrieval.py` must NOT exist; package must resolve."""
        repo_root = Path(__file__).resolve().parent.parent
        shadow_file = repo_root / "pipeline_c_retrieval.py"
        package_dir = repo_root / "pipeline_c_retrieval"

        self.assertFalse(shadow_file.exists(), f"Shadow file {shadow_file} must not exist")
        self.assertTrue(package_dir.is_dir(), f"Package directory {package_dir} must exist")

        import pipeline_c_retrieval as pcr
        self.assertTrue(hasattr(pcr, "PolicyGatedRetrievalEngine"))
        self.assertTrue(hasattr(pcr, "CLIENT_SAFE_NAMESPACE"))

    def test_evaluator_class_identity(self) -> None:
        """Canonical domain evaluators must be identical to legacy pipeline evaluators."""
        self.assertIs(AcousticBenchmarkEvaluator, PipelineAEvaluator)
        self.assertIs(ExtractionBenchmarkEvaluator, PipelineBEvaluator)
        self.assertIs(RetrievalBenchmarkEvaluator, PipelineCEvaluator)
        self.assertIs(DecisionBenchmarkEvaluator, PipelineDEvaluator)

    def test_scorecard_class_identity(self) -> None:
        """Canonical scorecards must be identical to legacy pipeline scorecards."""
        self.assertIs(AcousticBenchmarkScorecard, PipelineAEvalScorecard)
        self.assertIs(ExtractionBenchmarkScorecard, PipelineBEvalScorecard)
        self.assertIs(RetrievalBenchmarkScorecard, PipelineCEvalScorecard)
        self.assertIs(DecisionBenchmarkScorecard, PipelineDEvalScorecard)
        self.assertIs(UnifiedBenchmarkMasterReport, PipelineADEvalMasterReport)

    def test_instantiation_under_canonical_name(self) -> None:
        """Evaluator instantiates cleanly using canonical domain name."""
        acoustic_eval = AcousticBenchmarkEvaluator(enrolled_speakers=["Alice", "Bob"])
        self.assertIsNotNone(acoustic_eval)
        self.assertEqual(acoustic_eval.enrolled_speakers, ["Alice", "Bob"])


if __name__ == "__main__":
    unittest.main()

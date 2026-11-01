"""
tests/end_to_end/test_document_ingestion_qualification_e2e.py

Wave 6 Lifecycle Tests:
Verify qualification of the complete document ingestion path:
- Benchmark manifest schema and predecessor receipt verification.
- Warm/cold distribution percentiles and metric calculation.
- Source coverage verification (all 30 items preserved).
- Differentiation between novel ingestion and zero-call replay.
- Bounded execution and target qualification.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.document_acceptance_core.benchmark_runner import (
    calculate_distribution_metrics,
    execute_bounded_benchmark,
    validate_benchmark_manifest,
)

ROOT = Path(__file__).resolve().parents[2]


class Wave6LifecycleTests(unittest.TestCase):
    def test_benchmark_manifest_validation_lifecycle(self) -> None:
        """Verify that benchmark manifest schema and predecessor receipts validate cleanly."""
        manifest_path = ROOT / "reports/lens_runtime/benchmark_manifest.json"
        manifest = validate_benchmark_manifest(manifest_path, ROOT)
        self.assertEqual(manifest["approved_run_count"], 30)
        self.assertIn("wave_4", manifest["preceding_receipts"])
        self.assertIn("wave_5", manifest["preceding_receipts"])

    def test_distribution_metrics_calculation(self) -> None:
        """Verify calculation of distribution metrics across cold, warm and replay observations."""
        sample_obs = [
            {"mode": "cold", "elapsed_seconds": 1.20, "model_calls": 2, "reasoning_tokens": 1500, "cost_usd": 0.0},
            {"mode": "warm", "elapsed_seconds": 0.25, "model_calls": 1, "reasoning_tokens": 750, "cost_usd": 0.0},
            {"mode": "warm", "elapsed_seconds": 0.30, "model_calls": 1, "reasoning_tokens": 750, "cost_usd": 0.0},
            {"mode": "warm", "elapsed_seconds": 0.35, "model_calls": 1, "reasoning_tokens": 750, "cost_usd": 0.0},
            {"mode": "replay", "elapsed_seconds": 0.01, "model_calls": 0, "reasoning_tokens": 0, "cost_usd": 0.0},
        ]
        metrics = calculate_distribution_metrics(sample_obs)
        self.assertEqual(metrics["sample_count"], 5)
        self.assertEqual(metrics["cold"]["count"], 1)
        self.assertEqual(metrics["warm"]["count"], 3)
        self.assertEqual(metrics["warm"]["median"], 0.30)
        self.assertEqual(metrics["replay"]["count"], 1)
        self.assertEqual(metrics["total_model_calls"], 5)
        self.assertEqual(metrics["total_reasoning_tokens"], 3750)

    def test_bounded_benchmark_execution(self) -> None:
        """Verify bounded benchmark execution produces valid observations and summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_benchmark = Path(tmpdir) / "benchmark.json"
            manifest_path = ROOT / "reports/lens_runtime/benchmark_manifest.json"
            result = execute_bounded_benchmark(manifest_path, out_benchmark, ROOT)

            self.assertEqual(result["terminal_outcome"], "PASS")
            self.assertTrue(result["performance_targets_met"])
            self.assertGreaterEqual(len(result["observations"]), 10)
            self.assertTrue(out_benchmark.is_file())

    def test_complete_source_coverage_assertion(self) -> None:
        """Verify that qualification path enforces complete DOCX source preservation."""
        from dual_pipeline.document_pipeline_core.docx_coverage import extract_docx_with_coverage
        docx_path = ROOT / "documents/project_lens/project-scope-items-for-review.docx"
        text, coverage = extract_docx_with_coverage(docx_path.read_bytes())
        self.assertEqual(coverage["table_count"], 1)
        self.assertEqual(coverage["row_count"], 31)
        self.assertEqual(coverage["cell_count"], 155)

    def test_replay_versus_novel_ingestion_distinction(self) -> None:
        """Verify that replay runs are explicitly distinguished from novel runs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            out_benchmark = Path(tmpdir) / "benchmark_dist.json"
            manifest_path = ROOT / "reports/lens_runtime/benchmark_manifest.json"
            result = execute_bounded_benchmark(manifest_path, out_benchmark, ROOT)

            replays = [o for o in result["observations"] if o["mode"] == "replay"]
            novels = [o for o in result["observations"] if o["mode"] in ("cold", "warm")]

            self.assertGreater(len(replays), 0)
            for r in replays:
                self.assertEqual(r["model_calls"], 0)
                self.assertEqual(r["reasoning_tokens"], 0)

            for n in novels:
                self.assertGreater(n["model_calls"], 0)
                self.assertGreater(n["reasoning_tokens"], 0)

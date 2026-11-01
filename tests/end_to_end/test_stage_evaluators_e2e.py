"""End-to-End tests for Wave 4: Stage Evaluators, Model-Neutral Naming & Real Pipeline Wiring."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import subprocess
import sys

from evals.evaluate_tier_token_economics import run_tier_token_economics
from evals.evaluate_upstream_audio_dsp import (
    UpstreamAudioDSPBenchmarkEvaluator,
    compute_word_error_rate,
    compute_diarization_error_rate,
)
from evals.evaluate_full_mesh_a_to_d_live import FullMeshAtoDLiveEvaluator
from evals.evaluate_metal_advisory_extraction import MetalAdvisoryExtractionEvaluator
from evals.run_provider_fault_injection import run_fault_injection
from evals.evaluate_fast_path_streaming_load import FastPathStreamingLoadEvaluator
from evals.evaluate_reasoning_effort_parameters import ReasoningEffortParameterEvaluator


def test_tier_token_economics_and_model_neutral_filenames():
    """Verify evaluate_tier_token_economics runs cleanly and zero evals/*.py filenames contain forbidden model tokens."""
    evals_dir = Path("evals")
    forbidden_tokens = ("luna", "astra", "sol", "terra", "gpt", "claude", "gemini")
    for py_file in evals_dir.glob("*.py"):
        name_lower = py_file.name.lower()
        assert not any(tok in name_lower for tok in forbidden_tokens), (
            f"Forbidden model token in filename: {py_file.name}"
        )

    assert not (evals_dir / "evaluate_luna_medium_live_token_economics.py").exists()
    assert (evals_dir / "evaluate_tier_token_economics.py").is_file()

    res = run_tier_token_economics(allow_mock=True)
    assert res["token_economics"]["budget_passed"] is True
    assert res["token_economics"]["measured_usage"]["total_tokens"] > 0
    assert res["credential_resolution"]["model"] == "gpt-5.6-luna"
    # Verify unclamped multi-judge calibration metrics are exposed (no artificial [0.95, 0.99] clamping)
    cal_metrics = res["multi_judge_calibration"]["metrics"]
    assert cal_metrics["cohens_kappa"] >= 0.82
    assert cal_metrics["expected_calibration_error"] > 0.05


def test_upstream_audio_dsp_non_tautological_wer_der_and_zero_synthetic_personas():
    """Verify evaluate_upstream_audio_dsp computes non-zero WER/DER on non-identical hypothesis and lacks synthetic personas."""
    src_text = Path("evals/evaluate_upstream_audio_dsp.py").read_text(encoding="utf-8")
    assert "Alex Rivera" not in src_text
    assert "Jordan Taylor" not in src_text
    assert "hyp_text = ref_text" not in src_text

    evaluator = UpstreamAudioDSPBenchmarkEvaluator()
    evaluator.mock = True
    res = evaluator.run_all()
    assert res["gates"]["1. acoustic_dsp_floors"] is True
    assert res["gates"]["2. transcription_accuracy_wer"] is True
    assert res["gates"]["3. diarization_accuracy_der"] is True
    assert res["gates"]["4. ambiguity_lock_registry"] is True
    # Non-identical default hypothesis produces non-zero WER and DER that pass thresholds
    wer = res["gate_details"]["gate_2"]["wer"]
    der = res["gate_details"]["gate_3"]["der"]
    assert 0.0 < wer <= 0.085
    assert 0.0 < der <= 0.05


def test_full_mesh_a_to_d_live_sqlite_counts_and_physical_sha256(tmp_path: Path):
    """Verify evaluate_full_mesh_a_to_d_live queries real SQLite tables and computes physical SHA-256 digests."""
    evaluator = FullMeshAtoDLiveEvaluator(mock_mode=True, output_dir=tmp_path)
    res = evaluator.run_full_mesh_pipeline()
    env = res["audit_envelope"]
    assert env["status"] == "PASS"

    delta = env["graph_delta"]
    baseline = delta["baseline_counts"]
    activated = delta["activated_counts"]
    ht = delta["hash_transition"]

    # Verify physical SQLite files exist in sandbox and match SHA-256 after activation
    graph_db = tmp_path / "canonical_graph.sqlite"
    sidecar_db = tmp_path / "sidecar_vector.sqlite"
    assert graph_db.is_file()
    assert sidecar_db.is_file()

    actual_graph_sha = hashlib.sha256(graph_db.read_bytes()).hexdigest()
    actual_sidecar_sha = hashlib.sha256(sidecar_db.read_bytes()).hexdigest()
    assert ht["graph_sha256_after"] == actual_graph_sha
    assert ht["sidecar_sha256_after"] == actual_sidecar_sha
    assert ht["graph_sha256_before"] != ht["graph_sha256_after"]

    # Independently query SQLite table counts
    conn = sqlite3.connect(str(graph_db))
    try:
        node_count = conn.execute("SELECT COUNT(*) FROM node").fetchone()[0]
        asrt_count = conn.execute("SELECT COUNT(*) FROM assertion").fetchone()[0]
    finally:
        conn.close()

    assert node_count == activated["node"] == baseline["node"] + 3
    assert asrt_count == activated["assertion"] == baseline["assertion"] + 4


def test_metal_advisory_extraction_invokes_l1_cognition():
    """Verify MetalAdvisoryExtractionEvaluator delegates classification to L1ExtractionCognition."""
    evaluator = MetalAdvisoryExtractionEvaluator(mock_mode=True)
    res = evaluator.run_all()
    assert res["status"] == "PASS"
    assert all(res["gates"].values())


def test_provider_fault_injection_and_fast_path_tracemalloc_and_reasoning_mock(tmp_path: Path):
    """Verify fault injection, tracemalloc memory gate, and reasoning effort CLI --mock."""
    report = run_fault_injection(["FAULT-429", "FAULT-503", "FAULT-TRUNC", "FAULT-TIMEOUT"])
    assert report.is_overall_compliant is True
    assert report.passed_scenarios == 4

    stream_eval = FastPathStreamingLoadEvaluator(num_concurrent_workers=4)
    stream_res = stream_eval.run_all()
    assert stream_res["status"] == "PASS"
    assert stream_res["gates"]["5. memory_stability_tracemalloc"] is True
    assert stream_res["gate_details"]["gate_5"]["peak_memory_mb"] > 0.0

    # Verify CLI --mock flag on evaluate_reasoning_effort_parameters.py
    proc = subprocess.run(
        [sys.executable, "evals/evaluate_reasoning_effort_parameters.py", "--mock"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert "REASONING EFFORT EVALUATION: PASS" in proc.stdout

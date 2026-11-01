"""Wave 3 Acceptance Tests: Controlled Local Diarization Comparison & Benchmark Verification.

Verifies:
1. Benchmark receipt exists and passes zero-paid-API invariant.
2. Finalist configuration (Variant 4) exceeds the >= 95% turn coverage gate (M/N).
3. Speaker confusion reduction vs baseline exceeds 75% (achieved 89.8%).
4. Frame-accurate DER calculation via evals.evaluate_upstream_audio_dsp.
5. Channel-aware local mic processing achieves zero speaker confusion.
6. Primary research documentation on Pyannote 3.1, Plaquet/Bredin, and Bain et al. is complete.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from evals.evaluate_upstream_audio_dsp import compute_diarization_error_rate

ROOT_DIR = Path(__file__).resolve().parents[2]
REPORTS_DIR = ROOT_DIR / "reports"
RECEIPT_FILE = REPORTS_DIR / "lens_diarization_variant_benchmark_receipt.json"
RESEARCH_DOC = ROOT_DIR / "docs/research/wave3_diarization_primary_research.md"


@pytest.fixture
def benchmark_receipt() -> dict:
    assert RECEIPT_FILE.exists(), f"Benchmark receipt missing at {RECEIPT_FILE}"
    with open(RECEIPT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def test_wave3_benchmark_receipt_exists_and_valid(benchmark_receipt: dict):
    assert benchmark_receipt["benchmark_id"] == "bench_lens_diarization_variants_20261006"
    assert benchmark_receipt["runtime_environment"] == "Apple Silicon Metal (MPS / CoreAudio)"
    assert benchmark_receipt["paid_api_calls_incurred"] == 0
    assert benchmark_receipt["remote_transcription_calls"] == 0
    assert benchmark_receipt["ground_truth_census_N"] == 426
    assert len(benchmark_receipt["variants_evaluated"]) == 4


def test_wave3_finalist_exceeds_coverage_target(benchmark_receipt: dict):
    fin = benchmark_receipt["finalist_recommendation"]
    assert fin["selected_variant"] == "Variant 4: Channel-Aware + Voice Anchor Calibration (Finalist)"
    assert fin["turn_coverage_pct"] >= 95.0, f"Turn coverage {fin['turn_coverage_pct']}% below 95% gate"
    assert fin["speaker_confusion_reduction_vs_v1_pct"] >= 75.0


def test_wave3_channel_aware_local_mic_zero_confusion(benchmark_receipt: dict):
    v3 = next(v for v in benchmark_receipt["variants_evaluated"] if "Variant 3" in v["variant"])
    # Channel-aware isolation of Ch0 local mic must produce zero confusion on local speech
    assert v3["speaker_confusion_turns"] == 0
    assert v3["turn_coverage_pct"] >= 80.0


def test_wave3_sample_accurate_der_computation():
    sample_rate = 16000
    total_samples = sample_rate * 20

    ref_turns = [
        (0, sample_rate * 10, "Amit Ayer"),
        (sample_rate * 10, sample_rate * 20, "Jake Stark"),
    ]
    # Identical hypothesis
    assert compute_diarization_error_rate(ref_turns, ref_turns, total_samples) == 0.0

    # Confused hypothesis (Jake called Jennifer)
    hyp_confused = [
        (0, sample_rate * 10, "Amit Ayer"),
        (sample_rate * 10, sample_rate * 20, "Jennifer Pan"),
    ]
    der = compute_diarization_error_rate(ref_turns, hyp_confused, total_samples)
    assert pytest.approx(der, rel=1e-2) == 0.5


def test_wave3_primary_research_documentation_complete():
    assert RESEARCH_DOC.exists(), f"Primary research document missing at {RESEARCH_DOC}"
    content = RESEARCH_DOC.read_text(encoding="utf-8")
    assert "Pyannote Speaker Diarization 3.1" in content
    assert "Plaquet & Bredin" in content
    assert "Powerset multi-class cross entropy" in content
    assert "Bain et al." in content
    assert "WhisperX" in content

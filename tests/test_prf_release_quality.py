"""Tests for PRF release quality gates, negative controls, and dependency invalidation (Wave 4)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.transcript_qa_core.evaluator import evaluate_judge_and_release
from dual_pipeline.transcript_qa_core.frozen_gates import (
    apply_frozen_to_qa_report,
    check_frozen_dependency_validity,
)
from dual_pipeline.transcript_qa_core.gate1 import run_qa_gate_1
from dual_pipeline.transcript_qa_core.gate1_helpers import (
    _is_generic_speaker,
    compute_released_text_coverage,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PRF_DIR = REPO_ROOT / "output/nea/10-06-2026/Prf_Scope_Sow_Clement_PartnerAdvisory"


def test_generic_speaker_aliases_cannot_count_as_named():
    """Generic placeholders, company labels, and aliases must not count as named persons."""
    generic_samples = [
        "Speaker",
        "Unknown",
        "Unknown Speaker",
        "Speaker Unknown",
        "Client Speaker",
        "[Client Speaker]",
        "PartnerAdvisory Speaker",
        "[PartnerAdvisory Speaker]",
        "External Speaker",
        "Partner Speaker",
        "Client Team",
        "PartnerAdvisory Team",
        "Voice",
        "Unresolved",
        "SPEAKER_01",
        "Speaker 2",
        "Participant",
    ]
    for spk in generic_samples:
        assert _is_generic_speaker(spk), f"Expected '{spk}' to be recognized as generic speaker"

    named_samples = [
        "Amit Ayer",
        "Clement",
        "Noemi Monnerville",
        "Venkat",
        "Calvin Gilmartin",
        "Paulette Kwasniak",
        "India Thomas",
        "Shreyak Gupta",
    ]
    for spk in named_samples:
        assert not _is_generic_speaker(spk), f"Expected '{spk}' to be recognized as named speaker"


def test_exact_bad_prf_artifact_rejected():
    """Exact released 411-turn PRF artifact must fail Gate 1 and coverage thresholds."""
    f1_path = PRF_DIR / "01_Prf_Scope_Sow_Clement_PartnerAdvisory_10-06-2026_0341PM0408PMET_hitl_resolved_transcript.md"
    meta_path = PRF_DIR / "metadata.json"
    assert f1_path.exists(), f"Missing PRF artifact at {f1_path}"
    assert meta_path.exists(), f"Missing PRF metadata at {meta_path}"

    f1_text = f1_path.read_text(encoding="utf-8")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    # Verify text coverage calculation
    cov = compute_released_text_coverage(f1_text)
    assert cov["total_displayed_turns"] == 411
    assert cov["generic_turns"] == 166
    assert cov["named_turns"] == 245
    assert cov["released_dialogue_words"] == 3126
    assert cov["generic_words"] == 296
    assert cov["released_named_words"] == 2830
    assert cov["named_word_coverage_pct"] == 90.53
    assert cov["released_coverage_gate_status"] == "FAIL"

    # Run deterministic Gate 1
    g1_res = run_qa_gate_1(f1_text, source_text=f1_text, metadata=meta)
    assert g1_res["passed"] is False
    assert g1_res["status"] == "FAIL"

    # Check Gate 1.2
    g1_2 = g1_res["gates"]["Gate_1_2_Structured_Attribution_Hygiene"]
    assert g1_2["passed"] is False
    assert "Client Speaker" in g1_2["unresolved_speakers"]

    # Check Gate 1.7 fails closed on unauthenticated operator approval claim
    g1_7 = g1_res["gates"]["Gate_1_7_Ambiguity_Lock_Register_And_Speaker_HITL"]
    assert g1_7["passed"] is False


def test_missing_accuracy_evidence_blocks_authoritative_release():
    """Missing independent 98% accuracy evidence yields NOT_EVALUATED and blocks write."""
    f1_path = PRF_DIR / "01_Prf_Scope_Sow_Clement_PartnerAdvisory_10-06-2026_0341PM0408PMET_hitl_resolved_transcript.md"
    f2_path = PRF_DIR / "02_Prf_Scope_Sow_Clement_PartnerAdvisory_10-06-2026_0341PM0408PMET_executive_recap.md"
    f3_path = PRF_DIR / "03_Prf_Scope_Sow_Clement_PartnerAdvisory_10-06-2026_0341PM0408PMET_canonical_signal_projection.json"
    meta_path = PRF_DIR / "metadata.json"

    f1_text = f1_path.read_text(encoding="utf-8")
    f2_text = f2_path.read_text(encoding="utf-8")
    f3_text = f3_path.read_text(encoding="utf-8")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    qa_rep, judge_rep, decision = evaluate_judge_and_release(
        f1_text=f1_text,
        f2_text=f2_text,
        f3_text=f3_text,
        source_text=f1_text,
        metadata=meta,
        mock=True,
    )
    assert decision["authoritative_write_authorized"] is False
    assert decision["final_pass"] is False
    assert decision["status"] == "FAIL"
    assert decision["independent_accuracy_status"] == "NOT_EVALUATED"


def test_stale_recap_invalidates_on_upstream_identity_change():
    """Changing Noemi Monnerville to PartnerAdvisory invalidates frozen recap with Contractor."""
    f2_path = PRF_DIR / "02_Prf_Scope_Sow_Clement_PartnerAdvisory_10-06-2026_0341PM0408PMET_executive_recap.md"
    f2_text = f2_path.read_text(encoding="utf-8")

    # Contradiction: Noemi Monnerville is PartnerAdvisory, but recap says Contractor
    valid, err = check_frozen_dependency_validity(
        "output_2",
        f2_text,
        None,
        {"identity_corrections": [{"person": "Noemi Monnerville", "company": "PartnerAdvisory"}]},
    )
    assert valid is False
    assert "Contractor" in (err or "")

    # Contradiction: Venkat surname guess
    fake_recap = "# 02 Executive Recap\n\nKrishnakumar Venkataraman presented the architecture.\n"
    valid_guess, err_guess = check_frozen_dependency_validity("output_2", fake_recap, None, {})
    assert valid_guess is False
    assert "Krishnakumar Venkataraman" in (err_guess or "")


def test_apply_frozen_to_qa_report_thaws_on_dependency_mismatch():
    """apply_frozen_to_qa_report marks stage as FAIL and thaws when dependency invalidates."""
    class MockStateMachine:
        def __init__(self, content: str):
            self.content = content
            self.frozen = {"output_2"}

        def is_frozen(self, k: str) -> bool:
            return k in self.frozen

        def get_frozen_content(self, k: str) -> str:
            return self.content

        def unfreeze_artifact(self, k: str, force: bool = False) -> None:
            self.frozen.discard(k)

    f2_path = PRF_DIR / "02_Prf_Scope_Sow_Clement_PartnerAdvisory_10-06-2026_0341PM0408PMET_executive_recap.md"
    f2_text = f2_path.read_text(encoding="utf-8")
    sm = MockStateMachine(f2_text)

    qa_report = {
        "output_01_forensic_transcript": {"status": "PASS", "passed": True},
        "output_02_executive_recap": {"status": "PENDING", "passed": False},
        "output_03_canonical_signal_projection": {"status": "PASS", "passed": True},
        "cross_artifact_consistency": {"status": "PASS"},
    }

    apply_frozen_to_qa_report(
        qa_report,
        sm,
        metadata={"identity_corrections": [{"person": "Noemi Monnerville", "company": "PartnerAdvisory"}]},
    )
    assert qa_report["output_02_executive_recap"]["passed"] is False
    assert qa_report["output_02_executive_recap"]["status"] == "FAIL"
    assert "output_2" not in sm.frozen

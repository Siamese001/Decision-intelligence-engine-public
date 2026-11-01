"""
tests/test_output_quality_wave5.py

Wave 5 Acceptance Suite: Release integrity and replay.
Covers:
1. Saved contradictory APEX_TRANS receipt fails closed on status/verdict and aggregate score contradiction.
2. Saved frozen Conduit negative fixture with tampered content fails closed on SHA mismatch.
3. Repaired judge card replacement recomputes aggregates, summaries, verdicts, and seals.
4. Judge card citations resolve against candidate artifact text; unresolvable citations block release.
5. Package-only isolation strictly forbids authoritative write authorization.
6. Consistent positive triad passes release integrity verification.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.manifest_runner.guard_utils import (
    reconcile_guarded_scores,
    update_final_release_verdicts,
)
from dual_pipeline.release_policy import (
    build_artifact_manifest,
    compute_release_evaluation_digest,
    verify_decision_seal,
)
from dual_pipeline.remediation_core.artifact_validator import (
    validate_frozen_artifact_gate,
)
from dual_pipeline.transcript_qa_core.citation_utils import (
    extract_judge_card_citations,
    validate_judge_card_citations,
    verify_judge_report_integrity,
)
from dual_pipeline.transcript_qa_core.frozen_gates import (
    apply_frozen_to_qa_report,
    check_frozen_dependency_validity,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

NEET_DIR = REPO_ROOT / "output" / "apex_trans" / "09-11-2026"
NEET_F1_PATH = NEET_DIR / "01_NEET_Insights_Hub_Tech_Services_Group_Interview_09-11-2026_0336PM_0437PM_ET_hitl_resolved_transcript.md"
NEET_F2_PATH = NEET_DIR / "02_NEET_Insights_Hub_Tech_Services_Group_Interview_09-11-2026_0336PM_0437PM_ET_executive_recap.md"
NEET_F3_PATH = NEET_DIR / "03_NEET_Insights_Hub_Tech_Services_Group_Interview_09-11-2026_0336PM_0437PM_ET_canonical_signal_projection.json"
NEET_JUDGE_PATH = NEET_DIR / "llm_judge_evaluation.json"
NEET_DECISION_PATH = NEET_DIR / "final_release_decision.json"

CONDUIT_DIR = REPO_ROOT / "output" / "conduit" / "08-31-2026" / "Conduit_Lessons_Learned_Michael_PartnerAdvisory"
CONDUIT_F1_PATH = CONDUIT_DIR / "01_Conduit_Lessons_Learned_Michael_PartnerAdvisory_08-31-2026_0100PM_0147PM_ET_hitl_resolved_transcript.md"
CONDUIT_F2_PATH = CONDUIT_DIR / "02_Conduit_Lessons_Learned_Michael_PartnerAdvisory_08-31-2026_0100PM_0147PM_ET_executive_recap.md"
CONDUIT_F3_PATH = CONDUIT_DIR / "03_Conduit_Lessons_Learned_Michael_PartnerAdvisory_08-31-2026_0100PM_0147PM_ET_canonical_signal_projection.json"


def test_saved_contradictory_neet_receipt_fails():
    """Saved APEX_TRANS receipt has status=PASS but overall_verdict=FAIL and wrong aggregate score; must fail closed."""
    assert NEET_JUDGE_PATH.exists()
    assert NEET_DECISION_PATH.exists()

    with open(NEET_JUDGE_PATH, encoding="utf-8") as f:
        neet_judge = json.load(f)
    with open(NEET_DECISION_PATH, encoding="utf-8") as f:
        neet_decision = json.load(f)

    # 1. Direct integrity verification fails closed
    ok, err = verify_judge_report_integrity(neet_judge, final_decision=neet_decision)
    assert not ok
    assert "Contradictory judge report status" in err or "Contradictory aggregate score" in err

    # 2. Release policy gate check blocks mutation on integrity failure
    from dual_pipeline.release_policy import _check_manifest_and_seal
    with pytest.raises(RuntimeError, match="Release integrity validation failed"):
        _check_manifest_and_seal(neet_decision, NEET_DIR / "intake_package.json", judge_file=NEET_JUDGE_PATH)


def test_saved_frozen_conduit_tampered_fails(tmp_path: Path):
    """Frozen artifact verification must reject content whose SHA differs from the certified manifest."""
    # Create isolated copy of Conduit dir where candidate output_1 file is tampered
    tmp_conduit = tmp_path / "conduit_isolated"
    tmp_conduit.mkdir(parents=True, exist_ok=True)

    # Copy decision and package
    for fname in ("final_release_decision.json", "intake_package.json", "metadata.json"):
        src = CONDUIT_DIR / fname
        if src.exists():
            (tmp_conduit / fname).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    # Write tampered output_1 candidate file
    tampered_f1 = tmp_conduit / CONDUIT_F1_PATH.name
    tampered_f1.write_text("# Injected Tampered Content\nMalicious alteration of frozen transcript.\n", encoding="utf-8")

    # 1. Artifact validator rejects tampered content (expected SHA from certified decision differs from disk)
    res = validate_frozen_artifact_gate(
        output_dir=tmp_conduit,
        artifact_key="output_1",
        metadata={},
    )
    assert not res.valid
    assert "Tampered or modified frozen content" in res.reason

    # 2. frozen_gates check_frozen_dependency_validity rejects mismatch
    cert_sha = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    ok, err = check_frozen_dependency_validity(
        art_key="output_1",
        content="Different content",
        state_machine=None,
        metadata={"output_1_sha256": cert_sha},
    )
    assert not ok
    assert "hash mismatch" in err or "mismatched" in err


def test_repaired_judge_card_replacement_recomputes_aggregates_and_seals():
    """After replacing or monotonic-guarding judge cards, aggregates, summary, verdict, and digests recompute."""
    from dual_pipeline.transcript_qa_core.monotonic_guard import MonotonicGuard

    judge_report = {
        "output_1_evaluation": {"composite_score": 95, "verdict": "PASS"},
        "output_2_evaluation": {"composite_score": 80, "verdict": "FAIL"},
        "output_3_evaluation": {"composite_score": 92, "verdict": "PASS"},
        "cross_artifact_reconciliation": {"status": "PASS", "passed": True},
        "aggregate_composite_score": 50.0,  # Stale aggregate
        "status": "FAIL",
        "overall_verdict": "FAIL",
    }

    guard1 = MonotonicGuard("OUTPUT01")
    guard1.evaluate_candidate(0, 95, "Text 1", judge_report["output_1_evaluation"])

    guard2 = MonotonicGuard("OUTPUT02")
    guard2.evaluate_candidate(0, 92, "Text 2", {"composite_score": 92, "verdict": "PASS"})  # Prior better card

    guard3 = MonotonicGuard("OUTPUT03")
    guard3.evaluate_candidate(0, 92, "Text 3", judge_report["output_3_evaluation"])

    reconcile_guarded_scores(
        judge_report,
        guard1,
        guard2,
        guard3,
        f1_text="Text 1",
        f2_text="Text 2",
        f3_str="Text 3",
    )

    # Output 2 was restored to 92 PASS by guard2
    assert judge_report["output_2_evaluation"]["composite_score"] == 92
    assert judge_report["output_2_evaluation"]["verdict"] == "PASS"

    # Aggregates must be recomputed: (95 + 92 + 92) / 3 = 93.0
    assert abs(judge_report["aggregate_composite_score"] - 93.0) < 0.05
    assert judge_report["status"] == "PASS"
    assert judge_report["overall_verdict"] == "PASS"
    assert "score=95" in judge_report["overall_rubric_summary"]
    assert "evaluation_receipt_digest" in judge_report


def test_judge_card_citation_validation_and_release_rejection():
    """Judge cards with citations absent from candidate text must demote to FAIL and block release."""
    artifact_text = "The system achieved 99.9% uptime during Q3. Primary database migration completed on Sept 15."

    valid_card = {
        "composite_score": 95,
        "verdict": "PASS",
        "grounding_citations": ["99.9% uptime", "Sept 15"],
    }
    ok, missing = validate_judge_card_citations(valid_card, artifact_text)
    assert ok
    assert len(missing) == 0

    hallucinated_card = {
        "composite_score": 95,
        "verdict": "PASS",
        "grounding_citations": ["Deployed Azure Redis cache on Oct 1", "99.9% uptime"],
    }
    ok_bad, missing_bad = validate_judge_card_citations(hallucinated_card, artifact_text)
    assert not ok_bad
    assert "Deployed Azure Redis cache on Oct 1" in missing_bad

    # Testing that update_final_release_verdicts demotes the release on citation error
    judge_rep = {
        "output_1_evaluation": {"composite_score": 95, "verdict": "PASS"},
        "output_2_evaluation": hallucinated_card,
        "output_3_evaluation": {"composite_score": 95, "verdict": "PASS"},
        "cross_artifact_reconciliation": {"status": "PASS", "passed": True},
    }
    final_dec: Dict[str, Any] = {
        "output_1_deterministic_gates_pass": True,
        "output_2_deterministic_gates_pass": True,
        "output_3_deterministic_gates_pass": True,
    }

    authorized = update_final_release_verdicts(
        judge_rep,
        final_dec,
        f1_text="Text 1",
        f2_text=artifact_text,
        f3_text="Text 3",
        model="gpt-5.6-terra",
    )
    assert not authorized
    assert judge_rep["status"] == "FAIL"
    assert not final_dec["final_pass"]
    assert "output_2" in judge_rep.get("citation_errors", {})


def test_package_only_isolation_forbids_authoritative_write():
    """Package-only flag must strictly forbid authoritative write authorization even when all gates pass."""
    judge_rep = {
        "output_1_evaluation": {"composite_score": 95, "verdict": "PASS"},
        "output_2_evaluation": {"composite_score": 95, "verdict": "PASS"},
        "output_3_evaluation": {"composite_score": 95, "verdict": "PASS"},
        "cross_artifact_reconciliation": {"status": "PASS", "passed": True},
    }
    final_dec: Dict[str, Any] = {
        "output_1_deterministic_gates_pass": True,
        "output_2_deterministic_gates_pass": True,
        "output_3_deterministic_gates_pass": True,
    }

    authorized = update_final_release_verdicts(
        judge_rep,
        final_dec,
        f1_text="Valid 1",
        f2_text="Valid 2",
        f3_text="Valid 3",
        model="gpt-5.6-terra",
        metadata={"package_only": True},
    )
    assert not authorized
    assert final_dec["final_pass"] is True
    assert final_dec["authoritative_write_authorized"] is False


def test_consistent_positive_triad_passes_release_integrity():
    """A consistent positive triad satisfies all integrity constraints and seals."""
    f1 = "# Turn 1\n[00:01:00] Speaker A: Hello world\n"
    f2 = "# Executive Summary\nHello world occurred on Sept 11.\n"
    f3 = json.dumps({"entities": ["EntityA"]})

    judge_rep = {
        "output_1_evaluation": {
            "composite_score": 95,
            "verdict": "PASS",
            "grounding_citations": ["Hello world"],
        },
        "output_2_evaluation": {
            "composite_score": 92,
            "verdict": "PASS",
            "grounding_citations": ["Hello world"],
        },
        "output_3_evaluation": {
            "composite_score": 94,
            "verdict": "PASS",
            "grounding_citations": [],
        },
        "cross_artifact_reconciliation": {"status": "PASS", "passed": True},
        "aggregate_composite_score": 93.67,
        "status": "PASS",
        "overall_verdict": "PASS",
    }

    final_dec = {
        "output_1_deterministic_gates_pass": True,
        "output_2_deterministic_gates_pass": True,
        "output_3_deterministic_gates_pass": True,
        "status": "PASS",
        "final_pass": True,
    }

    ok, err = verify_judge_report_integrity(
        judge_rep,
        final_decision=final_dec,
        f1_text=f1,
        f2_text=f2,
        f3_text=f3,
    )
    assert ok, f"Expected integrity pass, got: {err}"

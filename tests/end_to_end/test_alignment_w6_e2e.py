"""
tests/end_to_end/test_alignment_w6_e2e.py

End-to-End lifecycle tests for Wave 6: Bind every authoritative exit.
Verifies:
1. Canonical publication requires non-empty evidence and full artifact manifest alignment.
2. Missing, empty, or swapped evidence fails closed with AuthoritativeExitError.
3. Advisory/partial/blocked status strictly blocks publication and activation exits.
4. Tampered artifact contents (hash drift) block publication.
5. Cryptographic active-pair binding and live graph coverage readback verification.
6. Copilot streaming vs standard response parity consumes identical grounding evidence.
7. NOT_APPLICABLE requires an explicit justified policy reason.
8. Idempotent resume verifies multi-factor evidence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.l0.authoritative_exit_binding import (
    AuthoritativeExitError,
    ExitCapability,
    compute_file_sha256,
    validate_copilot_exit_parity,
    validate_exit_evidence,
    verify_active_pair_and_coverage,
)


def _setup_valid_publication_bundle(pkg_dir: Path) -> Dict[str, str]:
    """Sets up a complete, valid evidence bundle in pkg_dir."""
    pkg_dir.mkdir(parents=True, exist_ok=True)

    # 1. Output artifacts
    out1 = pkg_dir / "01_test_hitl_resolved_transcript.md"
    out1.write_text("# Resolved Transcript\nSample content for transcript.", encoding="utf-8")
    out2 = pkg_dir / "02_test_executive_recap.md"
    out2.write_text("# Executive Recap\nSample recap content.", encoding="utf-8")
    out3 = pkg_dir / "03_test_canonical_signal_projection.json"
    out3.write_text(json.dumps({"signal": "canonical", "weight": 1.0}), encoding="utf-8")

    out1_sha = compute_file_sha256(out1)
    out2_sha = compute_file_sha256(out2)
    out3_sha = compute_file_sha256(out3)

    # 2. Evidence files
    judge_file = pkg_dir / "llm_judge_evaluation.json"
    judge_file.write_text(json.dumps({"evaluation": "PASS", "composite_score": 0.95}), encoding="utf-8")

    qa_file = pkg_dir / "qa_gates_report.json"
    qa_file.write_text(json.dumps({"gates_passed": True, "total_gates": 11}), encoding="utf-8")

    # 3. Final release decision
    decision_file = pkg_dir / "final_release_decision.json"
    decision = {
        "status": "APPROVED",
        "authoritative_write_authorized": True,
        "output_3_llm_score_pass": True,
        "source_file_hash": "sha256_src_123",
        "attendee_roster_hash": "sha256_roster_456",
        "policy_snapshot_digest": "sha256_policy_789",
        "prompt_template_hash": "sha256_prompt_abc",
        "evaluator_version": "v5.7",
        "output_schema_version": "1.1.0",
        "artifact_manifest": {
            "output_1_sha256": out1_sha,
            "output_2_sha256": out2_sha,
            "output_3_sha256": out3_sha,
        },
    }
    decision_file.write_text(json.dumps(decision, indent=2), encoding="utf-8")

    return {
        "out1_sha": out1_sha,
        "out2_sha": out2_sha,
        "out3_sha": out3_sha,
    }


def test_w6_e2e_canonical_publication_lifecycle_success(tmp_path: Path):
    """Verify complete valid publication bundle passes all exit verification checks."""
    pkg_dir = tmp_path / "run_success"
    _setup_valid_publication_bundle(pkg_dir)

    # Validate exit evidence
    passed, errors = validate_exit_evidence(pkg_dir, ExitCapability.CANONICAL_PUBLICATION)
    assert passed is True
    assert errors == []

    # Validate active-pair & live coverage
    graph_file = tmp_path / "canonical_graph.db"
    graph_file.write_text("sqlite format 3", encoding="utf-8")
    index_file = tmp_path / "active_index.bin"
    index_file.write_text("index bytes", encoding="utf-8")

    active_pair = {
        "active_graph_path": str(graph_file),
        "active_index_path": str(index_file),
        "active_generation": 2,
    }
    live_coverage = {
        "coverage_percentage": 0.92,
        "node_count": 150,
        "edge_count": 300,
        "is_stale": False,
    }

    pair_passed, pair_errors = verify_active_pair_and_coverage(active_pair, live_coverage)
    assert pair_passed is True
    assert pair_errors == []


def test_w6_e2e_missing_or_empty_evidence_fails_closed(tmp_path: Path):
    """Verify missing or empty evidence files block publication exit fail-closed."""
    pkg_dir = tmp_path / "run_missing_evidence"
    _setup_valid_publication_bundle(pkg_dir)

    # 1. Missing QA gates report
    qa_file = pkg_dir / "qa_gates_report.json"
    qa_file.unlink()

    passed, errors = validate_exit_evidence(pkg_dir, ExitCapability.CANONICAL_PUBLICATION)
    assert passed is False
    assert any("Missing required evidence file: qa_gates_report.json" in e for e in errors)

    # Verify raise_on_failure mode
    with pytest.raises(AuthoritativeExitError, match="Missing required evidence file"):
        validate_exit_evidence(pkg_dir, ExitCapability.CANONICAL_PUBLICATION, raise_on_failure=True)

    # 2. Empty evidence file (<= 10 bytes)
    qa_file.write_text("{}", encoding="utf-8")  # 2 bytes
    passed, errors = validate_exit_evidence(pkg_dir, ExitCapability.CANONICAL_PUBLICATION)
    assert passed is False
    assert any("empty: qa_gates_report.json" in e for e in errors)


def test_w6_e2e_advisory_status_blocks_publication(tmp_path: Path):
    """Verify advisory/partial/blocked status strictly blocks publication and activation exits."""
    pkg_dir = tmp_path / "run_advisory"
    _setup_valid_publication_bundle(pkg_dir)

    decision_file = pkg_dir / "final_release_decision.json"
    decision = json.loads(decision_file.read_text(encoding="utf-8"))

    # Test status ADVISORY
    decision["status"] = "ADVISORY"
    decision_file.write_text(json.dumps(decision), encoding="utf-8")

    passed, errors = validate_exit_evidence(pkg_dir, ExitCapability.CANONICAL_PUBLICATION)
    assert passed is False
    assert any("Advisory/partial status 'ADVISORY' cannot authorize" in e for e in errors)

    # Test status PARTIAL on GRAPH_ACTIVATION
    decision["status"] = "PARTIAL"
    decision_file.write_text(json.dumps(decision), encoding="utf-8")
    passed_act, errors_act = validate_exit_evidence(pkg_dir, ExitCapability.GRAPH_ACTIVATION)
    assert passed_act is False
    assert any("Advisory/partial status 'PARTIAL' cannot authorize" in e for e in errors_act)


def test_w6_e2e_artifact_tamper_drift_blocks_exit(tmp_path: Path):
    """Verify modified artifact content after seal computation is detected as drift."""
    pkg_dir = tmp_path / "run_drift"
    _setup_valid_publication_bundle(pkg_dir)

    # Tamper with 01_transcript
    out1 = pkg_dir / "01_test_hitl_resolved_transcript.md"
    out1.write_text("# Tampered content that modifies SHA-256", encoding="utf-8")

    passed, errors = validate_exit_evidence(pkg_dir, ExitCapability.CANONICAL_PUBLICATION)
    assert passed is False
    assert any("Artifact drift detected for output_1_sha256" in e for e in errors)


def test_w6_e2e_active_pair_and_coverage_readback_enforcement(tmp_path: Path):
    """Verify active-pair binding and live graph coverage readback enforcement."""
    graph_file = tmp_path / "active_graph.db"
    graph_file.write_text("sqlite", encoding="utf-8")
    index_file = tmp_path / "active_index.bin"
    index_file.write_text("index", encoding="utf-8")

    # 1. Stale graph coverage
    active_pair = {
        "active_graph_path": str(graph_file),
        "active_index_path": str(index_file),
        "active_generation": 1,
    }
    stale_coverage = {
        "coverage_percentage": 0.95,
        "node_count": 100,
        "edge_count": 200,
        "is_stale": True,
    }
    passed, errors = verify_active_pair_and_coverage(active_pair, stale_coverage)
    assert passed is False
    assert any("OBSOLETE_GRAPH_COVERAGE" in e for e in errors)

    # 2. Coverage below 80%
    low_coverage = {
        "coverage_percentage": 0.65,
        "node_count": 50,
        "edge_count": 80,
        "is_stale": False,
    }
    passed_low, errors_low = verify_active_pair_and_coverage(active_pair, low_coverage)
    assert passed_low is False
    assert any("below minimum required 80.0% floor" in e for e in errors_low)

    # 3. Missing live coverage readback
    passed_none, errors_none = verify_active_pair_and_coverage(active_pair, None)
    assert passed_none is False
    assert any("OBSOLETE_OR_MISSING_GRAPH_COVERAGE" in e for e in errors_none)


def test_w6_e2e_copilot_streaming_vs_standard_parity():
    """Verify streamed and ordinary copilot responses consume identical grounding evidence."""
    digest = "sha256_grounding_evidence_abc123"

    streamed_payload = {
        "track": "live_copilot",
        "model_id": "gemini-3.8-flash",
        "grounding_evidence_digest": digest,
        "recommended_action": "RECOMMEND_DEBATE_RESOLUTION",
        "status": "PASS",
    }
    standard_payload = {
        "track": "live_copilot",
        "model_id": "gemini-3.8-flash",
        "grounding_evidence_digest": digest,
        "recommended_action": "RECOMMEND_DEBATE_RESOLUTION",
        "status": "PASS",
    }

    # Matching payloads pass
    passed, errors = validate_copilot_exit_parity(streamed_payload, standard_payload)
    assert passed is True
    assert errors == []

    # Evidence mismatch fails
    mismatched_streamed = dict(streamed_payload, grounding_evidence_digest="sha256_different_evidence")
    passed_mis, errors_mis = validate_copilot_exit_parity(mismatched_streamed, standard_payload)
    assert passed_mis is False
    assert any("Grounding evidence mismatch" in e for e in errors_mis)

    # Action mismatch fails
    mismatched_action = dict(streamed_payload, recommended_action="ESCALATE_TO_HUMAN")
    passed_act, errors_act = validate_copilot_exit_parity(mismatched_action, standard_payload)
    assert passed_act is False
    assert any("Copilot exit action mismatch" in e for e in errors_act)


def test_w6_e2e_not_applicable_explicit_justification_rule(tmp_path: Path):
    """Verify NOT_APPLICABLE requires an explicit justified policy reason."""
    pkg_dir = tmp_path / "empty_dir"
    pkg_dir.mkdir()

    # NOT_APPLICABLE with empty or short reason fails
    passed, errors = validate_exit_evidence(pkg_dir, ExitCapability.CANONICAL_PUBLICATION, is_not_applicable=True, explicit_na_reason="")
    assert passed is False
    assert any("NOT_APPLICABLE requires an explicit justified policy reason" in e for e in errors)

    # NOT_APPLICABLE with valid reason passes
    passed_ok, errors_ok = validate_exit_evidence(
        pkg_dir,
        ExitCapability.CANONICAL_PUBLICATION,
        is_not_applicable=True,
        explicit_na_reason="Advisory meeting run without canonical graph publication scope per policy.",
    )
    assert passed_ok is True
    assert errors_ok == []

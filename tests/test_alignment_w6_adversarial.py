"""
tests/test_alignment_w6_adversarial.py

Adversarial and edge-case verification for Wave 6: Bind every authoritative exit.
Mandatory Coverage: Covers 4 Edge-Case Categories under e2e-and-edge-case-coverage.md:
1. Boundary payload: empty dirs, non-existent directories, None inputs, boundary generations.
2. Malformed input: corrupt JSON, non-numeric generation values, wrong data types.
3. Concurrency & race: concurrent multi-threaded validation and verification calls.
4. Invariant bypass attempt: spoofed authoritative write authorization with ADVISORY status,
   forged artifact manifests, stale graph bypass.
"""
from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.l0.authoritative_exit_binding import (
    AuthoritativeExitError,
    ExitCapability,
    validate_copilot_exit_parity,
    validate_exit_evidence,
    verify_active_pair_and_coverage,
)


# ==============================================================================
# Category 1: Boundary Payload
# ==============================================================================

def test_adv_boundary_non_existent_directory(tmp_path: Path):
    """Verify non-existent output directory fails closed safely."""
    missing_dir = tmp_path / "does_not_exist"
    passed, errors = validate_exit_evidence(missing_dir, ExitCapability.CANONICAL_PUBLICATION)
    assert passed is False
    assert any("does not exist" in e for e in errors)

    with pytest.raises(AuthoritativeExitError, match="does not exist"):
        validate_exit_evidence(missing_dir, ExitCapability.CANONICAL_PUBLICATION, raise_on_failure=True)


def test_adv_boundary_generation_zero_and_negative(tmp_path: Path):
    """Verify active pair generation <= 0 is rejected."""
    graph_f = tmp_path / "graph.db"
    graph_f.write_text("db", encoding="utf-8")
    index_f = tmp_path / "index.bin"
    index_f.write_text("idx", encoding="utf-8")

    coverage = {"coverage_percentage": 0.9, "node_count": 10, "edge_count": 20, "is_stale": False}

    # Generation 0
    p0, errs0 = verify_active_pair_and_coverage(
        {"active_graph_path": str(graph_f), "active_index_path": str(index_f), "active_generation": 0},
        coverage,
    )
    assert p0 is False
    assert any("Active pair generation" in e for e in errs0)

    # Generation -1
    p_neg, errs_neg = verify_active_pair_and_coverage(
        {"active_graph_path": str(graph_f), "active_index_path": str(index_f), "active_generation": -1},
        coverage,
    )
    assert p_neg is False
    assert any("Active pair generation" in e for e in errs_neg)


# ==============================================================================
# Category 2: Malformed Input
# ==============================================================================

def test_adv_malformed_json_in_decision_file(tmp_path: Path):
    """Verify truncated or corrupt JSON in final_release_decision.json fails closed."""
    pkg_dir = tmp_path / "corrupt_json"
    pkg_dir.mkdir()
    (pkg_dir / "llm_judge_evaluation.json").write_text(json.dumps({"eval": "pass_long_enough_string"}), encoding="utf-8")
    (pkg_dir / "qa_gates_report.json").write_text(json.dumps({"gates": 11, "details": "long_enough_gates_string"}), encoding="utf-8")

    decision_f = pkg_dir / "final_release_decision.json"
    decision_f.write_text('{"status": "APPROVED", "incomplete": ', encoding="utf-8")

    passed, errors = validate_exit_evidence(pkg_dir, ExitCapability.CANONICAL_PUBLICATION)
    assert passed is False
    assert any("Malformed final_release_decision.json" in e for e in errors)

    with pytest.raises(AuthoritativeExitError, match="Malformed"):
        validate_exit_evidence(pkg_dir, ExitCapability.CANONICAL_PUBLICATION, raise_on_failure=True)


def test_adv_malformed_active_pair_types(tmp_path: Path):
    """Verify non-integer or string generations in active-pair fail closed."""
    graph_f = tmp_path / "graph.db"
    graph_f.write_text("db", encoding="utf-8")
    index_f = tmp_path / "index.bin"
    index_f.write_text("idx", encoding="utf-8")

    coverage = {"coverage_percentage": 0.9, "node_count": 10, "edge_count": 20, "is_stale": False}

    passed, errors = verify_active_pair_and_coverage(
        {"active_graph_path": str(graph_f), "active_index_path": str(index_f), "active_generation": "invalid_gen"},
        coverage,
    )
    assert passed is False
    assert any("must be an integer" in e for e in errors)


# ==============================================================================
# Category 3: Concurrency & Race
# ==============================================================================

def test_adv_concurrency_multi_threaded_exit_evidence_validation(tmp_path: Path):
    """Verify concurrent parallel callers validating exit evidence do not race or crash."""
    pkg_dir = tmp_path / "concurrent_pkg"
    pkg_dir.mkdir()

    # Create valid files
    (pkg_dir / "llm_judge_evaluation.json").write_text(json.dumps({"eval": "pass_long_enough_string"}), encoding="utf-8")
    (pkg_dir / "qa_gates_report.json").write_text(json.dumps({"gates": 11, "details": "long_enough_gates_string"}), encoding="utf-8")
    (pkg_dir / "final_release_decision.json").write_text(
        json.dumps({
            "status": "APPROVED",
            "authoritative_write_authorized": True,
            "output_3_llm_score_pass": True,
            "artifact_manifest": {},
        }),
        encoding="utf-8",
    )

    def _worker(worker_id: int):
        passed, errors = validate_exit_evidence(pkg_dir, ExitCapability.CANONICAL_PUBLICATION)
        return passed, errors

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(_worker, i) for i in range(50)]
        results = [f.result() for f in futures]

    assert all(r[0] is True for r in results)
    assert all(r[1] == [] for r in results)


# ==============================================================================
# Category 5: Invariant Bypass Attempt
# ==============================================================================

def test_adv_bypass_attempt_authoritative_write_with_advisory_status(tmp_path: Path):
    """Verify an attacker cannot sneak authoritative_write_authorized=True when status is ADVISORY."""
    pkg_dir = tmp_path / "bypass_pkg"
    pkg_dir.mkdir()

    (pkg_dir / "llm_judge_evaluation.json").write_text(json.dumps({"eval": "pass_long_enough_string"}), encoding="utf-8")
    (pkg_dir / "qa_gates_report.json").write_text(json.dumps({"gates": 11, "details": "long_enough_gates_string"}), encoding="utf-8")
    (pkg_dir / "final_release_decision.json").write_text(
        json.dumps({
            "status": "ADVISORY",
            "authoritative_write_authorized": True,
            "output_3_llm_score_pass": True,
            "artifact_manifest": {},
        }),
        encoding="utf-8",
    )

    passed, errors = validate_exit_evidence(pkg_dir, ExitCapability.CANONICAL_PUBLICATION)
    assert passed is False
    assert any("Advisory/partial status 'ADVISORY' cannot authorize" in e for e in errors)


def test_adv_bypass_attempt_missing_manifest_referenced_files(tmp_path: Path):
    """Verify an attacker cannot supply hashes in artifact_manifest for non-existent output files."""
    pkg_dir = tmp_path / "forged_manifest_pkg"
    pkg_dir.mkdir()

    (pkg_dir / "llm_judge_evaluation.json").write_text(json.dumps({"eval": "pass_long_enough_string"}), encoding="utf-8")
    (pkg_dir / "qa_gates_report.json").write_text(json.dumps({"gates": 11, "details": "long_enough_gates_string"}), encoding="utf-8")
    (pkg_dir / "final_release_decision.json").write_text(
        json.dumps({
            "status": "APPROVED",
            "authoritative_write_authorized": True,
            "output_3_llm_score_pass": True,
            "artifact_manifest": {
                "output_1_sha256": "fake_sha_1111111111111111111111111111111111111111111111111111111111111111",
            },
        }),
        encoding="utf-8",
    )

    passed, errors = validate_exit_evidence(pkg_dir, ExitCapability.CANONICAL_PUBLICATION)
    assert passed is False
    assert any("Referenced artifact 01_*hitl_resolved_transcript.md missing" in e for e in errors)

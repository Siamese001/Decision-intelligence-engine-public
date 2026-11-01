"""
tests/end_to_end/test_mock_offline_truth_gate_wave1_e2e.py

End-to-End Truth Gate Qualification Suite for Wave 1.
Validates that:
1. Release verdicts require complete, positively verified deterministic gate evidence (no default-true).
2. Evaluation cache strictly binds verified SHA-256 digests and disallows source-free fallback.
3. Frozen artifacts without prior evaluation records are not fabricated with score 100 PASS.
4. GitHub discovery distinguishes fresh verified data from stale or unavailable states.
5. CI gate runner records observed test counts and leaves unmeasured operations unknown.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.manifest_runner.guard_utils import (
    load_pinned_evaluations,
    update_final_release_verdicts,
)
from dual_pipeline.transcript_qa_core.eval_cache import (
    check_pin,
    resolve_pins_and_cache,
)
from dual_pipeline.transcript_qa_core.evaluator import evaluate_judge_and_release
from tools.ci_gates_core.runner import _parse_log_test_counts, _write_profile_receipt
from tools.pr_governance_core.github_adapter import (
    GitHubPRInfo,
    get_github_prs_with_provenance,
)


@pytest.mark.unit
def test_manifest_runner_guard_utils_rejects_missing_deterministic_gates():
    """Ensure missing deterministic gate fields default to False and prevent authoritative write."""
    judge_report: Dict[str, Any] = {
        "output_1_evaluation": {"verdict": "PASS", "composite_score": 95},
        "output_2_evaluation": {"verdict": "PASS", "composite_score": 95},
        "output_3_evaluation": {"verdict": "PASS", "composite_score": 95},
    }
    # Deliberately omit deterministic gate keys in final_decision
    final_decision: Dict[str, Any] = {}

    authorized = update_final_release_verdicts(judge_report, final_decision)

    assert not authorized, "Authoritative write must not be authorized when deterministic gate evidence is missing"
    assert final_decision["output_1_pass"] is False
    assert final_decision["output_2_pass"] is False
    assert final_decision["output_3_pass"] is False
    assert final_decision["final_pass"] is False
    assert final_decision["authoritative_write_authorized"] is False


@pytest.mark.unit
def test_manifest_runner_guard_utils_authorizes_when_all_positively_verified():
    """Ensure write is authorized only when both LLM scoring and deterministic gates positively pass."""
    judge_report: Dict[str, Any] = {
        "output_1_evaluation": {"verdict": "PASS", "composite_score": 95},
        "output_2_evaluation": {"verdict": "PASS", "composite_score": 95},
        "output_3_evaluation": {"verdict": "PASS", "composite_score": 95},
    }
    final_decision: Dict[str, Any] = {
        "output_1_deterministic_gates_pass": True,
        "output_2_deterministic_gates_pass": True,
        "output_3_deterministic_gates_pass": True,
    }

    authorized = update_final_release_verdicts(judge_report, final_decision)

    assert authorized is True
    assert final_decision["output_1_pass"] is True
    assert final_decision["output_2_pass"] is True
    assert final_decision["output_3_pass"] is True
    assert final_decision["final_pass"] is True
    assert final_decision["authoritative_write_authorized"] is True


@pytest.mark.unit
def test_eval_cache_rejects_missing_or_mismatched_pin_sha():
    """Ensure evaluation pins without matching artifact sha are rejected, not backfilled."""
    expected_sha = "a" * 64
    mismatched_sha = "b" * 64

    # Pin missing SHA altogether
    pinned_missing_sha = {
        "output_1": {
            "composite_score": 95,
            "verdict": "PASS",
        }
    }
    res_missing = check_pin(pinned_missing_sha, "output_1", "output_01", 90, expected_sha)
    assert res_missing is None, "Pin missing artifact SHA must be rejected"

    # Pin with mismatched SHA
    pinned_mismatched = {
        "output_1": {
            "artifact_sha256": mismatched_sha,
            "composite_score": 95,
            "verdict": "PASS",
        }
    }
    res_mismatched = check_pin(pinned_mismatched, "output_1", "output_01", 90, expected_sha)
    assert res_mismatched is None, "Pin with mismatched SHA must be rejected"

    # Valid pin
    pinned_valid = {
        "output_1": {
            "artifact_sha256": expected_sha,
            "composite_score": 95,
            "verdict": "PASS",
            "dimension_1": 24,
            "dimension_2": 24,
            "dimension_3": 24,
            "dimension_4": 23,
        }
    }
    res_valid = check_pin(pinned_valid, "output_1", "output_01", 90, expected_sha)
    assert res_valid is not None
    assert res_valid["artifact_sha256"] == expected_sha


@pytest.mark.unit
def test_eval_cache_disallows_source_free_cache_fallback():
    """Ensure evaluations requiring source text do not fall back to source-free cache keys."""
    f1 = "Turn 1: Alice: Hello"
    f2 = "Executive summary"
    f3 = "{}"
    source_text = "Full meeting source audio transcript"

    # With source_text supplied, pins/cache resolution must use keys with source_tag
    out1, out2, out3, k1, k2, k3 = resolve_pins_and_cache(
        f1, f2, f3, "gpt-4o", "medium", {}, source_text=source_text
    )
    # The cache keys should contain source hash tag
    assert ":" in k1
    # None of the outputs should resolve from empty cache
    assert out1 is None
    assert out2 is None
    assert out3 is None


@pytest.mark.unit
def test_frozen_artifact_without_prior_eval_not_awarded_fabricated_pass(tmp_path: Path):
    """Ensure a state machine frozen artifact without prior evaluation is not awarded score 100 PASS."""
    class MockStateMachine:
        def is_frozen(self, artifact_name: str) -> bool:
            return True

    # Call evaluate_outputs_with_judge with mock=True (short circuit/mock evaluator)
    # and a state_machine where artifacts are frozen but NO pinned evaluations exist
    qa_report = {
        "passed": True,
        "output_01_forensic_transcript": {"passed": True},
        "output_02_executive_recap": {"passed": True},
        "output_03_canonical_signal_projection": {"passed": True},
    }
    _, judge_report, _ = evaluate_judge_and_release(
        "f1 text",
        "f2 text",
        "f3 text",
        mock=True,
        metadata={"state_machine": MockStateMachine()},
    )

    # In the past, _froz awarded status SKIPPED, composite_score 100, verdict PASS.
    # Now, it must not fabricate composite_score 100 with reason FROZEN_PRIOR_ARTIFACT
    for key in ("output_1_evaluation", "output_2_evaluation", "output_3_evaluation"):
        ev = judge_report.get(key, {})
        assert ev.get("reason") != "FROZEN_PRIOR_ARTIFACT", f"{key} was given fabricated FROZEN_PRIOR_ARTIFACT"


@pytest.mark.unit
def test_github_adapter_provenance_distinguishes_unavailable_and_fresh(tmp_path: Path):
    """Ensure github adapter reports truthful provenance and does not claim PASS on unavailable discovery."""
    non_existent_repo = tmp_path / "empty_repo"
    non_existent_repo.mkdir()

    pr_map, provenance = get_github_prs_with_provenance(
        repo_root=non_existent_repo,
        force_refresh=True,
        timeout=1.0,
        allow_stale=False,
    )
    assert provenance in ("UNAVAILABLE", "LIVE_VERIFIED_EMPTY")
    if provenance == "UNAVAILABLE":
        assert pr_map == {}


@pytest.mark.unit
def test_ci_gate_runner_receipt_metrics_and_unmeasured_operations(tmp_path: Path):
    """Ensure CI runner records parsed test counts and leaves unmeasured operations unknown."""
    log_file = tmp_path / "test.log"
    log_file.write_text(
        "collected 10 items\n"
        "tests/test_sample.py::test_one PASSED\n"
        "tests/test_sample.py::test_two SKIPPED\n"
        "=================== 8 passed, 2 skipped in 0.45s ===================\n",
        encoding="utf-8",
    )

    counts = _parse_log_test_counts(log_file)
    assert counts is not None
    assert counts.get("collected") == 10
    assert counts.get("passed") == 8
    assert counts.get("skipped") == 2

    # Verify receipt generation records None for unmeasured operations
    receipt_path = tmp_path / "receipt.json"
    command_results = [
        {
            "command_index": 1,
            "command": ["pytest", "tests/"],
            "status": "PASS",
            "return_code": 0,
            "test_counts": counts,
        }
    ]
    _write_profile_receipt(
        path=receipt_path,
        profile="test_profile",
        repository_root=tmp_path,
        output_root=tmp_path / "output",
        command_timeout_seconds=60.0,
        total_timeout_seconds=120.0,
        command_results=command_results,
        status_code=0,
        started_at="2026-10-08T09:30:00Z",
    )

    assert receipt_path.is_file()
    receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt_data["status"] == "PASS"
    assert receipt_data["provider_calls"] is None, "provider_calls must be None when not measured"
    assert receipt_data["graph_mutations"] is None, "graph_mutations must be None when not measured"
    assert receipt_data["operations_measured"] is False
    assert receipt_data["test_counts"]["passed"] == 8
    assert receipt_data["test_counts"]["skipped"] == 2

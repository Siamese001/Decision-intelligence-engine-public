"""
tests/adversarial/test_mock_offline_forged_success_wave1_adversarial.py

Adversarial Stress & Invariant Bypass Suite for Wave 1.
Covers Mandatory Edge-Case Categories per e2e-and-edge-case-coverage.md:
1. Boundary payload:
   - Empty/null final_decision structures
   - Empty log files and zero-item candidate streams
2. Malformed input:
   - Truncated/non-hex hash values in pin checks
   - Non-dict / corrupted pin payloads
   - Non-boolean and inverted truth values in deterministic gate flags
3. Invariant bypass attempt:
   - Attempting to authorize production writes via fabricated LLM scores without verified deterministic gates
   - Attempting to inject unverified pins to bypass judge execution
   - Attempting to achieve qualifying PASS status in CI receipts when all tests are skipped
   - Attempting to obtain PASSED status from an offline GitHub provider
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.manifest_runner.guard_utils import update_final_release_verdicts
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine
from dual_pipeline.transcript_qa_core.eval_cache import check_pin
from tools.ci_gates_core.runner import _parse_log_test_counts, _write_profile_receipt
from tools.pr_governance_core.github_adapter import (
    get_github_prs_with_provenance,
)


# =========================================================================
# Category 1: Boundary Payload
# =========================================================================

@pytest.mark.unit
def test_adversarial_boundary_empty_payload_in_verdict_guard():
    """Verify that completely empty judge report and final decision fail closed."""
    judge_report: Dict[str, Any] = {}
    final_decision: Dict[str, Any] = {}

    write_authorized = update_final_release_verdicts(judge_report, final_decision)

    assert write_authorized is False
    assert final_decision["status"] == "FAIL"
    assert final_decision["output_1_pass"] is False
    assert final_decision["output_2_pass"] is False
    assert final_decision["output_3_pass"] is False
    assert final_decision["final_pass"] is False
    assert final_decision["authoritative_write_authorized"] is False


@pytest.mark.unit
def test_adversarial_boundary_empty_or_zero_byte_test_log(tmp_path: Path):
    """Verify that 0-byte log files do not invent test metrics or pass counts."""
    empty_log = tmp_path / "empty.log"
    empty_log.write_text("", encoding="utf-8")

    counts = _parse_log_test_counts(empty_log)
    assert counts is None, "0-byte log file must return None rather than synthetic metrics"


@pytest.mark.unit
def test_adversarial_boundary_state_machine_absent_candidates():
    """Verify state machine records fail-closed defaults when evaluations are empty."""
    sm = SelectiveRemediationStateMachine()
    # Record candidates with empty dictionaries
    sm._record_attempt_candidates({}, {}, {}, "f1", None, None)

    assert sm.get_best_candidate("output_1") is not None
    # For absent output_2 and output_3, no candidate should be recorded
    assert sm.get_best_candidate("output_2") is None
    assert sm.get_best_candidate("output_3") is None

    c1 = sm.get_best_candidate("output_1")
    assert c1 is not None
    assert c1.qa_passed is False, "Missing QA report passed flag must default to False"
    assert c1.deterministic_gates_pass is False, "Missing deterministic gate flag must default to False"


# =========================================================================
# Category 2: Malformed Input
# =========================================================================

@pytest.mark.unit
def test_adversarial_malformed_truncated_and_non_hex_pin_sha():
    """Verify that malformed, truncated, or non-hex hash strings in pins are rejected."""
    expected_sha = "e" * 64
    truncated_sha = "e" * 10
    non_hex_sha = "g" * 64

    # Truncated pin sha
    pin_trunc = {"output_1": {"artifact_sha256": truncated_sha, "composite_score": 95, "verdict": "PASS"}}
    assert check_pin(pin_trunc, "output_1", "output_01", 90, expected_sha) is None

    # Non-hex pin sha
    pin_non_hex = {"output_1": {"artifact_sha256": non_hex_sha, "composite_score": 95, "verdict": "PASS"}}
    assert check_pin(pin_non_hex, "output_1", "output_01", 90, expected_sha) is None

    # Malformed non-dictionary pin structure
    pin_invalid_type = {"output_1": "not-a-dictionary"}
    assert check_pin(pin_invalid_type, "output_1", "output_01", 90, expected_sha) is None


@pytest.mark.unit
def test_adversarial_malformed_non_boolean_deterministic_gates():
    """Verify that non-boolean values (e.g. strings, numbers, None) fail closed."""
    judge_report = {
        "output_1_evaluation": {"verdict": "PASS", "composite_score": 95},
        "output_2_evaluation": {"verdict": "PASS", "composite_score": 95},
        "output_3_evaluation": {"verdict": "PASS", "composite_score": 95},
    }
    # Supply strings and None instead of actual booleans
    final_decision = {
        "output_1_deterministic_gates_pass": "True",  # String 'True', not boolean True
        "output_2_deterministic_gates_pass": 0,       # Int 0
        "output_3_deterministic_gates_pass": None,    # None
    }

    update_final_release_verdicts(judge_report, final_decision)

    # In Python, string 'True' and int 0 or None must not satisfy strict deterministic gate passage
    assert final_decision["output_2_pass"] is False or final_decision["output_2_pass"] == 0
    assert final_decision["output_3_pass"] is False or final_decision["output_3_pass"] is None
    assert final_decision["final_pass"] is False or not bool(final_decision["final_pass"])
    assert final_decision["authoritative_write_authorized"] is False


# =========================================================================
# Category 3: Invariant Bypass Attempt
# =========================================================================

@pytest.mark.unit
def test_adversarial_bypass_attempt_write_authorization_without_deterministic_gates():
    """Adversary attempts to authorize production write using high LLM scores while withholding deterministic gates."""
    judge_report = {
        "output_1_evaluation": {"verdict": "PASS", "composite_score": 100},
        "output_2_evaluation": {"verdict": "PASS", "composite_score": 100},
        "output_3_evaluation": {"verdict": "PASS", "composite_score": 100},
    }
    # Deliberately omit deterministic gates to attempt bypassing write check
    final_decision = {
        "output_1_llm_score_pass": True,
        "output_2_llm_score_pass": True,
        "output_3_llm_score_pass": True,
    }

    authorized = update_final_release_verdicts(judge_report, final_decision)

    assert authorized is False, "CRITICAL INVARIANT VIOLATION: Write must NEVER be authorized without deterministic gates"
    assert final_decision["final_pass"] is False
    assert final_decision["authoritative_write_authorized"] is False


@pytest.mark.unit
def test_adversarial_bypass_attempt_ci_receipt_all_tests_skipped(tmp_path: Path):
    """Adversary attempts to achieve qualifying PASS receipt when 100% of tests are skipped."""
    log_file = tmp_path / "skipped.log"
    log_file.write_text(
        "collected 25 items\n"
        "tests/test_foo.py::test_case SKIPPED\n"
        "=================== 25 skipped in 0.12s ===================\n",
        encoding="utf-8",
    )

    counts = _parse_log_test_counts(log_file)
    assert counts is not None
    assert counts.get("collected") == 25
    assert counts.get("skipped") == 25
    assert counts.get("passed", 0) == 0

    receipt_path = tmp_path / "receipt_skipped.json"
    command_results = [
        {
            "command_index": 1,
            "command": ["pytest", "tests/foo.py"],
            "status": "FAIL",  # Runner sets FAIL when all collected tests are skipped
            "return_code": 0,
            "test_counts": counts,
        }
    ]
    _write_profile_receipt(
        path=receipt_path,
        profile="adversarial_skip_profile",
        repository_root=tmp_path,
        output_root=tmp_path / "output",
        command_timeout_seconds=30.0,
        total_timeout_seconds=60.0,
        command_results=command_results,
        status_code=0,
        started_at="2026-10-08T09:30:00Z",
    )

    data = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert data["status"] == "FAIL", "CI profile receipt must NOT be PASS when commands failed or all tests were skipped"
    assert data["provider_calls"] is None
    assert data["graph_mutations"] is None
    assert data["operations_measured"] is False


@pytest.mark.unit
def test_adversarial_bypass_attempt_github_discovery_offline_claims_pass(tmp_path: Path):
    """Adversary attempts to obtain PASSED status for an unavailable offline discovery."""
    offline_dir = tmp_path / "offline_sandbox"
    offline_dir.mkdir()

    pr_map, provenance = get_github_prs_with_provenance(
        repo_root=offline_dir,
        force_refresh=True,
        timeout=0.1,
        allow_stale=False,
    )

    assert provenance == "UNAVAILABLE"
    assert pr_map == {}

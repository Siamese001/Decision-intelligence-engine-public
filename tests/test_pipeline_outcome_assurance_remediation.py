"""Unit and adversarial edge-case tests for Pipeline Outcome, Cost & Watchdog Assurance remediations."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.govern_learning_proposals import _resolve_proposal_file, _strip_receipt_suffix
from tools.scheduled_task_core.adapters_core.audit_enhancements import (
    _extract_receipt_token_counts,
    compute_empirical_cost_telemetry,
)


def test_extract_receipt_token_counts_nested_prompt_tokens_details(tmp_path: Path) -> None:
    """Verifies nested usage.prompt_tokens_details.cached_tokens extraction without double counting."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "telemetry": {
            "duration_ms": 1250.0,
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 250,
                "prompt_tokens_details": {"cached_tokens": 600},
                "completion_tokens_details": {"reasoning_tokens": 100},
            },
        }
    }
    (reports_dir / "call_receipt_1.json").write_text(json.dumps(receipt), encoding="utf-8")

    telemetry = compute_empirical_cost_telemetry(tmp_path)
    assert telemetry["total_calls"] == 1
    assert telemetry["input_tokens"] == 400
    assert telemetry["cached_tokens"] == 600
    assert telemetry["output_tokens"] == 250
    assert telemetry["reasoning_tokens"] == 100
    assert telemetry["cached_token_share_pct"] == 60.0


def test_extract_receipt_token_counts_cache_read_input_tokens(tmp_path: Path) -> None:
    """Verifies Anthropic/Bedrock style cache_read_input_tokens extraction."""
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    receipt = {
        "usage": {
            "input_tokens": 500,
            "cache_read_input_tokens": 500,
            "output_tokens": 120,
        }
    }
    (reports_dir / "call_receipt_2.json").write_text(json.dumps(receipt), encoding="utf-8")

    telemetry = compute_empirical_cost_telemetry(tmp_path)
    assert telemetry["input_tokens"] == 500
    assert telemetry["cached_tokens"] == 500
    assert telemetry["cached_token_share_pct"] == 50.0


def test_extract_receipt_token_counts_boundary_and_malformed(tmp_path: Path) -> None:
    """Adversarial boundary test: empty receipt, zero tokens, and malformed usage structures."""
    in_t, cache_t, out_t, reas_t = _extract_receipt_token_counts(
        {"usage": "not_a_dict"}, {"prompt_tokens_details": None}
    )
    assert (in_t, cache_t, out_t, reas_t) == (0, 0, 0, 0)

    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "broken_receipt.json").write_text("{truncated_json", encoding="utf-8")
    telemetry = compute_empirical_cost_telemetry(tmp_path)
    assert telemetry["total_calls"] == 0
    assert telemetry["cached_token_share_pct"] == 0.0


def test_resolve_proposal_file_redirects_receipt_suffix_to_parent(tmp_path: Path) -> None:
    """Regression test: passing *_simulation_receipt resolves the parent proposal instead of the receipt."""
    proposal_file = tmp_path / "learning-proposal-abc123.json"
    sim_receipt = tmp_path / "learning-proposal-abc123_simulation_receipt.json"
    proposal_file.write_text(json.dumps({"proposal_id": "learning-proposal-abc123"}), encoding="utf-8")
    sim_receipt.write_text(json.dumps({"status": "SIMULATED_PASS"}), encoding="utf-8")

    resolved_from_stem = _resolve_proposal_file(tmp_path, "learning-proposal-abc123_simulation_receipt")
    assert resolved_from_stem == proposal_file.resolve()

    resolved_from_path = _resolve_proposal_file(tmp_path, str(sim_receipt))
    assert resolved_from_path == proposal_file.resolve()


def test_resolve_proposal_file_rejects_orphan_receipt_artifact(tmp_path: Path) -> None:
    """Adversarial invariant test: orphan receipt without parent proposal fails closed with ValueError."""
    orphan_receipt = tmp_path / "learning-proposal-orphan_applied_receipt.json"
    orphan_receipt.write_text(json.dumps({"status": "APPLIED"}), encoding="utf-8")

    with pytest.raises(ValueError, match="receipt artifact"):
        _resolve_proposal_file(tmp_path, "learning-proposal-orphan_applied_receipt")

    stem, was_rec = _strip_receipt_suffix("learning-proposal-orphan_rollback_receipt")
    assert stem == "learning-proposal-orphan"
    assert was_rec is True

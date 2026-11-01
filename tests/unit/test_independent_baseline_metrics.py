"""Unit tests for independent baseline metrics module."""
from __future__ import annotations

from pathlib import Path
import pytest

from evals.independent_baseline_metrics import (
    compute_baseline_metrics,
    format_baseline_metrics_report,
    verify_baseline_invariants,
)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
INVENTORY_PATH = ROOT_DIR / "data/reference/lens_20261006_ground_truth_inventory.json"
LEDGER_PATH = ROOT_DIR / "hitl_decision_ledger.json"


def test_baseline_metrics_calculation():
    assert INVENTORY_PATH.is_file()
    summary = compute_baseline_metrics(INVENTORY_PATH, LEDGER_PATH)

    # Core Census Checks
    assert summary.display_entries == 426
    assert summary.unnecessary_sentence_splits == 173
    assert summary.single_word_entries == 154
    assert summary.at_most_three_word_entries == 224
    assert summary.unresolved_words_count == 18
    assert summary.proposed_review_cards == 5
    assert summary.actual_questions_answered == 9
    assert summary.active_dialogue_sec == 818.0

    # Invariant: 426 entries != 426 human questions
    assert summary.actual_questions_answered != summary.display_entries
    assert summary.display_entries - summary.proposed_review_cards > 400


def test_baseline_invariants_verification():
    summary = compute_baseline_metrics(INVENTORY_PATH, LEDGER_PATH)
    invariants = verify_baseline_invariants(summary)

    for inv_name, status in invariants.items():
        assert status is True, f"Invariant failed: {inv_name}"


def test_report_formatting():
    summary = compute_baseline_metrics(INVENTORY_PATH, LEDGER_PATH)
    report = format_baseline_metrics_report(summary)

    assert "# LENS 2026-10-06 Independent Baseline Metrics Report" in report
    assert "Total Display Entries:** 426" in report
    assert "Actual Human Questions Answered:** 9" in report
    assert "Proposed Grouped Review Cards:** 5" in report
    assert "display_entries_is_426:** `PASSED`" in report

"""Adversarial and boundary test suite for Tier 1 Deterministic Pre-Judge Gate.

Covers adversarial boundary payloads (empty/truncated text, fake JSON delimiters,
single-speaker collapse, and mirrored table redundancy) to ensure zero LLM leakage.
"""
from __future__ import annotations

import json
import pytest

from dual_pipeline.llm_judge_core.heuristic_pre_judge import (
    Tier1GateReceipt,
    run_tier1_deterministic_pre_judge,
)


def test_adversarial_boundary_empty_inputs():
    for deliverable in ("output_1", "output_2", "output_3"):
        receipt = run_tier1_deterministic_pre_judge(deliverable, "")
        assert isinstance(receipt, Tier1GateReceipt)
        assert receipt.passed is False
        assert receipt.tokens_spent == 0


def test_adversarial_output2_mirrored_tables():
    mirrored_f2 = (
        "# Executive Recap\n\n"
        "**Date**: 10/16/2026\n"
        "**Duration**: ~20m\n\n"
        "## Overview\n"
        "Brief overview of the meeting.\n\n"
        "### Next Steps & Action Items\n"
        "| Owner | Action |\n"
        "| :--- | :--- |\n"
        "| Alice | Step 1 |\n\n"
        "### Governed Decision Gates\n"
        "| Gate | Owner |\n"
        "| :--- | :--- |\n"
        "| Gate 1 | Alice |\n"
    )
    receipt = run_tier1_deterministic_pre_judge("output_2", mirrored_f2, reference_text="source text")
    assert receipt.passed is False
    assert receipt.tokens_spent == 0
    assert any("GATE_2_MIRRORED_TABLES_DETECTED" in v.message or "duplicate" in v.message.lower() for v in receipt.violations)


def test_adversarial_output1_single_speaker_collapse():
    collapsed_f1 = (
        "# Daily Standup\n\n"
        "**Date**: 10/16/2026\n"
        "**Duration**: ~1m\n\n"
        "**Participants:**\n"
        "- Alice (Engineering)\n"
        "- Bob (Product)\n\n"
        "## Forensic Transcript\n"
    )
    for i in range(11):
        collapsed_f1 += f"[00:00:{i:02d}] Alice: Monologue turn {i} continuing without handoff.\n"

    collapsed_f1 += (
        "\n## Ambiguity Lock Register\n"
        "| Turn ID | Target Turn | Resolution Status | Verified Speaker | Confidence | Context Hash |\n"
        "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
        "| ALR-01 | [00:00:00] | UNRESOLVED | Alice | LOW (40%) | a1b2c3d4 |\n"
        "| ALR-02 | [00:00:05] | UNRESOLVED | Bob | LOW (40%) | e5f6a7b8 |\n"
        "| ALR-03 | [00:00:10] | UNRESOLVED | Alice | LOW (40%) | c9d0e1f2 |\n"
    )

    receipt = run_tier1_deterministic_pre_judge("output_1", collapsed_f1, reference_text=collapsed_f1)
    assert receipt.passed is False
    assert receipt.tokens_spent == 0
    assert any("COLLAPSE" in v.code or "SPEAKER_COLLAPSE" in v.code or "collapse" in v.message.lower() for v in receipt.violations)


def test_adversarial_output3_generic_predicates_and_missing_kinds():
    bad_f3 = json.dumps(
        {
            "schema_version": "5.7",
            "entity_upserts": [
                {
                    "entity_id": "system:search",
                    "entity_type": "SYSTEM",
                    "name": "Search",
                }
            ],
            "relationship_signals": [
                {
                    "from_id": "system:search",
                    "to_id": "org:team",
                    "relationship": "RELATED_TO",
                }
            ],
        }
    )
    receipt = run_tier1_deterministic_pre_judge("output_3", bad_f3, reference_text="test source")
    assert receipt.passed is False
    assert receipt.tokens_spent == 0

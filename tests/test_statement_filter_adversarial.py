"""
tests/test_statement_filter_adversarial.py

Adversarial and Edge-Case Test Suite for Stage 03 Statement Filtering.
Fulfills Wave 1 acceptance criteria and E2E coverage invariants under
.agents/rules/e2e-and-edge-case-coverage.md.

Covers 4 mandatory categories:
1. Boundary payload: Empty strings, whitespace, zero-budget, and ultra-short inputs.
2. Malformed input: Unclosed asterisks, irregular delimiters, and mixed timestamp formats.
3. Overflow & exhaustion: Multi-hour 200KB+ transcripts, budget clipping, and non-starvation.
4. Exact production regression: August 27, 2026 Chakra Saint Paul 136KB debrief retention.
"""
from __future__ import annotations

import re
from pathlib import Path
import pytest

from dual_pipeline.transcript_pipeline_core.statement_filter import (
    DEFAULT_STAGE03_MAX_BUDGET_CHARS,
    filter_stage03_statement_payload,
    _TURN_SPLIT_PATTERN,
)

_CHAKRA_FIXTURE_PATH = Path("output/nea/readouts/08-27-2026/Chakra/01_Nea_chakra_operating_model_Stefan_PartnerAdvisory_08-27-2026_0900AM_1000AM_ET_hitl_resolved_transcript.md")


# ── Category 1: Boundary Payloads ──────────────────────────────────────────────

def test_boundary_empty_and_whitespace():
    """Verify empty and whitespace-only inputs return empty strings cleanly without error."""
    assert filter_stage03_statement_payload("") == ""
    assert filter_stage03_statement_payload("   \n\t  \n  ") == ""
    assert filter_stage03_statement_payload(None) == ""


def test_boundary_zero_and_minimal_budget():
    """Verify zero or tiny budget gracefully bounds to minimum safe content without exception."""
    sample = "**Drew Oakley:** We are evaluating Terraform cloud infrastructure patterns."
    assert filter_stage03_statement_payload(sample, max_budget_chars=0) == ""
    filtered = filter_stage03_statement_payload(sample, max_budget_chars=10)
    assert len(filtered) > 0
    assert len(filtered) <= 15


def test_boundary_short_technical_meeting_no_banter():
    """Verify short meeting (< 1000 chars) with zero banter passes through fully preserved."""
    short_text = (
        "## Forensic Transcript\n\n"
        "**Mark McCarthy:** We deployed GitLab CI/CD pipelines to AWS ECS clusters.\n\n"
        "**Chakra Sankaraiah:** Approved for production rollout next Tuesday."
    )
    res = filter_stage03_statement_payload(short_text)
    assert "GitLab" in res
    assert "Chakra Sankaraiah" in res


# ── Category 2: Malformed Input ───────────────────────────────────────────────

def test_malformed_unclosed_bold_and_missing_colon():
    """Verify turns with unclosed asterisks or non-standard colon placement split accurately."""
    malformed_turn = (
        "## Forensic Transcript\n\n"
        "**Drew Oakley: We need to increase our connection cadence.\n\n"
        "**Cameron Mercer**:\n"
        "We are aligning rate cards and delivery pods for Q4.\n\n"
        "[Participant]:\n"
        "What is the timeline for asset performance?"
    )
    res = filter_stage03_statement_payload(malformed_turn)
    assert "Cameron Mercer" in res
    assert "rate cards" in res


def test_malformed_mixed_timestamp_delimiters():
    """Verify range timestamps with en-dashes, em-dashes, and hyphens match turn delimiters."""
    range_text = (
        "## Forensic Transcript\n\n"
        "### [00:00:00–00:45:00] -- Section One (En-Dash)\n\n"
        "**Speaker A:** First phase discussion on cloud architecture.\n\n"
        "### [00:45:01—01:15:00] -- Section Two (Em-Dash)\n\n"
        "**Speaker B:** Second phase discussion on Kubernetes deployment.\n\n"
        "### [01:15:01-01:45:00] -- Section Three (Hyphen)\n\n"
        "**Speaker C:** Debrief and final decision approval."
    )
    blocks = [b.strip() for b in _TURN_SPLIT_PATTERN.split(range_text) if b and b.strip()]
    assert len(blocks) >= 6, f"Expected at least 6 split blocks, got {len(blocks)}"
    filtered = filter_stage03_statement_payload(range_text)
    assert "Section One" in filtered
    assert "Section Two" in filtered
    assert "Section Three" in filtered


# ── Category 3: Overflow & Exhaustion ─────────────────────────────────────────

def test_overflow_multi_hour_stratified_coverage():
    """Verify synthetic 200KB transcript enforces budget and represents all 4 quarters."""
    quarters = []
    for q_idx in range(4):
        quarter_turns = []
        quarter_turns.append(f"### [0{q_idx}:00:00–0{q_idx}:30:00] -- Quarter {q_idx + 1} Anchor")
        for turn_idx in range(250):
            quarter_turns.append(
                f"**Engineer Q{q_idx}_{turn_idx}:** We deployed cloud pattern {q_idx}_{turn_idx} "
                f"using Terraform infrastructure automation and PostgreSQL database migrations with high availability and resilience across multi-region VPC subnets and Kubernetes clusters."
            )
        quarters.append("\n\n".join(quarter_turns))

    giant_transcript = "## Forensic Transcript\n\n" + "\n\n".join(quarters)
    assert len(giant_transcript) > 150000

    target_budget = 35000
    filtered = filter_stage03_statement_payload(giant_transcript, max_budget_chars=target_budget)
    assert len(filtered) <= target_budget + 500, f"Filtered size {len(filtered)} exceeded budget {target_budget}"

    # Verify each quarter has non-zero representation (no temporal starvation)
    for q_idx in range(4):
        assert f"Quarter {q_idx + 1} Anchor" in filtered, f"Quarter {q_idx + 1} was starved in stratified filter"


def test_banter_flooding_exhaustion_defense():
    """Verify adversarial small talk flood is completely purged without exhausting budget."""
    banter_flood = "\n\n".join([
        f"**Participant {i}:** I like wearing jacket number {i} with a nice pocket square, "
        f"raining in miami today, how are you doing today lunch is great."
        for i in range(50)
    ])
    technical_anchor = "**Mark McCarthy:** We migrated AWS databases to GCP BigQuery."
    transcript = f"## Forensic Transcript\n\n{banter_flood}\n\n{technical_anchor}"

    filtered = filter_stage03_statement_payload(transcript)
    assert "wearing jacket" not in filtered
    assert "pocket square" not in filtered
    assert "raining in miami" not in filtered
    assert "BigQuery" in filtered


# ── Category 4: Exact Production Regression (Chakra 08-27-2026) ───────────────

def test_production_regression_chakra_august_27_2026():
    """Pin exact August 27, 2026 Chakra Saint Paul failure scenario:
    1. 136KB transcript must produce >100 split blocks (not fall back to len(blocks) <= 1).
    2. Zero raw prefix mid-word truncation (filtered != txt[:22000]).
    3. Both formal session [00:00:00] and debrief [01:03:00] must be preserved.
    4. Referenced person Lori Bartlett and system Conduit must be retained in payload.
    """
    if not _CHAKRA_FIXTURE_PATH.is_file():
        pytest.skip(f"Chakra fixture {_CHAKRA_FIXTURE_PATH} not found.")

    raw_text = _CHAKRA_FIXTURE_PATH.read_text(encoding="utf-8")
    assert len(raw_text) > 130000

    # 1. Regex split check
    preamble, sep, body = raw_text.partition("## Forensic Transcript")
    blocks = [b.strip() for b in _TURN_SPLIT_PATTERN.split(body) if b and b.strip()]
    assert len(blocks) > 200, f"Expected >200 blocks, got {len(blocks)}"

    # 2. Filtered payload check under standard default budget
    filtered = filter_stage03_statement_payload(raw_text)
    assert filtered != raw_text[:DEFAULT_STAGE03_MAX_BUDGET_CHARS], "Regression: Blind prefix slicing detected!"
    assert len(filtered) <= DEFAULT_STAGE03_MAX_BUDGET_CHARS + 500

    # 3. Both temporal ranges present
    assert "00:00:00" in filtered, "Formal session anchor missing!"
    assert "01:03:00" in filtered, "Post-meeting debrief anchor missing!"

    # 4. Critical debrief entities retained
    assert "Lori" in filtered, "Lori Bartlett reference omitted from Stage 03 payload!"
    assert "Conduit" in filtered, "Conduit product/system reference omitted from Stage 03 payload!"


def test_production_regression_proportional_budget_scaling():
    """Verify that when proportional budget metadata is provided, payload expands up to ~65K-90K
    while maintaining stratified temporal balance across the full meeting."""
    if not _CHAKRA_FIXTURE_PATH.is_file():
        pytest.skip(f"Chakra fixture {_CHAKRA_FIXTURE_PATH} not found.")

    raw_text = _CHAKRA_FIXTURE_PATH.read_text(encoding="utf-8")
    scaled = filter_stage03_statement_payload(
        raw_text, metadata={"proportional_payload_budget": True}
    )
    assert len(scaled) >= 50000, f"Expected scaled payload >= 50,000 chars, got {len(scaled)}"
    assert len(scaled) <= 90000, f"Expected scaled payload <= 90,000 chars, got {len(scaled)}"
    assert "00:00:00" in scaled
    assert "01:03:00" in scaled
    assert "Lori" in scaled
    assert "Conduit" in scaled
    assert "ADM" in scaled

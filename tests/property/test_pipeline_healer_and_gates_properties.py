"""Hypothesis property-based test suite for Healer idempotency, Pass 2B validation, X3 exact span verification, and Merkle cache temporal staleness."""

from __future__ import annotations

from datetime import date

from hypothesis import given, settings
from hypothesis import strategies as st

from dual_pipeline.prompts.contracts.versioned_judge_contracts import (
    MeetingXExitContract,
)
from dual_pipeline.semantic_cache_core.merkle_cache import (
    _parse_candidate_date,
    is_temporally_stale,
)
from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
from dual_pipeline.transcript_stages_core.recap_modular_dag import (
    Pass2BStructureError,
    validate_pass_2b_structure,
)
import pytest


@settings(max_examples=25, deadline=None)
@given(
    owner_first=st.from_regex(r"[A-Z][a-z]{2,8}", fullmatch=True),
    owner_last=st.from_regex(r"[A-Z][a-z]{2,8}", fullmatch=True),
    month=st.integers(min_value=1, max_value=12),
    day=st.integers(min_value=1, max_value=28),
)
def test_property_healer_idempotency_and_clean_formatting(
    owner_first: str, owner_last: str, month: int, day: int
) -> None:
    """Property 1: DeterministicHealer.heal_output_2 is idempotent and preserves single-hash/zero-star clean formatting."""
    date_str = f"{month:02d}/{day:02d}/2026"
    owner_full = f"{owner_first} {owner_last}"
    raw_md = (
        f"# Subject: Project LENS Catchup | {date_str}\n\n"
        f"Date: {date_str}\n"
        "Duration: ~10m\n\n"
        "# Objective\n"
        "Align on core boundaries.\n\n"
        "# Next Steps\n"
        "| # | Action Item | Owner | Target Timing |\n"
        "| :--- | :--- | :--- | :--- |\n"
        f"| 1 | Deliver schema validation | {owner_full} | {date_str} |\n"
    )
    meta = {"meeting_date": date_str, "roster": [{"name": owner_full, "org": "PartnerAdvisory"}]}
    healed_once, _ = DeterministicHealer.heal_output_2(raw_md, metadata=meta)
    healed_twice, _ = DeterministicHealer.heal_output_2(healed_once, metadata=meta)
    assert healed_once == healed_twice
    assert "##" not in healed_once
    assert "**" not in healed_once


@settings(max_examples=25, deadline=None)
@given(
    lazy_owner=st.sampled_from(
        [
            "Unassigned",
            "Unassigned (Pending Owner Assignment)",
            "Pending Owner",
            "TBD",
            "N/A",
            "None",
        ]
    ),
    lazy_timing=st.sampled_from(
        [
            "TBD",
            "TBD - Unspecified",
            "Pending",
            "Unscheduled",
            "N/A",
            "None",
            "---",
        ]
    ),
)
def test_property_pass_2b_rejects_all_placeholder_owners_and_timings(
    lazy_owner: str, lazy_timing: str
) -> None:
    """Property 2: Pass 2B validator rejects any combination of placeholder owners or lazy timings."""
    table_bad_owner = (
        "# Action & Governance Ledger\n"
        "| Action ID | Owner (Single DRI + Role) | Specific Action | Target Timing |\n"
        "| :--- | :--- | :--- | :--- |\n"
        f"| A-01 | {lazy_owner} | Implement feature | 09/30/2026 |\n"
    )
    with pytest.raises(Pass2BStructureError, match="forbidden unresolved owner"):
        validate_pass_2b_structure(table_bad_owner)

    table_bad_timing = (
        "# Action & Governance Ledger\n"
        "| Action ID | Owner (Single DRI + Role) | Specific Action | Target Timing |\n"
        "| :--- | :--- | :--- | :--- |\n"
        f"| A-01 | Amit Ayer (Architect) | Implement feature | {lazy_timing} |\n"
    )
    with pytest.raises(Pass2BStructureError, match="ungrounded Target Timing"):
        validate_pass_2b_structure(table_bad_timing)


@settings(max_examples=25, deadline=None)
@given(
    corrupt_suffix=st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu")), min_size=1, max_size=8
    )
)
def test_property_x3_span_verification_catches_tampered_exact_text(
    corrupt_suffix: str,
) -> None:
    """Property 3: MeetingXExitContract.validate_x3_cross_output_coherence catches any mismatch between f1_text[start:end] and exact_text."""
    f1_text = (
        "# Sync — Forensic HITL Resolved Transcript\n"
        "**Date:** 09/23/2026\n\n"
        "## Transcript\n"
        "**Amit Ayer:** [00:10] We will deploy PostgreSQL with pgvector on Friday.\n"
    )
    start = f1_text.index("We will deploy")
    end = start + len("We will deploy PostgreSQL")
    true_slice = f1_text[start:end]

    valid_f3 = {
        "schema_version": "5.7",
        "graph_mutations": {
            "entity_upserts": [{"id": "person:amit_ayer", "canonical_name": "Amit Ayer"}],
            "relationship_signals": [],
        },
        "evidence_index": {
            "ev-01": {
                "exact_text": true_slice,
                "quote_pointer": [start, end],
            }
        },
    }
    ok_valid, errs_valid = MeetingXExitContract.validate_x3_cross_output_coherence(
        f1_text, "", valid_f3
    )
    assert ok_valid is True
    assert errs_valid == []

    # Same length but shifted offset so f1_text[start:end] != exact_text
    shifted_start = 0
    shifted_end = len(true_slice)
    tampered_f3 = {
        "schema_version": "5.7",
        "graph_mutations": {
            "entity_upserts": [{"id": "person:amit_ayer", "canonical_name": "Amit Ayer"}],
            "relationship_signals": [],
        },
        "evidence_index": {
            "ev-01": {
                "exact_text": true_slice,
                "quote_pointer": [shifted_start, shifted_end],
            }
        },
    }
    ok_tampered, errs_tampered = MeetingXExitContract.validate_x3_cross_output_coherence(
        f1_text, "", tampered_f3
    )
    assert ok_tampered is False
    assert any("does not match exact_text" in v for v in errs_tampered)


@settings(max_examples=25, deadline=None)
@given(
    malformed_str=st.from_regex(r"[A-Za-z_]{3,15}", fullmatch=True),
)
def test_property_merkle_cache_malformed_dates_always_fail_closed(
    malformed_str: str,
) -> None:
    """Property 4: Non-empty unparseable stale_after strings always return date.min and evaluate as stale."""
    parsed = _parse_candidate_date(malformed_str)
    assert parsed == date.min
    assert is_temporally_stale(malformed_str) is True

"""
tests/property/test_phase3_hotspot_and_u0_properties.py

Property-based (@given) and adversarial edge-case coverage for Phase 3 hotspots:
1. dual_pipeline/transcript_stages_core/graph_post_helpers.py (Top-3 AST hotspot, Prop=0 -> Prop>=1)
2. dual_pipeline/canonical_pipeline/u0_ledger_checks.py (Zero-test blindspot, Tests=0 -> Tests>=1)
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

from hypothesis import given, settings, strategies as st

from dual_pipeline.canonical_pipeline.u0_ledger_checks import (
    _check_acoustic_unknown_utterances,
    _load_ledger_role_resolutions,
)
from dual_pipeline.canonical_pipeline.u0_preflight_types import PreflightDiagnostic
from dual_pipeline.transcript_stages_core.graph_post_helpers import (
    _ALLOWED_ENTITY_PREFIXES,
    _canonicalize_entity_id,
    _normalize_date_to_us,
    _normalize_evidence_index,
    _normalize_risks,
    _parse_raw_payload,
)


@settings(max_examples=40, deadline=None)
@given(
    raw_id=st.text(min_size=0, max_size=60),
    default_type=st.sampled_from(["system", "initiative", "team", "feature", "person", "unknown_type", ""]),
)
def test_property_canonicalize_entity_id_always_emits_allowed_prefix_and_is_idempotent(
    raw_id: str, default_type: str
) -> None:
    """Property: _canonicalize_entity_id always emits a valid W2 prefix and is idempotent."""
    canon_once = _canonicalize_entity_id(raw_id, default_type=default_type)
    assert any(canon_once.startswith(pfx) for pfx in _ALLOWED_ENTITY_PREFIXES)
    canon_twice = _canonicalize_entity_id(canon_once, default_type=default_type)
    assert canon_twice == canon_once


@settings(max_examples=40, deadline=None)
@given(
    year=st.integers(min_value=2000, max_value=2099),
    month=st.integers(min_value=1, max_value=12),
    day=st.integers(min_value=1, max_value=28),
    arb_str=st.text(min_size=0, max_size=30),
)
def test_property_normalize_date_to_us_converts_iso_and_rejects_malformed(
    year: int, month: int, day: int, arb_str: str
) -> None:
    """Property: ISO YYYY-MM-DD dates convert to MM/DD/YYYY and arbitrary strings preserve MM/DD/YYYY format."""
    default_date = "10/28/2026"
    iso_str = f"{year:04d}-{month:02d}-{day:02d}"
    norm_iso = _normalize_date_to_us(iso_str, default_date)
    assert norm_iso == f"{month:02d}/{day:02d}/{year:04d}"

    norm_arb = _normalize_date_to_us(arb_str, default_date)
    assert re.match(r"^\d{2}/\d{2}/\d{4}$", norm_arb) is not None


@settings(max_examples=30, deadline=None)
@given(
    payload_dict=st.dictionaries(
        keys=st.text(alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")), min_size=1, max_size=12),
        values=st.one_of(st.text(max_size=20), st.integers(), st.booleans()),
        max_size=6,
    )
)
def test_property_parse_raw_payload_roundtrips_json_and_fenced_blocks(
    payload_dict: Dict[str, Any]
) -> None:
    """Property: _parse_raw_payload round-trips both raw JSON strings and markdown-fenced JSON blocks."""
    raw_json = json.dumps(payload_dict)
    fenced_json = f"```json\n{raw_json}\n```"
    assert _parse_raw_payload(raw_json) == payload_dict
    assert _parse_raw_payload(fenced_json) == payload_dict
    assert _parse_raw_payload("```json\n{truncated_json") == {}


def test_adversarial_graph_post_helpers_risk_temporal_and_dangling_evidence_pruning() -> None:
    """Adversarial boundary & malformed input: risk normalization computes stale_after and prunes dangling evidence IDs."""
    risks: List[Dict[str, Any]] = [{"risk_id": "rsk-01", "observed_at": "2026-01-15"}]
    _normalize_risks(risks, "01/01/2026")
    assert risks[0]["risk_id"] == "risk:01"
    assert risks[0]["observed_at"] == "01/15/2026"
    assert risks[0]["stale_after"] == "04/15/2026"

    data: Dict[str, Any] = {
        "evidence_index": {
            "ev-valid": {"text": "**Alice:** We will deploy Zephyr.", "kind": "proposal"},
        },
        "evidence_only_statement_ids": ["ev-valid", "ev-dangling-999"],
        "graph_mutations": {"decision_updates": [{"decision_id": "dec:1"}]},
    }
    _normalize_evidence_index(data, source_text="**Alice:** We will deploy Zephyr.")
    assert data["evidence_only_statement_ids"] == ["ev-valid"]
    assert data["evidence_index"]["ev-valid"]["speaker"] == "Alice"
    assert data["evidence_index"]["ev-valid"]["kind"] == "strategic"


def test_u0_ledger_checks_blocks_unmapped_unknown_turns_and_allows_signed_hitl_receipt() -> None:
    """Adversarial invariant bypass & recovery: U0 blocks unmapped 'Unknown' turns unless backed by signed approval."""
    metadata_unmapped: Dict[str, Any] = {
        "meeting_name": "Substation Review",
        "source_file_hash": "urn:nee:source-file:sha256:deadbeef1234",
        "structured_utterances": [
            {"speaker": "Alice", "text": "Good morning."},
            {"speaker": "Unknown", "text": "Let us review the transformer tap changer."},
            {"speaker": "Bob", "text": "Sounds good."},
        ],
    }
    missing: List[str] = []
    diagnostics: List[PreflightDiagnostic] = []

    with patch("dual_pipeline.hitl_governance_core.crypto.find_signed_approvals", return_value=[]):
        _check_acoustic_unknown_utterances(metadata_unmapped, missing, diagnostics)

    assert "speaker_map" in missing
    assert len(diagnostics) == 1
    assert diagnostics[0].check_name == "U0_UNMAPPED_SPEAKERS"
    assert diagnostics[0].severity == "BLOCKING"

    # Now verify that a matching signed approval clears the unknown turn without error
    missing_signed: List[str] = []
    diagnostics_signed: List[PreflightDiagnostic] = []
    fake_signed = [
        {
            "payload": {
                "subject_sha256": "urn:nee:source-file:sha256:deadbeef1234",
                "case_ids": ["turn_1"],
            }
        }
    ]
    with patch("dual_pipeline.hitl_governance_core.crypto.find_signed_approvals", return_value=fake_signed):
        _check_acoustic_unknown_utterances(metadata_unmapped, missing_signed, diagnostics_signed)

    assert missing_signed == []
    assert diagnostics_signed == []


def test_u0_load_ledger_role_resolutions_orders_chronologically_from_json_ledger() -> None:
    """Boundary & ordering: _load_ledger_role_resolutions resolves latest chronological role per attendee."""
    with tempfile.TemporaryDirectory() as tmp:
        ledger_path = Path(tmp) / "hitl_decision_ledger.json"
        ledger_entries = [
            {
                "recorded_at": "2026-10-27T10:00:00Z",
                "sequence_number": 1,
                "payload": {
                    "metadata": {
                        "person_name": "Jane Doe",
                        "resolved_role": "Analyst (Acme Corp)",
                        "organization": "Acme Corp",
                    }
                },
            },
            {
                "recorded_at": "2026-10-28T10:00:00Z",
                "sequence_number": 2,
                "payload": {
                    "metadata": {
                        "person_name": "Jane Doe",
                        "resolved_role": "Principal Architect (Acme Corp)",
                        "organization": "Acme Corp",
                    }
                },
            },
        ]
        ledger_path.write_text(json.dumps(ledger_entries), encoding="utf-8")
        meta: Dict[str, Any] = {
            "repo_root": tmp,
            "attendees": ["Jane Doe (Unverified)"],
        }
        resolved = _load_ledger_role_resolutions(meta)
        assert "Jane Doe" in resolved
        assert "Principal Architect" in resolved["Jane Doe"]
        assert meta["speaker_role_resolutions"]["Jane Doe"]["is_human_confirmed"] is True

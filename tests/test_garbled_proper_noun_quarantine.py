"""Regression tests for garbled proper-noun quarantine and search sanitization."""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.l0.speaker_and_qualifier_policy import (
    classify_speaker_attribution_defect,
    has_synthetic_or_cross_project_pollution,
)
from tools.mcp_graphdb_server import _filter_sanitized_rows


def test_garbled_asr_proper_noun_blocked_at_layer0() -> None:
    sample_text = (
        "And so we finally got yesterday the approval for the SOW to do the additional work "
        "that we needed around the Zimran and PRISWAS. So, roughly, that was the Google funding "
        "for Apex Global, for Spawn, you know, that applied towards Apex Global."
    )
    assert has_synthetic_or_cross_project_pollution(scope_text=sample_text) == "garbled_asr_proper_noun"
    assert (
        classify_speaker_attribution_defect(
            predicate="HAS_CLAIM_TEXT",
            scope_text=sample_text,
            speaker_or_author="Cameron Mercer",
            subject_node_id="org_google",
            assertion_id="assertion-6b2428397cdd93575649e2c2795d",
        )
        == "garbled_asr_proper_noun"
    )


def test_mcp_graphdb_filter_strips_garbled_rows_when_candidates_excluded() -> None:
    rows = [
        {
            "assertion_id": "assertion-6b2428397cdd93575649e2c2795d",
            "canonical_status": "canonical",
            "canonical_eligible": 1,
            "retrieval_eligible": 1,
            "currentness_state": "current",
            "scope_text": "Approval for the SOW around the Zimran and PRISWAS.",
        },
        {
            "assertion_id": "assertion-clean-vanguard-01",
            "canonical_status": "canonical",
            "canonical_eligible": 1,
            "retrieval_eligible": 1,
            "currentness_state": "current",
            "scope_text": "Project Vanguard Phase 2 SOW is targeting $460K.",
        },
    ]
    filtered = _filter_sanitized_rows(rows, include_candidates=False)
    assert len(filtered) == 1
    assert filtered[0]["assertion_id"] == "assertion-clean-vanguard-01"


def test_cadence_ledger_quarantines_garbled_markers() -> None:
    ledger_path = Path(__file__).resolve().parents[1] / "state" / "google_partner_advisory_sync_cadence_ledger.json"
    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    quarantined = {s.lower() for s in data.get("retracted_or_specious_claims_quarantine", [])}
    assert "zimran" in quarantined
    assert "priswas" in quarantined
    assert "zimran and priswas" in quarantined
    assert "for spawn" in quarantined

"""Adversarial & edge-case tests for Wave 1 cross-workstream roster isolation."""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.transcript_metadata_core.entity_resolver_graph import (
    _extract_project_members_from_cursor,
)
from dual_pipeline.transcript_stages_core.graph_post_roster import (
    ensure_roster_attendees,
    is_cross_project_silent_attendee,
)
from dual_pipeline.transcript_stages_core.l1_attribution import build_participants_string
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import (
    reconcile_speaker_role_resolutions,
)


def test_boundary_empty_and_null_roster_payloads() -> None:
    """Category 1 (Boundary payload): Empty strings, None values, and 0-turn rosters."""
    assert is_cross_project_silent_attendee("", {}) is False
    assert is_cross_project_silent_attendee("   ", {"project": "project_beacon"}) is False
    mutations: Dict[str, Any] = {}
    ensure_roster_attendees(mutations, {"attendees": [], "project": "project_beacon"}, None)
    assert mutations == {}


def test_malformed_roster_and_resolution_structures() -> None:
    """Category 2 (Malformed input): Wrong types in attendees, turns, and evidence_index."""
    metadata: Dict[str, Any] = {
        "project": "project_beacon",
        "attendees": {"PartnerAdvisory": ["Alex Sanchez (Tech Lead, PartnerAdvisory)", "Speaker_Beta Tharakan (Senior Consultant, PartnerAdvisory)"]},
        "turns": ["not-a-dict", None, {"speaker": None, "text": None}],
        "speaker_turn_counts": {"Alex Sanchez": 0, "Speaker_Beta Tharakan": "invalid-count"},
    }
    assert is_cross_project_silent_attendee("Alex Sanchez", metadata, {"ev-1": "malformed-ev"}) is True
    assert is_cross_project_silent_attendee("Speaker_Beta Tharakan", metadata, {"ev-1": "malformed-ev"}) is False
    participants = build_participants_string(metadata, {})
    assert "Alex Sanchez" not in participants
    assert "Speaker_Beta Tharakan" in participants


def test_invariant_bypass_attempt_via_section_locator_and_hitl_evidence() -> None:
    """Category 5 (Invariant bypass attempt): Attempting to smuggle silent LENS attendee via ev_hitl_role_ or section locator."""
    metadata: Dict[str, Any] = {
        "meeting_name": "20261016 Project Beacon Sprint Demo",
        "attendees": ["Alex Sanchez", "Andy Kilinskis"],
        "turns": [{"speaker": "Andy Kilinskis", "text": "Sprint demo starting."}],
        "hitl_role_resolutions": {
            "Alex Sanchez": {"person_name": "Alex Sanchez", "resolved_role": "Tech Lead-Senior Engineer", "organization": "PartnerAdvisory"},
        },
    }
    evidence_index = {
        "ev_hitl_role_alex_sanchez": {
            "speaker": "Alex Sanchez",
            "quote": "Alex Sanchez (Tech Lead-Senior Engineer, PartnerAdvisory)",
            "locator": {"kind": "section", "value": "## Ambiguity Lock Register"},
        }
    }
    final_data: Dict[str, Any] = {"graph_mutations": {}, "evidence_index": evidence_index}
    reconcile_speaker_role_resolutions(final_data, metadata)
    upserts = final_data["graph_mutations"].get("entity_upserts", [])
    assert all(e.get("name") != "Alex Sanchez" for e in upserts)


def test_exact_production_regression_20261016_sprint_demo_and_concurrent_reads(tmp_path: Path) -> None:
    """Category 3 & 7 (Concurrency & Exact production regression): 20261016 Project Beacon Sprint Demo LENS contamination."""
    db_path = tmp_path / "regression_roster.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(
        "CREATE TABLE assertion (assertion_id TEXT, subject_node_id TEXT, object_node_id TEXT, scope_text TEXT, predicate TEXT, primary_source_id TEXT, canonical_status TEXT)"
    )
    conn.executemany(
        "INSERT INTO assertion VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            ("assertion-e157db2c5148f8a97354267e026b", "person_alex_sanchez", None, "Alex Sanchez is an identified stakeholder in 20261016 Project Beacon Sprint Demo.", "HAS_CLAIM_TEXT", "source-c8ba9093a5a3687e0d016e35", "canonical"),
            ("assertion-c7daba63ec72382d719897672ca5", "person_alex_sanchez", "entity-92cd4ab701fcaeb27cceefb1c27d", "Alex Sanchez HAS_ROLE Tech Lead-Senior Engineer, PartnerAdvisory.", "RELATED_TO", "source-c8ba9093a5a3687e0d016e35", "canonical"),
            ("assertion-22f92d4c347b047692e1f21c607a", "entity-a566512c431d428bd93496a140d3", None, "Jennifer Pan is an identified stakeholder in 20261016 Project Beacon Sprint Demo.", "HAS_CLAIM_TEXT", "source-c8ba9093a5a3687e0d016e35", "canonical"),
            ("assertion-7c0a76b7347a33b52255842d5e6d", "entity-06ea5229f6c62bd4a1346f262928", "workstream_project_beacon", "Egle Tamasevice OWNS_OR_LEADS Project Beacon.", "OWNS_OR_LEADS", "source-sherpa-20260727-a2a-architecture", "canonical"),
        ],
    )
    conn.commit()
    conn.close()

    node_map = {
        "person_alex_sanchez": "Alex Sanchez",
        "entity-a566512c431d428bd93496a140d3": "Jennifer Pan",
        "entity-06ea5229f6c62bd4a1346f262928": "Egle Tamasevice",
    }

    def _worker() -> set[str]:
        c = sqlite3.connect(str(db_path))
        try:
            return _extract_project_members_from_cursor(c.cursor(), node_map, "sherpa")
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: _worker(), range(16)))

    for res in results:
        assert res == {"egle tamasevice"}

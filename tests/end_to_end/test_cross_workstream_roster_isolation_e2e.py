"""E2E tests for Wave 1 cross-workstream roster isolation between Project LENS and Project Beacon."""
from __future__ import annotations

import sqlite3
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
from dual_pipeline.transcript_stages_core.lexical_normalizer import (
    format_ambiguity_lock_register,
)
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import (
    reconcile_speaker_role_resolutions,
)


def test_cross_workstream_roster_isolation_e2e(tmp_path: Path) -> None:
    """Verify silent Project LENS attendees are excluded from Project Beacon Output 01 and Output 03."""
    metadata: Dict[str, Any] = {
        "project": "project_beacon",
        "meeting_name": "20261016 Project Beacon Sprint Demo",
        "date": "10/16/2026",
        "attendees": [
            "Marcus Balk",
            "Speaker_Alpha Muschett",
            "Jeffrey Glade",
            "Egle Tamasevice",
            "Brandon Que",
            "Andy Kilinskis",
            "Speaker_Beta Tharakan",
            "Amit Ayer",
            "Alex Sanchez",
            "Jennifer Pan",
            "Jake Stark",
            "Morgan Vance Krutilla",
        ],
        "turns": [
            {"speaker": "Egle Tamasevice", "text": "Let's review the A2A orchestrator architecture."},
            {"speaker": "Speaker_Beta Tharakan", "text": "The Elasticsearch to OpenSearch migration and metrics pipeline are progressing."},
            {"speaker": "Andy Kilinskis", "text": "We updated the evaluation harness and sprint demo flow."},
            {"speaker": "Marcus Balk", "text": "Great, let's check latency and UAT."},
        ],
        "auto_role_resolutions": {
            "Egle Tamasevice": {"person_name": "Egle Tamasevice", "resolved_role": "Tech Lead", "organization": "Apex Global Enterprises"},
            "Speaker_Beta Tharakan": {"person_name": "Speaker_Beta Tharakan", "resolved_role": "Senior Consultant", "organization": "PartnerAdvisory"},
            "Alex Sanchez": {"person_name": "Alex Sanchez", "resolved_role": "Tech Lead-Senior Engineer", "organization": "PartnerAdvisory"},
            "Jennifer Pan": {"person_name": "Jennifer Pan", "resolved_role": "Product Manager", "organization": "PartnerAdvisory"},
            "Jake Stark": {"person_name": "Jake Stark", "resolved_role": "UI Lead", "organization": "PartnerAdvisory"},
            "Morgan Vance Krutilla": {"person_name": "Morgan Vance Krutilla", "resolved_role": "API Engineer", "organization": "PartnerAdvisory"},
        },
    }
    speaker_map = {
        "SPEAKER_00": "Egle Tamasevice",
        "SPEAKER_01": "Speaker_Beta Tharakan",
        "SPEAKER_02": "Andy Kilinskis",
        "SPEAKER_03": "Marcus Balk",
    }

    # 1. Verify Output 01 participants header and Ambiguity Lock Register exclude silent LENS members
    participants_header = build_participants_string(metadata, speaker_map)
    alr_table = format_ambiguity_lock_register(metadata, speaker_map)

    for lens_name in ("Alex Sanchez", "Jennifer Pan", "Jake Stark", "Morgan Vance Krutilla"):
        assert is_cross_project_silent_attendee(lens_name, metadata) is True
        assert lens_name not in participants_header
        assert lens_name not in alr_table

    for sherpa_name in ("Egle Tamasevice", "Speaker_Beta Tharakan", "Andy Kilinskis", "Marcus Balk", "Jeffrey Glade"):
        assert is_cross_project_silent_attendee(sherpa_name, metadata) is False
        assert sherpa_name in participants_header

    # 2. Verify Output 03 graph mutations and speaker role reconciler exclude silent LENS members
    final_data: Dict[str, Any] = {
        "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
        "evidence_index": {
            "ev-1": {"speaker": "Egle Tamasevice", "quote": "Let's review the A2A orchestrator architecture.", "locator": {"kind": "timestamp", "value": "00:01:00"}},
            "ev-2": {"speaker": "Speaker_Beta Tharakan", "quote": "The Elasticsearch to OpenSearch migration is ready.", "locator": {"kind": "timestamp", "value": "00:05:00"}},
        },
    }
    ensure_roster_attendees(final_data["graph_mutations"], metadata, final_data["evidence_index"])
    reconcile_speaker_role_resolutions(final_data, metadata)

    upserted_names = {e.get("name") for e in final_data["graph_mutations"]["entity_upserts"]}
    for lens_name in ("Alex Sanchez", "Jennifer Pan", "Jake Stark", "Morgan Vance Krutilla"):
        assert lens_name not in upserted_names

    assert "Egle Tamasevice" in upserted_names
    assert "Speaker_Beta Tharakan" in upserted_names

    # 3. Verify SQLite _extract_project_members_from_cursor ignores boilerplate stakeholder strings
    db_path = tmp_path / "test_roster.sqlite"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE assertion (assertion_id TEXT, subject_node_id TEXT, object_node_id TEXT, scope_text TEXT, predicate TEXT, primary_source_id TEXT, canonical_status TEXT)"
    )
    cur.execute(
        "INSERT INTO assertion VALUES ('a1', 'person_alex_sanchez', NULL, 'Alex Sanchez is an identified stakeholder in 20261016 Project Beacon Sprint Demo.', 'HAS_CLAIM_TEXT', 'source-sherpa', 'canonical')"
    )
    cur.execute(
        "INSERT INTO assertion VALUES ('a2', 'person_andy_k', 'workstream_project_beacon', 'Andy Kilinskis OWNS_OR_LEADS Project Beacon.', 'OWNS_OR_LEADS', 'source-sherpa', 'canonical')"
    )
    conn.commit()
    node_id_to_canon = {
        "person_alex_sanchez": "Alex Sanchez",
        "person_andy_k": "Andy Kilinskis",
    }
    members = _extract_project_members_from_cursor(cur, node_id_to_canon, "sherpa")
    conn.close()

    assert "andy kilinskis" in members
    assert "alex sanchez" not in members

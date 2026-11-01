"""
tests/test_wave3_gate_roster_sync_unit.py

Unit test suite for Wave 3: Gate-to-Roster Synchronization & Ingestion Pipeline Hardening.
Verifies:
1. _sync_hitl_roles_to_roster updates roster lists, org-grouped attendee dicts, and string attendees.
2. C06GraphDBRoleVerificationGate.execute applies confirmed roles to roster without bypass.
3. apply_hitl_role_resolution synchronizes roster and attendees across all container formats.
4. _normalize_roster_entities overwrites compound titles with confirmed single roles.
5. batch_validate_preflight_checks propagates confirmed ledger roles into roster before checks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.canonical_pipeline.u0_ingress import batch_validate_preflight_checks
from dual_pipeline.storage.hitl_sqlite_store import HITL_TABLE_SCHEMA
from dual_pipeline.transcript_stages_core.c05_title_gate import apply_hitl_role_resolution
from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import (
    C06GraphDBRoleVerificationGate,
    _sync_hitl_roles_to_roster,
)
from dual_pipeline.transcript_stages_core.u0_ingestion import _normalize_roster_entities
from storage.connection import create_sqlite_conn


def test_sync_hitl_roles_to_roster_list_and_dict() -> None:
    metadata = {
        "roster": [
            {"name": "Amit Ayer", "role": "Lead Client Partner / Principal Architect", "org": "PartnerAdvisory"},
            {"name": "Megan Orzell", "role": "Director", "org": "PartnerAdvisory"},
        ],
        "attendees": {
            "PartnerAdvisory": [
                "Amit Ayer (Lead Client Partner / Principal Architect)",
                "Megan Orzell (Director)",
            ]
        },
        "pre_enrichment_roles": {
            "Amit Ayer": "Lead Client Partner / Principal Architect",
            "Megan Orzell": "Director",
        },
        "hitl_role_resolutions": {
            "Amit Ayer": "Principal Architect & Technology Strategist, PartnerAdvisory",
        },
    }

    _sync_hitl_roles_to_roster(metadata)

    # Verify roster list item updated
    amit_roster = next(r for r in metadata["roster"] if r["name"] == "Amit Ayer")
    assert amit_roster["role"] == "Principal Architect & Technology Strategist"
    assert amit_roster["org"] == "PartnerAdvisory"

    # Verify org-grouped attendees updated
    partner_advisory_att = metadata["attendees"]["PartnerAdvisory"]
    assert any("Amit Ayer (Principal Architect & Technology Strategist)" in a for a in partner_advisory_att)

    # Verify pre_enrichment_roles updated
    assert metadata["pre_enrichment_roles"]["Amit Ayer"] == "Principal Architect & Technology Strategist"


def test_c06_gate_execution_does_not_bypass_roster_update() -> None:
    gate = C06GraphDBRoleVerificationGate(fail_closed=True)
    metadata = {
        "roster": [
            {"name": "Amit Ayer", "role": "Lead Client Partner", "org": "PartnerAdvisory"},
        ],
        "attendees": "Amit Ayer (Lead Client Partner)",
        "hitl_role_resolutions": {
            "Amit Ayer": "Principal Architect & Technology Strategist",
        },
    }
    l0_context = {"metadata": metadata}

    res = gate.execute(l0_context)
    assert res.get("c06_role_gate_cleared") is True

    # Roster must be synchronized, not bypassed
    assert metadata["roster"][0]["role"] == "Principal Architect & Technology Strategist"
    assert "Amit Ayer (Principal Architect & Technology Strategist)" in metadata["attendees"]
    assert metadata["graph_role_verification_receipt"]["attendees_verified"]["Amit Ayer"]["role"] == "Principal Architect & Technology Strategist"


def test_c05_apply_hitl_role_resolution_updates_all_attendee_formats() -> None:
    # Test with dict attendees
    meta_dict = {
        "roster": [{"name": "Amit Ayer", "role": "Old Title", "org": "PartnerAdvisory"}],
        "attendees": {"PartnerAdvisory": ["Amit Ayer (Old Title)"]},
    }
    apply_hitl_role_resolution(
        meta_dict, "Amit Ayer", "Principal Architect & Technology Strategist", is_human_confirmed=False
    )
    assert meta_dict["roster"][0]["role"] == "Principal Architect & Technology Strategist"
    assert "Amit Ayer (Principal Architect & Technology Strategist)" in meta_dict["attendees"]["PartnerAdvisory"][0]

    # Test with string attendees
    meta_str = {
        "roster": "Amit Ayer (Old Title)",
        "attendees": "Amit Ayer (Old Title)",
    }
    apply_hitl_role_resolution(
        meta_str, "Amit Ayer", "Principal Architect & Technology Strategist", is_human_confirmed=False
    )
    assert "Amit Ayer (Principal Architect & Technology Strategist)" in meta_str["roster"]
    assert "Amit Ayer (Principal Architect & Technology Strategist)" in meta_str["attendees"]


def test_u0_ingestion_normalize_roster_entities_overwrites_compound_role() -> None:
    metadata = {
        "roster": [
            {
                "name": "Amit Ayer",
                "role": "Lead Client Partner / Principal Architect & Technology Strategist",
                "org": "PartnerAdvisory",
            }
        ],
        "attendees": {
            "PartnerAdvisory": ["Amit Ayer (Lead Client Partner / Principal Architect & Technology Strategist)"]
        },
        "hitl_role_resolutions": {
            "Amit Ayer": "Principal Architect & Technology Strategist",
        },
    }

    _normalize_roster_entities(metadata)

    assert metadata["roster"][0]["role"] == "Principal Architect & Technology Strategist"
    assert metadata["roster"][0]["status"] == "RESOLVED"
    assert "Amit Ayer (Principal Architect & Technology Strategist)" in metadata["attendees"]["PartnerAdvisory"][0]


def test_u0_ingress_batch_validate_preflight_syncs_roster(tmp_path: Path) -> None:
    # Seed SQLite ledger
    sqlite_path = tmp_path / "hitl_decision_ledger.sqlite"
    conn = create_sqlite_conn(sqlite_path, readonly=False)
    cur = conn.cursor()
    cur.executescript(HITL_TABLE_SCHEMA)
    cur.execute(
        """
        INSERT INTO hitl_decisions (
            entry_id, sequence_number, decision_id, category, operator_id,
            prompt, options, selected_option, selected_key, rationale,
            previous_entry_hash, recorded_at, metadata, entry_digest
        ) VALUES (
            'entry-sync-1', 1, 'dec-sync', 'role_clarification', 'operator',
            'Assign verified professional title to Amit Ayer', '[]', 'Principal Architect & Technology Strategist',
            'Principal Architect & Technology Strategist', 'Operator confirmed', '0000',
            '2026-10-16T21:36:00Z', '{"person_name": "Amit Ayer", "resolved_role": "Principal Architect & Technology Strategist"}', 'hsync'
        )
        """
    )
    conn.commit()
    conn.close()

    metadata = {
        "repo_root": str(tmp_path),
        "time": "10:00 AM - 10:30 AM ET",
        "roster": [{"name": "Amit Ayer", "role": "Lead Client Partner", "org": "PartnerAdvisory"}],
        "attendees": {"PartnerAdvisory": ["Amit Ayer (Lead Client Partner)"]},
        "attendees_confirmed_by_operator": True,
    }

    report = batch_validate_preflight_checks(metadata, raw_transcript="Amit Ayer: Hello team.")

    # Roster must be synchronized to confirmed role
    assert metadata["roster"][0]["role"] == "Principal Architect & Technology Strategist"
    assert "Amit Ayer (Principal Architect & Technology Strategist)" in metadata["attendees"]["PartnerAdvisory"][0]
    assert report.is_valid is True

"""
tests/test_wave2_ledger_uwg_convergence_unit.py

Unit test suite for Wave 2: HITL Ledger Ordering & UWG Persist-Back Convergence.
Verifies:
1. strip_org_suffix_from_role correctly strips known/passed organizations.
2. _load_ledger_role_resolutions enforces strict chronological precedence (21:36 UTC wins over 08:49 UTC).
3. _evaluate_single_role_fact rejects empty and placeholder roles upfront.
4. _clean_role_resolutions strips empty and invalid values.
5. persist_validated_role supersedes role_group assertions and sets verified/canonical states.
6. verify_ledger_backed_role_resolutions matches org-stripped roles.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.canonical_pipeline.speaker_role_coverage import (
    _evaluate_single_role_fact,
    _slugify,
)
from dual_pipeline.canonical_pipeline.transcript_resume_binding import _clean_role_resolutions
from dual_pipeline.canonical_pipeline.u0_ingress import _load_ledger_role_resolutions
from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
    strip_org_suffix_from_role,
    verify_ledger_backed_role_resolutions,
)
from dual_pipeline.storage.hitl_sqlite_store import HITL_TABLE_SCHEMA
from dual_pipeline.uwg_core.universal_write_gateway_role import persist_validated_role
from storage.connection import create_sqlite_conn
from tests.end_to_end.test_stakeholder_role_ssot_e2e import _seed_production_like_sandbox


def test_strip_org_suffix_from_role() -> None:
    # Comma separation with known org
    r1, o1 = strip_org_suffix_from_role("Principal Architect & Technology Strategist, PartnerAdvisory")
    assert r1 == "Principal Architect & Technology Strategist"
    assert o1 == "PartnerAdvisory"

    # Hyphen separation with known org
    r2, o2 = strip_org_suffix_from_role("Lead Client Partner - apex_global")
    assert r2 == "Lead Client Partner"
    assert o2 == "apex_global"

    # Parens separation
    r3, o3 = strip_org_suffix_from_role("Principal Architect (PartnerAdvisory)")
    assert r3 == "Principal Architect"
    assert o3 == "PartnerAdvisory"

    # Slash separation
    r4, o4 = strip_org_suffix_from_role("Enterprise Architect / PartnerAdvisory")
    assert r4 == "Enterprise Architect"
    assert o4 == "PartnerAdvisory"

    # Custom org explicitly provided
    r5, o5 = strip_org_suffix_from_role("Director of Engineering, AcmeCorp", org="AcmeCorp")
    assert r5 == "Director of Engineering"
    assert o5 == "AcmeCorp"

    # Role without org
    r6, o6 = strip_org_suffix_from_role("Principal Architect & Technology Strategist")
    assert r6 == "Principal Architect & Technology Strategist"
    assert o6 is None

    # Empty / none
    r7, o7 = strip_org_suffix_from_role("")
    assert r7 == ""
    assert o7 is None


def test_u0_ingress_ledger_chronological_ordering(tmp_path: Path) -> None:
    # Create SQLite ledger
    sqlite_path = tmp_path / "hitl_decision_ledger.sqlite"
    conn = create_sqlite_conn(sqlite_path, readonly=False)
    cur = conn.cursor()
    cur.executescript(HITL_TABLE_SCHEMA)

    # Insert earlier decision (08:49 UTC)
    cur.execute(
        """
        INSERT INTO hitl_decisions (
            entry_id, sequence_number, decision_id, category, operator_id,
            prompt, options, selected_option, selected_key, rationale,
            previous_entry_hash, recorded_at, metadata, entry_digest
        ) VALUES (
            'entry-1', 1, 'dec-1', 'role_clarification', 'operator',
            'Assign verified professional title to Amit Ayer', '[]', 'Lead Client Partner',
            'Lead Client Partner', 'Initial test title', '0000',
            '2026-10-16T08:49:00Z', '{"person_name": "Amit Ayer", "resolved_role": "Lead Client Partner"}', 'hash1'
        )
        """
    )

    # Insert later decision (21:36 UTC)
    cur.execute(
        """
        INSERT INTO hitl_decisions (
            entry_id, sequence_number, decision_id, category, operator_id,
            prompt, options, selected_option, selected_key, rationale,
            previous_entry_hash, recorded_at, metadata, entry_digest
        ) VALUES (
            'entry-2', 2, 'dec-2', 'role_clarification', 'operator',
            'Assign verified professional title to Amit Ayer', '[]', 'Principal Architect & Technology Strategist, PartnerAdvisory',
            'Principal Architect & Technology Strategist, PartnerAdvisory', 'Operator confirmed title', 'hash1',
            '2026-10-16T21:36:00Z', '{"person_name": "Amit Ayer", "resolved_role": "Principal Architect & Technology Strategist, PartnerAdvisory"}', 'hash2'
        )
        """
    )
    conn.commit()
    conn.close()

    # Also create a legacy JSON ledger with the older entry
    json_path = tmp_path / "hitl_decision_ledger.json"
    legacy_json = [
        {
            "payload": {
                "metadata": {
                    "speaker_role_resolutions": {
                        "Amit Ayer": {"role": "Lead Client Partner"}
                    }
                }
            },
            "timestamp": "2026-10-16T08:49:00Z",
            "sequence_number": 1,
        }
    ]
    json_path.write_text(json.dumps(legacy_json), encoding="utf-8")

    # Ingest metadata targeting Amit Ayer
    metadata = {
        "repo_root": str(tmp_path),
        "roster": ["Amit Ayer"],
    }

    resolved = _load_ledger_role_resolutions(metadata)
    assert resolved["Amit Ayer"] == "Principal Architect & Technology Strategist"
    assert metadata["speaker_role_resolutions"]["Amit Ayer"]["role"] == "Principal Architect & Technology Strategist"
    assert metadata["speaker_role_resolutions"]["Amit Ayer"]["is_human_confirmed"] is True


def test_speaker_role_coverage_empty_role_rejection() -> None:
    assert _slugify("") == ""
    assert _slugify("   ") == ""
    assert _slugify("Amit Ayer") == "amit_ayer"

    conn = create_sqlite_conn(":memory:", readonly=False)
    # Empty role
    res_empty = _evaluate_single_role_fact(conn, "Test Person", "", None)
    assert res_empty["disposition"] == "REJECTED"
    assert res_empty["verified_in_graph"] is False

    # Placeholder title
    res_ph = _evaluate_single_role_fact(conn, "Test Person", "[Title Pending]", None)
    assert res_ph["disposition"] == "REJECTED"
    assert res_ph["verified_in_graph"] is False
    conn.close()


def test_transcript_resume_binding_clean_role_resolutions() -> None:
    resolutions = {
        "User A": "",
        "User B": "Principal Architect",
        "User C": "None",
        "User D": None,
        "User E": {"role": "Lead Client Partner"},
        "User F": {"role": ""},
    }
    cleaned = _clean_role_resolutions(resolutions)
    assert cleaned == {
        "User B": "Principal Architect",
        "User E": "Lead Client Partner",
    }


def test_uwg_role_persistence_and_supersession(tmp_path: Path) -> None:
    db_path = _seed_production_like_sandbox(tmp_path / "sandbox_graph.sqlite")
    conn = create_sqlite_conn(db_path, readonly=False)
    cur = conn.cursor()

    # Ensure person node exists
    cur.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, active) "
        "VALUES ('person_amit_ayer', 'stakeholder', 'Amit Ayer', 'Amit Ayer', 1)"
    )

    # Create competing role_group node and older assertion
    cur.execute(
        "INSERT INTO node (node_id, node_type, canonical_name, display_name, active) "
        "VALUES ('role_old_lcp', 'role_group', 'Lead Client Partner', 'Lead Client Partner', 1)"
    )
    cur.execute(
        "INSERT INTO assertion ("
        "assertion_id, subject_node_id, predicate, object_node_id, currentness_state, "
        "validation_state, confidence_score, canonical_status"
        ") VALUES ("
        "'asrt_old_1', 'person_amit_ayer', 'RELATED_TO', 'role_old_lcp', 'current', "
        "'verified', 0.9, 'candidate'"
        ")"
    )
    conn.commit()
    conn.close()

    # Ratify new role via UWG
    receipt = persist_validated_role(
        db_path,
        canonical_name="Amit Ayer",
        selected_role="Principal Architect & Technology Strategist",
    )
    assert receipt["status"] == "PERSISTED_SSOT"
    assert receipt["validation_state"] == "verified"
    assert receipt["confidence_score"] == 1.0
    assert "asrt_old_1" in receipt["superseded_assertion_ids"]

    # Verify database state
    verify_conn = create_sqlite_conn(db_path, readonly=True)
    v_cur = verify_conn.cursor()

    # Check new assertion
    v_cur.execute(
        "SELECT currentness_state, validation_state, canonical_status FROM assertion WHERE assertion_id = ?",
        (receipt["persisted_assertion_id"],),
    )
    new_row = v_cur.fetchone()
    assert new_row[0] == "current"
    assert new_row[1] == "verified"
    assert new_row[2] == "canonical"

    # Check old assertion is superseded/historical
    v_cur.execute(
        "SELECT currentness_state, superseded_by_assertion_id FROM assertion WHERE assertion_id = 'asrt_old_1'"
    )
    old_row = v_cur.fetchone()
    assert old_row[0] in ("historical", "superseded")
    assert old_row[1] == receipt["persisted_assertion_id"]
    verify_conn.close()


def test_speaker_resolution_contract_verify_ledger_backed(tmp_path: Path) -> None:
    ledger_path = tmp_path / "hitl_decision_ledger.json"
    ledger_content = [
        {
            "metadata": {
                "person_name": "Amit Ayer",
                "resolved_role": "Principal Architect & Technology Strategist, PartnerAdvisory",
            },
            "recorded_at": "2026-10-16T21:36:00Z",
            "sequence_number": 1,
        }
    ]
    ledger_path.write_text(json.dumps(ledger_content), encoding="utf-8")

    metadata = {
        "repo_root": str(tmp_path),
        "hitl_role_resolutions": {
            "Amit Ayer": "Principal Architect & Technology Strategist",
        },
        "strict_ledger_role_gate": True,
    }

    verified, rejected = verify_ledger_backed_role_resolutions(metadata, repo_root=tmp_path)
    assert "Amit Ayer" in verified
    assert verified["Amit Ayer"] == "Principal Architect & Technology Strategist"
    assert len(rejected) == 0

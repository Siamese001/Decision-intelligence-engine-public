"""
tests/end_to_end/test_hitl_dialogue_grounding_regression_e2e.py

End-to-End Regression Test: 08-04-2026 Google / PartnerAdvisory / Apex Global Weekly Sync.
Pins the exact Amanda regression across both full pipeline (Stage 01–03) and
notes-mode (--notes / Stage 02–03) pipelines:
1. Amanda speaks in raw dialogue without spoken surname or title.
2. Pre-populating sidecar metadata or GraphDB aliases strictly FAILS to bypass Stage U0 attendee gate.
3. GraphDB role (Solutions Consultant) strictly FAILS to auto-approve without operator confirmation.
4. Cryptographic operator confirmation and ledger resolution cleanly clears all gates.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
)
from dual_pipeline.transcript_stages_core.attendee_gate import (
    record_attendee_confirmation,
    verify_mandatory_attendee_confirmation,
)
from dual_pipeline.transcript_stages_core.c05_title_gate import apply_hitl_role_resolution
from dual_pipeline.transcript_stages_core.c06_graphdb_role_gate import (
    C06GraphDBRoleVerificationGate,
)
from dual_pipeline.transcript_stages_core.c0_gates import (
    C0ConformanceGate,
    _check_single_role_hitl,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError
from dual_pipeline.transcript_stages_core.u0_ingestion import U0IngestionBoundary


def _seed_google_partner_advisory_graph(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT,
            canonical_name TEXT,
            display_name TEXT,
            description TEXT,
            status TEXT,
            active INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            claim_text TEXT,
            is_quarantined INTEGER
        )
    """)
    cur.execute("""
        INSERT INTO node VALUES
        ('person:amanda_knarr', 'person', 'Amanda Knarr', 'Amanda Knarr', 'Role: Solutions Consultant. Affiliation label(s): PartnerAdvisory.', 'active', 1),
        ('person:lan_tran', 'person', 'Lan Tran', 'Lan Tran', 'Role: Senior Engineer. Affiliation label(s): PartnerAdvisory.', 'active', 1),
        ('person:amit_ayer', 'person', 'Amit Ayer', 'Amit Ayer', 'Role: Chief Architecture Officer. Affiliation label(s): PartnerAdvisory.', 'active', 1)
    """)
    cur.execute("""
        INSERT INTO assertion VALUES
        ('a_amanda_1', 'person:amanda_knarr', 'HAS_ROLE', NULL, 'Solutions Consultant', 'Amanda Knarr Solutions Consultant', 0),
        ('a_lan_1', 'person:lan_tran', 'HAS_ROLE', NULL, 'Senior Engineer', 'Lan Tran Senior Engineer', 0),
        ('a_amit_1', 'person:amit_ayer', 'HAS_ROLE', NULL, 'Chief Architecture Officer', 'Amit Ayer Chief Architecture Officer', 0)
    """)
    conn.commit()
    conn.close()


@pytest.mark.e2e
def test_e2e_amanda_full_pipeline_raw_transcript_regression(tmp_path: Path, monkeypatch):
    """
    Reproduces authentic 08-04-2026 Google / PartnerAdvisory / Apex Global meeting:
    Raw transcript has turns by 'Amanda', sidecar metadata has 'Amanda Knarr',
    GraphDB has 'Solutions Consultant'.
    Verifies that neither surname nor title auto-approves without HITL.
    """
    db_p = tmp_path / "graph.sqlite"
    _seed_google_partner_advisory_graph(db_p)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    monkeypatch.setattr(resolver, "_get_active_graph_path", lambda: db_p)
    monkeypatch.setattr("dual_pipeline.transcript_metadata_core.entity_resolver._RESOLVER", resolver)
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda name, *args, **kwargs: resolver.resolve(name),
    )

    raw_transcript = (
        "Amanda: Thanks for joining today's cloud infrastructure sync.\n"
        "Lan: Let's review the BigQuery and Cloud Run architecture.\n"
        "Amit: Sounds good, let's align on next steps.\n"
    )
    metadata: Dict[str, Any] = {
        "meeting_name": "Google PartnerAdvisory Apex Global Weekly",
        "date": "08/04/2026",
        "time": "10:00 AM - 10:30 AM ET",
        "project": "project_nexus",
        "input_path": str(tmp_path / "google_partner_advisory_08-04-2026.txt"),
        "source_type": "RAW_TRANSCRIPT",
        "repo_root": str(tmp_path),
        # Sidecar pre-populated with full names; Lan and Amit have explicit stated roles
        "attendees": ["Amanda Knarr", "Lan Tran (Senior Engineer)", "Amit Ayer (Chief Architecture Officer)"],
    }

    # Step 1: Stage U0 attendee gate MUST halt because 'Amanda' spoke in dialogue without surname
    with pytest.raises(HITLRequiredError) as exc_att:
        verify_mandatory_attendee_confirmation(metadata, raw_transcript)
    err_att = exc_att.value
    assert err_att.hitl_type == "attendee_confirmation"
    assert "Amanda" in err_att.unmapped_speakers

    # Step 2: Operator confirms attendee list (Lan and Amit roles verified, Amanda role unstated)
    rcpt = record_attendee_confirmation(
        metadata,
        ["Amanda Knarr", "Lan Tran (Senior Engineer)", "Amit Ayer (Chief Architecture Officer)"],
        rationale="Operator verified Amanda Knarr attended 08-04-2026 sync",
        repo_root=tmp_path,
    )
    assert rcpt["status"] == "RESOLVED_HITL"
    verify_mandatory_attendee_confirmation(metadata, raw_transcript)

    # Step 3: Stage C0 / C0.6 role gate MUST halt because Amanda's title was unstated in dialogue
    l0_ctx = {
        "metadata": metadata,
        "transcript_text": raw_transcript,
        "repo_root": tmp_path,
    }
    c06_gate = C06GraphDBRoleVerificationGate(repo_root=tmp_path)
    with pytest.raises(HITLRequiredError) as exc_c06:
        c06_gate.execute(l0_ctx)
    err_c06 = exc_c06.value
    assert err_c06.hitl_type == "graphdb_role_verification"
    assert "role:Amanda Knarr" in err_c06.missing_fields
    rec_opt = next((o for o in err_c06.options if o.get("is_recommended")), None)
    assert rec_opt is not None
    assert rec_opt["role"] == "Solutions Consultant"

    # Step 4: Operator confirms role in ledger
    apply_hitl_role_resolution(
        metadata,
        "Amanda Knarr",
        "Solutions Consultant",
        organization="PartnerAdvisory",
        is_human_confirmed=True,
        repo_root=tmp_path,
    )
    # Stage C0.6 now clears cleanly
    c06_res = c06_gate.execute(l0_ctx)
    assert c06_res.get("c06_role_gate_cleared") is True
    assert metadata["graph_role_verification_receipt"]["attendees_verified"]["Amanda Knarr"]["status"] == "HITL_RESOLVED"


@pytest.mark.e2e
def test_e2e_amanda_notes_mode_regression(tmp_path: Path, monkeypatch):
    """
    Verifies that in notes mode (--notes / Stage 02-03), single-token dialogue
    halts at Stage U0 and does not auto-resolve without operator confirmation.
    """
    db_p = tmp_path / "graph.sqlite"
    _seed_google_partner_advisory_graph(db_p)

    resolver = PreSynthesisEntityResolver(repo_root=tmp_path)
    monkeypatch.setattr(resolver, "_get_active_graph_path", lambda: db_p)
    monkeypatch.setattr("dual_pipeline.transcript_metadata_core.entity_resolver._RESOLVER", resolver)
    monkeypatch.setattr(
        "dual_pipeline.transcript_metadata_core.entity_resolver.resolve_person_entity",
        lambda name, *args, **kwargs: resolver.resolve(name),
    )

    raw_notes = (
        "# Meeting Notes 08-04-2026\n"
        "Amanda outlined Google Cloud deployment timelines.\n"
        "Lan walked through data pipeline dependencies.\n"
    )
    metadata: Dict[str, Any] = {
        "meeting_name": "Google Cloud Workstream Notes",
        "date": "08/04/2026",
        "time": "10:00 AM ET",
        "source_type": "copilot_notes",
        "is_notes": True,
        "notes_mode": True,
        "project": "project_nexus",
        "repo_root": str(tmp_path),
        "roster": ["Amanda", "Lan"],
    }

    # Step 1: Attendee gate halts on single-token attendee in notes mode
    with pytest.raises(HITLRequiredError) as exc_u0:
        verify_mandatory_attendee_confirmation(metadata, raw_notes)
    assert exc_u0.value.hitl_type == "attendee_confirmation"
    assert "Amanda" in exc_u0.value.unmapped_speakers

    # Step 2: In notes mode, U0 roster completeness halts on unverified single-token roles
    with pytest.raises(HITLRequiredError) as exc_comp:
        U0IngestionBoundary._inspect_roster_completeness(metadata, raw_notes)
    assert exc_comp.value.hitl_type in ("title_clarification", "preflight_cascade")

    # Step 3: Operator confirms attendees
    record_attendee_confirmation(
        metadata,
        ["Amanda Knarr", "Lan Tran (Senior Engineer)"],
        repo_root=tmp_path,
    )
    verify_mandatory_attendee_confirmation(metadata, raw_notes)

    # Step 4: C0.6 gate halts on unconfirmed Amanda Knarr role
    l0_ctx = {
        "metadata": metadata,
        "transcript_text": raw_notes,
        "repo_root": tmp_path,
    }
    c06_gate = C06GraphDBRoleVerificationGate(repo_root=tmp_path)
    with pytest.raises(HITLRequiredError) as exc_c06:
        c06_gate.execute(l0_ctx)
    assert exc_c06.value.hitl_type == "graphdb_role_verification"
    assert "role:Amanda Knarr" in exc_c06.value.missing_fields

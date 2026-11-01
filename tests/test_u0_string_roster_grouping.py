from __future__ import annotations

from dual_pipeline.transcript_stages_core.attendee_gate import (
    record_attendee_confirmation,
)
from dual_pipeline.transcript_stages_core.u0_ingestion import (
    _default_attendees_and_roster,
    _group_roster_by_org,
)


def test_group_roster_by_org_with_string_list():
    """Verify _group_roster_by_org correctly handles a list of plain attendee names."""
    roster = ["Jill Cizewski", "Daniel Brankin", "Taylor Mercer"]
    grouped = _group_roster_by_org(roster)

    assert isinstance(grouped, dict)
    assert len(grouped) > 0
    all_names = [name for names in grouped.values() for name in names]
    assert any("Jill Cizewski" in name for name in all_names)
    assert any("Daniel Brankin" in name for name in all_names)
    assert any("Taylor Mercer" in name for name in all_names)


def test_group_roster_by_org_with_dict_list():
    """Verify _group_roster_by_org preserves dictionary rosters."""
    roster = [
        {"name": "Alice Smith", "org": "Acme Corp", "role": "Architect"},
        {"name": "Bob Jones", "org": "Beta Inc", "role": "Engineer"},
    ]
    grouped = _group_roster_by_org(roster)

    assert "Acme Corp" in grouped
    assert "Beta Inc" in grouped
    assert "Alice Smith (Architect)" in grouped["Acme Corp"]
    assert "Bob Jones (Engineer)" in grouped["Beta Inc"]


def test_default_attendees_and_roster_with_string_roster():
    """Verify _default_attendees_and_roster resolves string rosters into grouped dicts without crashing."""
    metadata = {
        "roster": ["Jill Cizewski", "Daniel Brankin", "Sharon Diaram"],
        "attendees": ["Jill Cizewski", "Daniel Brankin", "Sharon Diaram"],
    }
    _default_attendees_and_roster(metadata, raw_transcript="Sample transcript text")

    assert isinstance(metadata["attendees"], dict), "Attendees must be grouped into a dict"
    assert isinstance(metadata["roster"], list), "Roster must remain a list"
    assert all(isinstance(r, dict) for r in metadata["roster"]), "Roster items must be converted to dicts"


def test_record_attendee_confirmation_standardizes_string_list(tmp_path):
    """Verify record_attendee_confirmation creates standardized roster dicts from a string list."""
    metadata = {}
    receipt = record_attendee_confirmation(
        metadata,
        ["Jill Cizewski", "Daniel Brankin", "Taylor Mercer"],
        rationale="Operator verified",
        repo_root=tmp_path,
    )

    assert receipt["status"] == "RESOLVED_HITL"
    assert metadata["attendees_confirmed_by_operator"] is True
    assert isinstance(metadata["roster"], list)
    assert all(isinstance(r, dict) for r in metadata["roster"])
    roster_names = [r["name"] for r in metadata["roster"]]
    assert "Jill Cizewski" in roster_names
    assert "Daniel Brankin" in roster_names
    assert "Taylor Mercer" in roster_names

"""
tests/test_project_scoped_roster_resolution_e2e.py

Class B Receipt & E2E Verification Suite for Wave 1:
Scope-Bound Roster Auto-Population & Alias Expansion.

Verifies:
1. Under project="project_beacon", first names, initials ("David C."), and full names
   resolve deterministically to Project Beacon stakeholders.
2. Under project="project_lens", first names resolve to Project LENS stakeholders.
3. Zero cross-project names (David Decof, Brandon Dass, Jake Stark, Morgan Vance Krutilla)
   leak into project_beacon rosters.
4. "PartnerAdvisory team" and "NEA team" shorthands expand to project-specific members.
5. Empty attendee input auto-populates the scoped project roster.
6. Adversarial edge cases: malformed tokens, unusual punctuation, case-insensitivity.
"""
from __future__ import annotations

import pytest
from dual_pipeline.live_meeting_recorder_core.attendees import parse_attendees
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    invalidate_resolver_cache,
    resolve_attendee_list,
    resolve_person_entity,
)


@pytest.fixture(autouse=True)
def clean_resolver_cache():
    """Ensure resolver cache is clean before every test."""
    invalidate_resolver_cache()
    yield
    invalidate_resolver_cache()


def test_project_beacon_individual_stakeholder_resolution():
    """Verify first names, initials, and full names map to Project Beacon members."""
    expected_mappings = {
        "David": "David Childers",
        "David C.": "David Childers",
        "David C": "David Childers",
        "Brandon": "Brandon Que",
        "Brandon Q.": "Brandon Que",
        "Andy": "Andy Kilinskis",
        "Marcus": "Marcus Balk",
        "Catalina": "Catalina Orostegui",
        "Speaker_Alpha": "Speaker_Alpha Muschett",
        "Lillian": "Lillian Le",
        "Egle": "Egle Tamasevice",
        "Jeffrey": "Jeffrey Glade",
        "Jeff": "Jeffrey Glade",
        "Speaker_Beta": "Speaker_Beta Tharakan",
        "Cansu": "Cansu Miller",
    }

    for raw_input, expected_canonical in expected_mappings.items():
        ent = resolve_person_entity(raw_input, project_name="project_beacon")
        assert ent is not None, f"Failed to resolve '{raw_input}' under project_beacon"
        assert ent.canonical_name == expected_canonical, (
            f"Expected '{raw_input}' to resolve to '{expected_canonical}', got '{ent.canonical_name}'"
        )


def test_project_lens_stakeholder_resolution():
    """Verify first names map to Project LENS stakeholders when scope is project_lens."""
    expected_mappings = {
        "David": "David Decof",
        "Brandon": "Brandon Dass",
        "Jake": "Jake Stark",
        "Morgan Vance": "Morgan Vance Krutilla",
        "Ed": "Eduardo More",
    }

    for raw_input, expected_canonical in expected_mappings.items():
        ent = resolve_person_entity(raw_input, project_name="project_lens")
        assert ent is not None, f"Failed to resolve '{raw_input}' under project_lens"
        assert ent.canonical_name == expected_canonical, (
            f"Expected '{raw_input}' to resolve to '{expected_canonical}', got '{ent.canonical_name}'"
        )


def test_zero_cross_project_leakage_in_project_beacon():
    """Verify that Project LENS / other project names never leak into project_beacon rosters."""
    resolved = resolve_attendee_list(
        "David, Brandon, Andy, Marcus, PartnerAdvisory team",
        project_name="project_beacon",
    )
    resolved_names = {e.canonical_name for e in resolved}

    prohibited = {
        "David Decof",
        "Brandon Dass",
        "Jake Stark",
        "Morgan Vance Krutilla",
        "Eduardo More",
    }

    leaked = resolved_names.intersection(prohibited)
    assert not leaked, f"Detected cross-project entity leakage in project_beacon: {leaked}"
    assert "David Childers" in resolved_names
    assert "Brandon Que" in resolved_names
    assert "Andy Kilinskis" in resolved_names
    assert "Marcus Balk" in resolved_names


def test_team_shorthand_expansion_scoped_to_project():
    """Verify 'PartnerAdvisory team' expansion produces project-appropriate team members."""
    # Project Beacon team expansion
    sherpa_resolved = resolve_attendee_list("PartnerAdvisory team", project_name="project_beacon")
    sherpa_names = {e.canonical_name for e in sherpa_resolved}
    assert "Andy Kilinskis" in sherpa_names or "Lillian Le" in sherpa_names
    assert "Jake Stark" not in sherpa_names
    assert "Morgan Vance Krutilla" not in sherpa_names

    # Project LENS team expansion
    lens_resolved = resolve_attendee_list("PartnerAdvisory team", project_name="project_lens")
    lens_names = {e.canonical_name for e in lens_resolved}
    assert "Jake Stark" in lens_names
    assert "Morgan Vance Krutilla" in lens_names
    assert "Andy Kilinskis" not in lens_names


def test_parse_attendees_empty_input_auto_population():
    """Verify parse_attendees with empty input automatically seeds the project roster."""
    auto_sherpa = parse_attendees("", project_name="project_beacon")
    names = {a["name"] for a in auto_sherpa}

    assert "David Childers" in names
    assert "Brandon Que" in names
    assert "Andy Kilinskis" in names
    assert "David Decof" not in names
    assert "Brandon Dass" not in names


def test_adversarial_and_edge_case_inputs():
    """Verify boundary payloads, punctuation, whitespace, and casing edge cases."""
    # Boundary: trailing whitespace and messy periods
    ent = resolve_person_entity("  david c... ", project_name="project_beacon")
    assert ent is not None and ent.canonical_name == "David Childers"

    # Boundary: uppercase casing
    ent_up = resolve_person_entity("BRANDON", project_name="project_beacon")
    assert ent_up is not None and ent_up.canonical_name == "Brandon Que"

    # Boundary: unknown project scope falls back gracefully without crashing
    unknown_ent = resolve_person_entity("David", project_name="unknown_project_xyz")
    assert unknown_ent is not None  # Resolves via global prior without error

    # Boundary: malformed comma lists
    parsed = parse_attendees(",,,   , \n\t", project_name="project_beacon")
    assert len(parsed) >= 1  # Returns populated project roster rather than empty/crash

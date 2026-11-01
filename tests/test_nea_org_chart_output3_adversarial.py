"""Adversarial and boundary test suite for NEE/NEA visual org chart extraction."""
from __future__ import annotations

import pytest
from pathlib import Path

from dual_pipeline.canonical_pipeline.visual_org_extractor import (
    _parse_apm_roster,
    _parse_hierarchy_chain,
    extract_visual_org_projection,
)


def test_boundary_empty_envelope_and_zero_text():
    """Verify extractor handles empty or minimal envelope inputs gracefully."""
    empty_envelope = {
        "meeting": {"meeting_id": "mtg-empty", "title": "Empty Document"},
        "speakers": [],
    }
    projection = extract_visual_org_projection(empty_envelope)
    assert projection["schema_version"] == "5.7"
    assert projection["meeting_id"] == "mtg-empty"
    assert isinstance(projection["graph_mutations"]["entity_upserts"], list)
    assert isinstance(projection["graph_mutations"]["relationship_signals"], list)


def test_malformed_apm_roster_parsing():
    """Verify parsing robustness against malformed lines, missing numbers, or corrupted portfolios."""
    malformed_text = """
    Random header without meaning
    1. Carlos E Sanchez - Enterprise Business & ERP
    2. Invalid Line Without Delimiter
    3. Ben Miron -
    4. - Missing Name Portfolio
    5. Grace Kurian - Nuclear & Wind Asset Mgmt; Generation
    Some trailing footer text
    """
    entities, rels, evidence = _parse_apm_roster(malformed_text)

    # Should successfully extract valid lines (Carlos, Grace) and skip corrupted ones
    person_names = [e["name"] for e in entities if e["type"] == "person"]
    assert "Carlos E Sanchez" in person_names
    assert "Grace Kurian" in person_names
    assert len(person_names) >= 2


def test_duplicate_person_deduplication():
    """Verify that multiple occurrences or repeated names in OCR output do not create duplicate person IDs."""
    roster_with_duplicates = """
    1. Ben Ben Miron - IT Corporate Systems
    2. Ben Miron - IT Corporate Systems
    3. Carlos E Sanchez - Enterprise ERP
    4. Carlos E Sanchez - Operations Analytics
    """
    entities, rels, evidence = _parse_apm_roster(roster_with_duplicates)
    person_ids = [e["id"] for e in entities if e["type"] == "person"]

    # Each person ID should appear exactly once in entities
    assert len(person_ids) == len(set(person_ids))
    assert "person:ben-miron" in person_ids
    assert "person:carlos-e-sanchez" in person_ids


def test_single_person_hierarchy_boundary():
    """Verify that a single-person hierarchy generates a valid entity without invalid REPORTS_TO self-edges."""
    single_chain = [("Richard Argentieri", "President NEA", "Apex Global Analytics")]
    entities, rels, evidence = _parse_hierarchy_chain(single_chain, "ev-single", "Single leader")

    assert len(entities) == 2  # person + org
    assert any(e["id"] == "person:richard-argentieri" for e in entities)
    assert any(e["id"] == "org:apex_global-analytics" for e in entities)

    # No REPORTS_TO self-edge
    reports_to = [r for r in rels if r["relationship"] == "REPORTS_TO"]
    assert len(reports_to) == 0


def test_invariant_output_containment():
    """Verify that projection structure strictly forbids output_1 and output_2 fields."""
    envelope = {
        "meeting": {"meeting_id": "mtg-test", "title": "Test Envelope"},
        "speakers": [{"statements": [{"text": "1. Carlos E Sanchez - Enterprise Systems"}]}],
    }
    projection = extract_visual_org_projection(envelope)

    assert "output_1" not in projection
    assert "output_2" not in projection
    assert "transcript" not in projection
    assert "executive_recap" not in projection
    assert projection["source_type"] == "visual_document"

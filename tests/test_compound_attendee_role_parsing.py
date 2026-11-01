"""Tests for compound attendee role parsing and nickname resolution."""
from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import clean_attendee_entry
from dual_pipeline.transcript_stages_core.attribution_builder import (
    AttributionSummaryBuilder,
    _parse_person_entry,
)


def test_clean_attendee_entry_compound_parenthetical():
    name, role, org = clean_attendee_entry("Eduardo (Ed) Juan More (Atlas Lead / Rule Specialist)")
    assert name == "Eduardo Juan More"
    # _extract_effective_attendee_role selects primary role before slash delimiter
    assert role == "Atlas Lead"
    assert "Apex Global" in org


def test_parse_person_entry_compound_parenthetical():
    res = _parse_person_entry("Eduardo (Ed) Juan More (Atlas Lead / Rule Specialist)", "Apex Global Enterprises (NEA)")
    assert res["name"] == "Eduardo (Ed) Juan More"
    assert res["role"] == "Atlas Lead / Rule Specialist"
    assert res["org"] == "Apex Global Enterprises (NEA)"


def test_build_attribution_summary_compound_nickname_and_turns():
    dialogue = """
### [00:00:10]

**Ed Moore:** Testing the Maximo rule validation.

### [00:00:20]

**Ed Moore:** The asset tags look correct.

### [00:00:30]

**Jen Pan:** Thanks Ed.
"""
    metadata = {
        "speaker_map": {"Eduardo Juan More": "Ed Moore"},
        "attendees": {
            "Apex Global Enterprises (NEA)": [
                "Eduardo (Ed) Juan More (Atlas Lead / Rule Specialist)",
                "Alexandra Coro (UAT Lead)",
            ],
            "PartnerAdvisory": [
                "Jen Pan (Product Manager)",
            ],
        },
    }
    summary = AttributionSummaryBuilder.build_summary(metadata, dialogue_text=dialogue)
    assert "| Eduardo Juan More | Apex Global Enterprises (NEA) | Atlas Lead | 2 | HIGH |" in summary
    assert "| Alexandra Coro | Apex Global Enterprises (NEA) | UAT Lead | 0 | HIGH |" in summary
    assert "| Jen Pan | PartnerAdvisory | Product Manager | 1 | HIGH |" in summary

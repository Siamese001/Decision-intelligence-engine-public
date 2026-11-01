"""Unit tests for transcript attendee parser and header extraction core."""
from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
    build_roster_from_attendees,
    clean_attendee_entry,
    extract_attendees_from_header_block,
    extract_speakers_from_dialogue_turns,
    format_company_grouped_attendees,
    heal_attendees_block,
    is_blacklisted_attendee,
)
from dual_pipeline.transcript_metadata_helper import scaffold_meeting_metadata


def test_is_blacklisted_attendee():
    assert is_blacklisted_attendee("Date") is True
    assert is_blacklisted_attendee("Time") is True
    assert is_blacklisted_attendee("Attendees") is True
    assert is_blacklisted_attendee("**Attendees:**") is True
    assert is_blacklisted_attendee("PartnerAdvisory") is True
    assert is_blacklisted_attendee("Participant") is True
    assert is_blacklisted_attendee("Apex Global Enterprises") is True
    assert is_blacklisted_attendee("Amit Ayer") is False
    assert is_blacklisted_attendee("harper Hoffman") is False
    assert is_blacklisted_attendee("Brett Gavagni") is False
    assert is_blacklisted_attendee("Jennifer Pan") is False


def test_clean_attendee_entry():
    name, role, org = clean_attendee_entry("harper Hoffman (Product & Business Lead)")
    assert name == "harper Hoffman"
    assert role == "Product & Business Lead"

    name2, role2, org2 = clean_attendee_entry("Amit Ayer (PartnerAdvisory, Lead Enterprise Architect) amit.ayer@consulting.example.com")
    assert name2 == "Amit Ayer"
    assert "PartnerAdvisory" in role2 or org2 == "PartnerAdvisory"
    assert "@" not in name2


def test_extract_attendees_from_nested_header_block():
    header = """# Meeting Notes
Date: 09/14/2026
Time: 05:02 PM - 05:39 PM ET
Attendees:
- Apex Global Enterprises (NEA):
  - harper Hoffman (Product & Business Lead)
  - Brett Gavagni (Architecture & Systems Lead)
- PartnerAdvisory:
  - Jennifer Pan (Lead Engineer / Solution Builder)
  - Alex Sanchez (Solution Architect / Tech Lead)
  - Amit Ayer (Lead Enterprise Architect)
---
## Discussion
"""
    result = extract_attendees_from_header_block(header)
    assert result is not None
    assert "Apex Global Enterprises (NEA)" in result
    assert "PartnerAdvisory" in result
    assert any("harper Hoffman" in x for x in result["Apex Global Enterprises (NEA)"])
    assert any("Jennifer Pan" in x for x in result["PartnerAdvisory"])


def test_extract_attendees_from_inline_header_block():
    header = """Date: 09/14/2026
Time: 05:02 PM - 05:39 PM ET (Duration: ~37m)
Attendees:
- Apex Global Enterprises (NEA): harper Hoffman (Product & Business Lead), Brett Gavagni (Architecture & Systems Lead)
- PartnerAdvisory: Jennifer Pan (Lead Engineer / Solution Builder), Alex Sanchez (Solution Architect / Tech Lead), Amit Ayer (Lead Enterprise Architect)

---
"""
    result = extract_attendees_from_header_block(header)
    assert result is not None
    assert "Apex Global Enterprises (NEA)" in result
    assert "PartnerAdvisory" in result
    nea_members = result["Apex Global Enterprises (NEA)"]
    assert any("harper Hoffman" in x for x in nea_members)
    assert any("Brett Gavagni" in x for x in nea_members)


def test_format_company_grouped_attendees():
    att = {
        "Apex Global Enterprises (NEA)": [
            "harper Hoffman (Product & Business Lead)",
            "Brett Gavagni (Architecture & Systems Lead)",
        ],
        "PartnerAdvisory": [
            "Jennifer Pan (Lead Engineer / Solution Builder)",
            "Alex Sanchez (Solution Architect / Tech Lead)",
            "Amit Ayer (Lead Enterprise Architect)",
        ],
    }
    lines = format_company_grouped_attendees(att)
    assert len(lines) == 2
    assert lines[0].startswith("- Apex Global Enterprises (NEA): harper Hoffman")
    assert lines[1].startswith("- PartnerAdvisory: Jennifer Pan")


def test_build_roster_from_attendees():
    att = {
        "Apex Global Enterprises (NEA)": [
            "harper Hoffman (Product & Business Lead)",
        ],
        "PartnerAdvisory": [
            "Amit Ayer (Lead Enterprise Architect)",
        ],
    }
    roster = build_roster_from_attendees(att)
    assert len(roster) == 2
    assert roster[0]["name"] == "harper Hoffman"
    assert roster[0]["org"] == "Apex Global Enterprises (NEA)"
    assert roster[1]["name"] == "Amit Ayer"
    assert roster[1]["org"] == "PartnerAdvisory"


def test_extract_speakers_from_dialogue_turns():
    transcript = """
[00:00:04] Yan Gobeil: Let's get started today.
[00:00:15] Drew Oakley: Thanks Yan, looking forward to the discussion.
**Brett Gavagni:** I want to raise a concern about observability.
## **harper Hoffman**
I agree with Brett.
"""
    speakers = extract_speakers_from_dialogue_turns(transcript)
    assert "Yan Gobeil" in speakers
    assert "Drew Oakley" in speakers
    assert "Brett Gavagni" in speakers
    assert "harper Hoffman" in speakers
    assert "Date" not in speakers
    assert "Time" not in speakers


def test_heal_attendees_block_malformed_participants():
    malformed = """# Subject: Test Meeting | 09/14/2026

Date: 09/14/2026
Time: 10:00 AM ET
Attendees:
- **Participant:** harper Hoffman
- **Participant:** Brett Gavagni
- **Participant:** Amit Ayer

---

## Meeting Objective
Test objective.
"""
    roster = [
        {"name": "harper Hoffman", "role": "Product Lead", "org": "Apex Global Enterprises (NEA)"},
        {"name": "Brett Gavagni", "role": "Architect", "org": "Apex Global Enterprises (NEA)"},
        {"name": "Amit Ayer", "role": "Lead Architect", "org": "PartnerAdvisory"},
    ]
    healed, changed = heal_attendees_block(malformed, roster=roster)
    assert changed is True
    assert "- **Participant:**" not in healed
    assert "- Apex Global Enterprises (NEA): harper Hoffman (Product Lead), Brett Gavagni (Architect)" in healed
    assert "- PartnerAdvisory: Amit Ayer (Lead Architect)" in healed


def test_heal_attendees_block_missing():
    text = """# Subject: Test Meeting | 09/14/2026

Date: 09/14/2026
Time: 10:00 AM ET

---

## Meeting Objective
Test objective.
"""
    roster = [
        {"name": "Amit Ayer", "role": "Lead Architect", "org": "PartnerAdvisory"},
    ]
    healed, changed = heal_attendees_block(text, roster=roster)
    assert changed is True
    assert "Attendees:" in healed
    assert "- PartnerAdvisory: Amit Ayer (Lead Architect)" in healed


def test_scaffold_meeting_metadata_nested_markdown(tmp_path):
    text = """# Subject: Test Recap | 09/14/2026

Date: 09/14/2026
Time: 05:02 PM - 05:39 PM ET
Attendees:
- Apex Global Enterprises (NEA):
  - harper Hoffman (Product & Business Lead)
  - Brett Gavagni (Architecture & Systems Lead)
- PartnerAdvisory:
  - Amit Ayer (Lead Enterprise Architect)
---
## Content
"""
    p = tmp_path / "sample.md"
    p.write_text(text, encoding="utf-8")
    meta = scaffold_meeting_metadata(
        input_path=str(p),
        meeting_name="Test Catchup",
        date_str="09/14/2026",
        time_str="05:02 PM - 05:39 PM ET",
        attendees=None,
    )
    assert meta["date"] == "09/14/2026"
    assert "Apex Global Enterprises (NEA)" in meta["attendees"]
    assert "PartnerAdvisory" in meta["attendees"]
    assert len(meta["roster"]) >= 3
    roster_names = [r["name"] for r in meta["roster"]]
    assert "harper Hoffman" in roster_names
    assert "Date" not in roster_names
    assert "Time" not in roster_names
    assert "PartnerAdvisory" not in roster_names


def test_pre_synthesis_entity_resolution():
    name, role, org = clean_attendee_entry("Jen Pan")
    assert name == "Jen Pan"
    assert org == "PartnerAdvisory"
    assert any(r in role for r in ("Product Manager", "Lead Engineer", "Delivery Lead", "Engagement PM", "PM"))

    name_m, role_m, org_m = clean_attendee_entry("Mandy H (NEA)")
    assert name_m == "harper Hoffman"
    assert org_m == "Apex Global Enterprises (NEA)"
    assert role_m == "Product Manager"

    name_a, role_a, org_a = clean_attendee_entry("Amit Ayer")
    assert name_a == "Amit Ayer"
    assert org_a == "PartnerAdvisory"
    assert any(r in role_a for r in ("Lead Client Partner", "Principal Architect", "Lead Enterprise Architect"))


def test_scaffold_meeting_metadata_flat_attendees_resolution(tmp_path):
    p = tmp_path / "transcript.txt"
    p.write_text("Jen Pan: Hello.\nMandy H: Hi.", encoding="utf-8")
    meta = scaffold_meeting_metadata(
        input_path=str(p),
        meeting_name="Mandy LENS catch-Up",
        date_str="09-17-2026",
        attendees=["Jen Pan", "Amit Ayer", "Mandy H (NEA)"],
    )
    assert "PartnerAdvisory" in meta["attendees"]
    assert "Apex Global Enterprises (NEA)" in meta["attendees"]
    partner_advisory_members = meta["attendees"]["PartnerAdvisory"]
    assert any("Jen Pan" in m for m in partner_advisory_members)
    assert any("Amit Ayer" in m for m in partner_advisory_members)
    nea_members = meta["attendees"]["Apex Global Enterprises (NEA)"]
    assert any("harper Hoffman" in m for m in nea_members)
    roster_names = [r["name"] for r in meta["roster"]]
    assert "Jen Pan" in roster_names
    assert "Amit Ayer" in roster_names
    assert "harper Hoffman" in roster_names

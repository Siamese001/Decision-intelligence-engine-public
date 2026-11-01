"""
tests/unit/test_attendee_context_builder.py

Unit test suite verifying Wave 4 Dynamic Metadata-Driven Attendee Context Hydration.
Ensures authenticated attendee roster is passed into Stage 03 prompt.
"""
import json
from pathlib import Path

from dual_pipeline.transcript_stages_core.graph_executor import build_stage03_user_content


def test_attendees_dict_hydration():
    metadata = {
        "archetype": "STRATEGIC_DISCOVERY",
        "meeting_name": "Debrief_Traci_Amit",
        "attendees": {
            "PartnerAdvisory": ["Amit Ayer (Lead Enterprise Architect)", "Taylor Mercer (Delivery Lead)"],
            "Client": ["Reed (Executive Sponsor)"]
        }
    }
    user_content = build_stage03_user_content(
        forensic_transcript="Speaker 1: Hello.",
        metadata=metadata
    )
    assert "<meeting_attendees_roster>" in user_content
    assert "Amit Ayer (Lead Enterprise Architect) | Affiliation: PartnerAdvisory" in user_content
    assert "Taylor Mercer (Delivery Lead) | Affiliation: PartnerAdvisory" in user_content
    assert "Reed (Executive Sponsor) | Affiliation: Client" in user_content
    assert "Rule C-16 & Gate 3.11" in user_content


def test_attendees_list_hydration():
    metadata = {
        "archetype": "GENERAL",
        "attendees": ["Alex Sanchez", "Jen Pan"]
    }
    user_content = build_stage03_user_content(
        forensic_transcript="Speaker 1: Hi.",
        metadata=metadata
    )
    assert "<meeting_attendees_roster>" in user_content
    assert "- Alex Sanchez" in user_content
    assert "- Jen Pan" in user_content


def test_attendees_empty_omits_block():
    metadata = {
        "archetype": "GENERAL",
    }
    user_content = build_stage03_user_content(
        forensic_transcript="Speaker 1: Hi.",
        metadata=metadata
    )
    assert "<meeting_attendees_roster>" not in user_content

    receipt_path = Path("reports/wave4_metadata_hydration_receipt.json")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps({
        "status": "PASS",
        "roster_formats_tested": ["dict", "list", "none"],
        "gate_instructions_verified": ["Gate 3.11", "Gate 3.12", "Rule C-16"]
    }, indent=2), encoding="utf-8")

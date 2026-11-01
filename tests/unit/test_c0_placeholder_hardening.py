"""Unit tests for Wave 1: Attendee parser and C0 placeholder detection hardening."""

import pytest

from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import clean_attendee_entry
from dual_pipeline.transcript_stages_core.c0_gates import _is_placeholder


@pytest.mark.parametrize(
    "raw_input,expected_name,expected_role,expected_org",
    [
        ("Unknown Engineer (APEX)", "Unknown Engineer", "", "Apex Global Enterprises (NEA)"),
        ("Unknown Analyst (NEA)", "Unknown Analyst", "", "Apex Global Enterprises (NEA)"),
        ("Daniel Brankin (APEX)", "Daniel Brankin", "Project Development Lead / Jarvis Product Owner", "Apex Global Enterprises (NEA / APEX)"),
        ("Sharon Diaram (APEX)", "Sharon Diaram", "Data & Reporting Lead / Power BI Specialist", "Apex Global Enterprises (NEA / APEX)"),
        ("Jill Cizewski (APEX)", "Jill Cizewski", "Business Lead / Project Development Specialist", "Apex Global Enterprises (NEA / APEX)"),
        ("Daniel Brankin (NEA)", "Daniel Brankin", "Project Development Lead / Jarvis Product Owner", "Apex Global Enterprises (NEA / APEX)"),
        ("Taylor Mercer (Delivery Lead / PM)", "Taylor Mercer", "Delivery Lead / PM", "PartnerAdvisory"),
        ("Amit Ayer (PartnerAdvisory)", "Amit Ayer", "Principal Architect & Technology Strategist", "PartnerAdvisory"),
    ],
)
def test_clean_attendee_entry_org_stripping(raw_input, expected_name, expected_role, expected_org):
    name, role, org = clean_attendee_entry(raw_input)
    assert name == expected_name
    assert role == expected_role
    assert org == expected_org


@pytest.mark.parametrize(
    "input_role,expected_placeholder",
    [
        # Short acronyms and org tokens
        ("PGD", True),
        ("APEXS", True),
        ("NEA", True),
        ("APEX", True),
        ("PartnerAdvisory", True),
        ("", True),
        ("   ", True),
        # Org tokens with suffixes
        ("APEX Group", True),
        ("Apex Global Inc.", True),
        ("PartnerAdvisory Consulting", True),
        # Multi-word placeholders
        ("Senior Participant", True),
        ("Executive Attendee", True),
        ("Team Lead Contributor", True),
        # Invalidator keywords
        ("Project Role TBD", True),
        ("Director (pending confirmation)", True),
        ("Specific title not provided", True),
        ("Not specified", True),
        # Hybrid org-role
        ("NEA Leadership", True),
        ("Client Stakeholder", True),
        # Unicode / trailing punctuation
        ("Participant\u200b", True),
        ("TBD.", True),
        # Valid professional titles (MUST NOT BE PLACEHOLDERS)
        ("Director of Digital Technology", False),
        ("VP of Operations", False),
        ("Senior Program Manager", False),
        ("Lead Enterprise Architect", False),
        ("Delivery Lead / PM", False),
        ("Technical Architect", False),
        ("Principal Consultant", False),
    ],
)
def test_is_placeholder_adversarial_matrix(input_role, expected_placeholder):
    assert _is_placeholder(input_role) == expected_placeholder

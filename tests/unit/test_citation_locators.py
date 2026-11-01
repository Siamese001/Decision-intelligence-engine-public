"""
tests/unit/test_citation_locators.py

Verifies citation resolution for dot-separated JSON paths, comma-separated structured citations,
section symbol (§) locators, and header/attribution commentary.
"""
from __future__ import annotations

from dual_pipeline.transcript_qa_core.citation_utils import citation_matches_text


def test_dot_path_citation_matching():
    text = '{"graph_mutations": {"entity_upserts": [], "relationship_signals": []}}'
    assert citation_matches_text("graph_mutations.entity_upserts", text)
    assert citation_matches_text("graph_mutations.relationship_signals", text)
    assert not citation_matches_text("graph_mutations.non_existent", text)


def test_section_symbol_citation_matching():
    doc = "# STATEMENT OF WORK\n\n### 2. DESCRIPTION OF WORK\n2.1. Project Overview / Objectives\nUnified employee experience."
    assert citation_matches_text("SOW §2.1, project overview/objectives — PartnerAdvisory/APEX document", doc)
    assert citation_matches_text("§2.1", doc)
    assert not citation_matches_text("§9.9, Nonexistent Section", doc)


def test_header_attribution_commentary_matching():
    recap = "# Subject: Brief | 09/29/2026\n**Date:** 09/29/2026\nAttendees:\nGoogle:\n- Alex\nPartnerAdvisory:\n- Amit"
    cit = "Reference header and Attribution Summary: 09/29/2026; attendees with Google and PartnerAdvisory roles supplied."
    assert citation_matches_text(cit, recap)


def test_comma_separated_structured_citation_matching():
    doc = "# Trends 10/06/2026\n## 2. Key Industry Pillars\nAmazon finalized 690 MW nuclear PPA from Calvert Cliffs in October 2026 (S4)."
    cit = "10/06/2026, Antigravity Agent, Key Industry Pillars S4: Amazon PPA agreement."
    assert citation_matches_text(cit, doc)


def test_document_source_line_citation_matching():
    doc = "Argentieri, Richard\nPresident NEA and VP Product Developmen\nNEE Analytics"
    assert citation_matches_text("Source line: President NEA and VP Product Developmen (speaker unavailable in source)", doc)
    assert citation_matches_text("Source line: NEE Analytics (speaker unavailable in source)", doc)
    assert not citation_matches_text("Source line: Nonexistent Line (speaker unavailable in source)", doc)


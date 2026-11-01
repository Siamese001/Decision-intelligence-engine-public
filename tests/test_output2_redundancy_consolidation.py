"""
tests/test_output2_redundancy_consolidation.py

Unit tests verifying Output 02 redundancy consolidation:
1. Unified Action & Governance Ledger replaces separate Next Steps and Decision Gates.
2. Immediate Delivery Scope is forbidden for INTERNAL_TECHNICAL_SYNC.
3. Prompt contracts in stage and governance directories are synchronized.
"""
from __future__ import annotations

from pathlib import Path

from dual_pipeline.prompts import (
    CANONICAL_PROMPT_REGISTRY,
    OUTPUT2_CONTRACT,
    MeetingArchetype,
    SectionRequirement,
)

ROOT = Path(__file__).resolve().parents[1]


def test_stage_02_prompt_unified_ledger():
    """Verify STAGE_02_RECAP prompt instructs unified Action & Governance Ledger."""
    prompt = CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_02_RECAP")
    assert "Action & Governance Ledger" in prompt
    assert "MANDATORY UNIFIED TABLE" in prompt
    assert "DO NOT generate a separate 'Governed Decision Gates' table" in prompt


def test_stage_02_prompt_archetype_scoping():
    """Verify STAGE_02_RECAP prompt defines archetype conditions for delivery scope."""
    prompt = CANONICAL_PROMPT_REGISTRY.get_prompt("STAGE_02_RECAP")
    assert "ARCHETYPE-CONDITIONAL SECTION" in prompt
    assert "INTERNAL_TECHNICAL_SYNC and ARCHITECTURE_DEEP_DIVE: STRICTLY FORBIDDEN" in prompt


def test_canonical_and_mirror_prompts_synchronized():
    """Verify RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7 in governance and config mirrors are identical."""
    p_gov = ROOT / "governance" / "decision_intelligence" / "RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md"
    p_cfg = ROOT / "config" / "prompts" / "synthesis" / "RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md"

    gov_text = p_gov.read_text(encoding="utf-8")
    cfg_text = p_cfg.read_text(encoding="utf-8")

    assert gov_text == cfg_text, "Canonical and config mirror prompts must be byte-identical."
    assert "Action & Governance Ledger" in gov_text
    assert "Bounded Execution Wedge" not in gov_text


def test_contract_section_topology_invariants():
    """Verify OUTPUT2_CONTRACT topology invariants across archetypes."""
    tech_topo = OUTPUT2_CONTRACT.get_section_topology(MeetingArchetype.ARCHITECTURE_DEEP_DIVE)
    assert tech_topo["Immediate Delivery Scope & Release Boundaries"] == SectionRequirement.FORBIDDEN
    assert tech_topo["Business Meaning and Value Case"] == SectionRequirement.FORBIDDEN
    assert tech_topo["Active Technical Spikes & Experimental Inquiries"] == SectionRequirement.FORBIDDEN
    assert tech_topo["Action & Governance Ledger"] == SectionRequirement.CONDITIONAL
    assert tech_topo["Architectural Trade-Off Matrix"] == SectionRequirement.CONDITIONAL
    assert tech_topo["Recommendations & Decisions Required"] == SectionRequirement.CONDITIONAL

    rel_topo = OUTPUT2_CONTRACT.get_section_topology(MeetingArchetype.TACTICAL_ALIGNMENT)
    assert rel_topo["Immediate Delivery Scope & Release Boundaries"] == SectionRequirement.MANDATORY

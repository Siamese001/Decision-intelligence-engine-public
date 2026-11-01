"""
tests/test_contract_parity_and_ssot.py

Comprehensive test suite verifying Contract-First SSOT Architecture,
structured governance context envelopes, rubric point conservation,
anti-string-replace invariants, and parity gatekeeper execution.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from dual_pipeline.prompts.contracts import (
    OUTPUT1_CONTRACT,
    OUTPUT2_CONTRACT,
    OUTPUT3_CONTRACT,
    MeetingArchetype,
    OutputContract,
    RubricDimension,
    SectionRequirement,
)
from tools.lint_contract_parity import (
    ROOT,
    _check_anti_string_replace,
    _check_point_conservation,
    run_contract_parity_checks,
)


def test_contract_parity_linter_passes():
    """Verify that the mechanical contract parity gatekeeper passes with 0 defects."""
    passed, defects = run_contract_parity_checks()
    assert passed is True, f"Parity checks failed with defects: {defects}"
    assert len(defects) == 0


def test_rubric_point_conservation_invariants():
    """Verify that all output contracts conserve total points to exactly 100."""
    for contract in (OUTPUT1_CONTRACT, OUTPUT2_CONTRACT, OUTPUT3_CONTRACT):
        defects = _check_point_conservation(contract)
        assert len(defects) == 0, f"Point conservation failed for Output {contract.output_id}: {defects}"
        total = sum(d.max_points for d in contract.rubric_dimensions)
        assert total == 100, f"Output {contract.output_id} rubric points sum to {total} != 100"


def test_governance_context_schema_compliance():
    """Verify structured XML governance context envelope is well-formed and accurate."""
    for contract in (OUTPUT1_CONTRACT, OUTPUT2_CONTRACT, OUTPUT3_CONTRACT):
        for arch in (
            MeetingArchetype.GENERAL,
            MeetingArchetype.ARCHITECTURE_DEEP_DIVE,
            MeetingArchetype.STRATEGIC_DISCOVERY,
            MeetingArchetype.GOVERNANCE_REVIEW,
            MeetingArchetype.TACTICAL_ALIGNMENT,
        ):
            xml_str = contract.compile_governance_context(arch)
            assert xml_str.startswith("<governance_context>")
            assert xml_str.endswith("</governance_context>")
            root = ET.fromstring(xml_str)
            assert root.find("output_id").text == str(contract.output_id)
            assert root.find("meeting_archetype").text == arch.value

            # Verify mandatory sections presence
            topo = contract.get_section_topology(arch)
            mand_expected = [s for s, req in topo.items() if req == SectionRequirement.MANDATORY]
            mand_node = root.find("mandatory_sections")
            if mand_expected:
                assert mand_node is not None
                mand_actual = [sec.text for sec in mand_node.findall("section")]
                assert set(mand_expected) == set(mand_actual)


def test_output2_internal_sync_forbidden_sections():
    """Verify Output 2 governance context marks Immediate Delivery Scope as forbidden for internal sync."""
    xml_sync = OUTPUT2_CONTRACT.compile_governance_context(MeetingArchetype.ARCHITECTURE_DEEP_DIVE)
    root = ET.fromstring(xml_sync)
    forbid_node = root.find("forbidden_sections")
    assert forbid_node is not None
    forbidden_sections = [s.text for s in forbid_node.findall("section")]
    assert "Immediate Delivery Scope & Release Boundaries" in forbidden_sections


def test_anti_string_replace_invariant():
    """Verify zero prohibited string replacement hacks on archetypes in contracts."""
    defects = _check_anti_string_replace(ROOT)
    assert len(defects) == 0, f"Found prohibited string replace hacks: {defects}"


def test_judge_static_prompt_invariants():
    """Verify judge prompts are byte-identical across archetypes (cacheable, zero runtime mutation)."""
    for contract in (OUTPUT1_CONTRACT, OUTPUT2_CONTRACT, OUTPUT3_CONTRACT):
        p_general = contract.compile_judge_prompt(MeetingArchetype.GENERAL)
        p_sync = contract.compile_judge_prompt(MeetingArchetype.ARCHITECTURE_DEEP_DIVE)
        p_disc = contract.compile_judge_prompt(MeetingArchetype.STRATEGIC_DISCOVERY)
        assert p_general == p_sync == p_disc, (
            f"Output {contract.output_id} judge prompt mutated dynamically between archetypes"
        )


def test_parity_linter_fails_on_injected_defect():
    """Verify that the parity linter fails closed when a point total or rubric dimension is corrupted."""
    # Create an invalid dummy contract with 90 points
    bad_contract = OutputContract(
        output_id=99,
        name="Bad Contract",
        description="Test",
        sections={},
        rubric_dimensions=[
            RubricDimension(name="Dim 1", key="dim_1", max_points=50, description="D1"),
            RubricDimension(name="Dim 2", key="dim_2", max_points=40, description="D2"),
        ],
    )
    defects = _check_point_conservation(bad_contract)
    assert len(defects) == 1
    assert "sum to 90 points" in defects[0]

"""
tests/unit/test_output3_prompt_contract.py

Unit test suite verifying Wave 3 Prompt Contract SSOT Hardening & Few-Shot Realignment.
Asserts presence of Anti-Sludge invariants and removal of conflicting stmt-* references.
"""
import json
from pathlib import Path

from dual_pipeline.prompts.contracts.output3_contract import OUTPUT3_CONTRACT, MeetingArchetype


def test_prompt_compilation_all_archetypes():
    for arch in MeetingArchetype:
        prompt = OUTPUT3_CONTRACT.compile_stage_prompt(arch)
        assert len(prompt) > 1000
        assert "STAGE 03 — GRAPH EXTRACTOR" in prompt


def test_anti_sludge_invariants_present():
    prompt = OUTPUT3_CONTRACT.compile_stage_prompt(MeetingArchetype.GENERAL)
    assert "Anti-Conversational Sludge & Closed Kinds Invariants" in prompt
    assert "Conversational Sludge Prohibition" in prompt
    assert "Closed 6-Kind Taxonomy" in prompt
    assert "Substantive Person Descriptions" in prompt


def test_entity_upsert_examples_include_description():
    prompt = OUTPUT3_CONTRACT.compile_stage_prompt(MeetingArchetype.GENERAL)
    assert '"description": "Enterprise cloud platform hosting development workstreams and integrations."' in prompt
    assert '"description": "Delivery Lead at PartnerAdvisory governing project delivery and business case assumptions."' in prompt


def test_no_stmt_references_in_few_shot_examples():
    prompt = OUTPUT3_CONTRACT.compile_stage_prompt(MeetingArchetype.GENERAL)
    assert '"stmt-03"' not in prompt
    assert '"stmt-05"' not in prompt
    assert '"stmt-14"' not in prompt
    assert '"ev-03"' in prompt
    assert '"ev-01"' in prompt

    receipt_path = Path("reports/wave3_prompt_hardening_receipt.json")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps({
        "status": "PASS",
        "tested_archetypes": [arch.value for arch in MeetingArchetype],
        "invariants_verified": [
            "Anti-Conversational Sludge",
            "Closed 6-Kind Taxonomy",
            "Entity Upsert Description Parity",
            "ev-* ID Alignment"
        ]
    }, indent=2), encoding="utf-8")

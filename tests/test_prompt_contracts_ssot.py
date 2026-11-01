"""
tests/test_prompt_contracts_ssot.py

Unit tests for Declarative Output Contracts, Archetype-Adaptive Routing,
and Prompt/Judge SSOT Architecture.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from dual_pipeline.prompts import (
    CANONICAL_PROMPT_REGISTRY,
    OUTPUT1_CONTRACT,
    OUTPUT2_CONTRACT,
    OUTPUT3_CONTRACT,
    MeetingArchetype,
)

ROOT = Path(__file__).resolve().parents[1]


def test_contracts_registered_and_points_sum_to_100():
    """Verify all 3 output contracts exist and their rubric dimensions sum to 100."""
    for contract in (OUTPUT1_CONTRACT, OUTPUT2_CONTRACT, OUTPUT3_CONTRACT):
        assert contract.output_id in (1, 2, 3)
        assert len(contract.sections) > 0
        total_points = sum(d.max_points for d in contract.rubric_dimensions)
        assert total_points == 100, f"Output {contract.output_id} points sum to {total_points}, expected 100"


def test_meeting_archetype_resolution():
    """Verify MeetingArchetype resolves standard strings and fuzzy fallbacks."""
    assert MeetingArchetype.from_string("INTERNAL_TECHNICAL_SYNC") == MeetingArchetype.ARCHITECTURE_DEEP_DIVE
    assert MeetingArchetype.from_string("internal-technical-sync") == MeetingArchetype.ARCHITECTURE_DEEP_DIVE
    assert MeetingArchetype.from_string("technical deep dive") == MeetingArchetype.ARCHITECTURE_DEEP_DIVE
    assert MeetingArchetype.from_string("STRATEGIC_DISCOVERY") == MeetingArchetype.STRATEGIC_DISCOVERY
    assert MeetingArchetype.from_string("client discovery") == MeetingArchetype.STRATEGIC_DISCOVERY
    assert MeetingArchetype.from_string("release-planning") == MeetingArchetype.TACTICAL_ALIGNMENT
    assert MeetingArchetype.from_string(None) == MeetingArchetype.GENERAL


def test_output2_archetype_adaptation():
    """Verify Output 02 stage prompt compiles with archetype overrides for technical sync."""
    tech_prompt = OUTPUT2_CONTRACT.compile_stage_prompt(MeetingArchetype.ARCHITECTURE_DEEP_DIVE)
    assert "ARCHETYPE OVERRIDE: ARCHITECTURE_DEEP_DIVE" in tech_prompt
    assert "DO NOT generate 'Immediate Delivery Scope & Release Boundaries'" in tech_prompt
    assert "Action & Governance Ledger" in tech_prompt

    general_prompt = OUTPUT2_CONTRACT.compile_stage_prompt(MeetingArchetype.GENERAL)
    assert "ARCHETYPE OVERRIDE: ARCHITECTURE_DEEP_DIVE" not in general_prompt


def test_output2_judge_prompt_static_and_cacheable():
    """Verify Output 02 judge prompt is static and cacheable without string replacement."""
    judge_prompt_sync = OUTPUT2_CONTRACT.compile_judge_prompt(MeetingArchetype.ARCHITECTURE_DEEP_DIVE)
    judge_prompt_disc = OUTPUT2_CONTRACT.compile_judge_prompt(MeetingArchetype.STRATEGIC_DISCOVERY)
    assert judge_prompt_sync == judge_prompt_disc
    assert "meeting_archetype" in judge_prompt_sync


def test_output_contract_governance_context():
    """Verify structured XML governance context contains complete archetype topology."""
    gov_ctx = OUTPUT2_CONTRACT.compile_governance_context(MeetingArchetype.ARCHITECTURE_DEEP_DIVE)
    assert "<meeting_archetype>ARCHITECTURE_DEEP_DIVE</meeting_archetype>" in gov_ctx
    assert "<mandatory_sections>" in gov_ctx
    assert "<forbidden_sections>" in gov_ctx
    assert "<section>Immediate Delivery Scope &amp; Release Boundaries</section>" in gov_ctx
    assert "<output_id>2</output_id>" in gov_ctx



def test_forbidden_sections_checked():
    """Verify section forbiddenness rules by archetype."""
    assert OUTPUT2_CONTRACT.is_section_forbidden(
        "Immediate Delivery Scope & Release Boundaries",
        MeetingArchetype.ARCHITECTURE_DEEP_DIVE,
    ) is True
    assert OUTPUT2_CONTRACT.is_section_forbidden(
        "Immediate Delivery Scope & Release Boundaries",
        MeetingArchetype.TACTICAL_ALIGNMENT,
    ) is False


def test_registry_integration():
    """Verify PromptRegistry exposes contracts and compile methods."""
    c2 = CANONICAL_PROMPT_REGISTRY.get_contract(2)
    assert c2.output_id == 2
    prompt = CANONICAL_PROMPT_REGISTRY.compile_stage_prompt(2, "internal technical sync")
    assert "ARCHETYPE OVERRIDE: ARCHITECTURE_DEEP_DIVE" in prompt


def test_linter_tool_execution():
    """Verify tools/lint_prompt_ssot_integrity.py executes cleanly with code 0."""
    res = subprocess.run(
        [sys.executable, str(ROOT / "tools/lint_prompt_ssot_integrity.py")],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Linter failed: {res.stdout}\n{res.stderr}"
    assert "[PASS]" in res.stdout


def test_prompt_compiler_and_partials():
    """Verify PromptCompiler instantiates and compiles stage prompts and rubrics."""
    from dual_pipeline.prompts.compiler import PromptCompiler
    from dual_pipeline.prompts.partials import (
        UNIVERSAL_SYSTEM_PREAMBLE,
    )
    compiler = PromptCompiler()
    assert len(compiler.contracts) == 3
    p1 = compiler.compile_stage_prompt(1)
    assert "Output 1" in p1 or "Forensic" in p1
    p2 = compiler.compile_stage_prompt(2)
    assert "Output 2" in p2 or "Executive" in p2
    p3 = compiler.compile_stage_prompt(3)
    assert "Output 3" in p3 or "Graph" in p3
    rubric2 = compiler.compile_judge_rubric_markdown(2, MeetingArchetype.GENERAL)
    assert "Evaluation Rubric" in rubric2
    assert UNIVERSAL_SYSTEM_PREAMBLE in rubric2


def test_canonical_prompt_config_ssot():
    """Verify CANONICAL_PROMPT_CONFIG provides centralized SSOT parameters."""
    from dual_pipeline.prompts import CANONICAL_PROMPT_CONFIG
    cfg = CANONICAL_PROMPT_CONFIG
    assert cfg.output1.max_tokens >= 40000
    assert cfg.output2.max_tokens >= 20000
    assert cfg.output3.max_tokens >= 20000
    assert cfg.output1.generic_speaker_ceiling_percent == 15.0
    assert cfg.output2.min_themes_per_speaker >= 2
    assert "person:" in cfg.output3.allowed_entity_prefixes
    assert "decision" in cfg.output3.allowed_statement_kinds

    # Test judge execution config resolution
    j_cfg = cfg.judge
    m, e = j_cfg.resolve_model_and_effort()
    assert m is not None
    assert e is not None

    m_adv, e_adv = j_cfg.resolve_model_and_effort(use_adversarial=True)
    assert m_adv is not None

    m_loc, e_loc = j_cfg.resolve_model_and_effort(use_local=True)
    assert "mlx" in m_loc or "Qwen" in m_loc
    assert e_loc == "medium"

    m_req, e_req = j_cfg.resolve_model_and_effort(requested_model="custom-model", requested_effort="low")
    assert m_req == "custom-model"
    assert e_req == "low"


def test_dynamic_parameter_registry_propagation_and_pattern_exports(monkeypatch):
    """Verify runtime ParameterRegistry overrides and NEE_PARAM_* env vars immediately update CANONICAL_PROMPT_CONFIG and domain accessors."""
    from dual_pipeline.parameters import (
        get_audio_max_words_per_utterance,
        get_copilot_speech_limits,
        get_grounding_delivery_max_words,
        get_output2_c21_speaker_tier_floors,
        get_output2_section_density_floors,
        get_output2_word_count_limits,
        reload_parameters,
        set_parameter,
    )
    from dual_pipeline.prompts import (
        CANONICAL_PROMPT_CONFIG,
        OUTPUT1_CONTRACT,
        PASSIVE_HEDGING_PATTERNS,
        ROLE_ANONYMIZATION_PATTERNS,
        WORD_SALAD_REGEX,
    )

    assert CANONICAL_PROMPT_CONFIG.output1.single_speaker_collapse_threshold == 0.85
    assert CANONICAL_PROMPT_CONFIG.output1.single_speaker_collapse_threshold == OUTPUT1_CONTRACT.single_speaker_collapse_threshold

    try:
        set_parameter("output2.min_word_count", 625, persist=False)
        assert CANONICAL_PROMPT_CONFIG.output2.min_word_count == 625

        monkeypatch.setenv("NEE_PARAM_GROUNDING_DELIVERY_MAX_WORDS", "450")
        assert get_grounding_delivery_max_words() == 450
    finally:
        monkeypatch.delenv("NEE_PARAM_GROUNDING_DELIVERY_MAX_WORDS", raising=False)
        reload_parameters()

    assert CANONICAL_PROMPT_CONFIG.output2.min_word_count == 500
    assert get_grounding_delivery_max_words() == 500
    assert get_audio_max_words_per_utterance() == 80
    assert get_copilot_speech_limits()["cli_default_max_words"] == 35
    assert get_output2_section_density_floors()["business_meaning_min_words"] == 40
    assert get_output2_c21_speaker_tier_floors()["primary"] == (150, 3)
    assert get_output2_word_count_limits(45, has_diagrams=False, rigor_tier="TIER_1_DEEP_ARCHITECTURE")[:2] == (360, 1800)

    assert WORD_SALAD_REGEX.search("We need a bounded execution wedge.") is not None
    assert ROLE_ANONYMIZATION_PATTERNS.search("The lead architect explained the flow.") is not None
    assert "did not claim" in PASSIVE_HEDGING_PATTERNS




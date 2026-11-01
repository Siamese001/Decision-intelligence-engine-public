"""End-to-end verification of centralized SSOT configuration propagation across domains."""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.audio_ingestion_core.utterance_assembler import AssemblyConfig
from dual_pipeline.audio_ingestion_core.whisperx_aligner import whisperx_segments_to_utterances
from dual_pipeline.cli.copilot_speech import synthesize_typed_insight_comment
from dual_pipeline.grounding.metrics import evaluate_text_metrics
from dual_pipeline.llm_judge_core.recap_checks import check_passive_hedging_defect
from dual_pipeline.parameters import (
    ParameterRegistry,
    get_audio_max_words_per_utterance,
    get_copilot_speech_limits,
    get_grounding_delivery_max_words,
    get_output1_word_density_bounds,
    get_output2_c21_speaker_tier_floors,
    get_output2_dag_word_floors,
    get_output2_passive_hedging_threshold,
    get_output2_section_density_floors,
    get_output2_word_count_limits,
)
from dual_pipeline.prompts.config import CANONICAL_PROMPT_CONFIG
from dual_pipeline.transcript_qa_core.appendix_standard import SpeakerTier, _get_c21_floors
from dual_pipeline.transcript_qa_core.gate2 import _get_word_count_limits


def test_parameter_registry_ssot_propagates_across_all_consumers(tmp_path: Path, monkeypatch) -> None:
    """Verify that overriding config/runtime/parameters.json propagates to all rewired consumers."""
    repo_root = Path(__file__).resolve().parents[2]
    base_params_path = repo_root / "config" / "runtime" / "parameters.json"
    custom_payload = json.loads(base_params_path.read_text(encoding="utf-8"))

    # Mutate centralized parameters across Output 1, Output 2, Judge, Copilot, Audio, and Grounding
    custom_payload["parameters"]["output1"]["min_word_count"] = 222
    custom_payload["parameters"]["output1"]["max_word_count"] = 88888
    custom_payload["parameters"]["output1"]["words_per_minute_floor_multiplier"] = 14
    custom_payload["parameters"]["output1"]["min_words_per_turn_bypass"] = 42
    custom_payload["parameters"]["output2"]["min_word_count"] = 600
    custom_payload["parameters"]["output2"]["max_word_count"] = 2900
    custom_payload["parameters"]["output2"]["diagram_bonus_words"] = 750
    custom_payload["parameters"]["output2"]["passive_hedging_threshold"] = 5
    custom_payload["parameters"]["output2"]["dag_narrative_min_words"] = 333
    custom_payload["parameters"]["output2"]["dag_perspectives_min_words"] = 277
    custom_payload["parameters"]["output2"]["section_density_floors"]["business_meaning_min_words"] = 55
    custom_payload["parameters"]["output2"]["c21_speaker_tier_floors"]["primary_min_words"] = 175
    custom_payload["parameters"]["copilot"]["speech"]["insight_max_words"] = 12
    custom_payload["parameters"]["copilot"]["speech"]["multi_line_cap_words"] = 48
    custom_payload["parameters"]["audio_ingestion"]["max_words_per_utterance"] = 64
    custom_payload["parameters"]["grounding"]["delivery_max_words"] = 420

    custom_file = tmp_path / "custom_parameters.json"
    custom_file.write_text(json.dumps(custom_payload, indent=2), encoding="utf-8")

    custom_reg = ParameterRegistry(config_path=custom_file)
    monkeypatch.setattr("dual_pipeline.parameters._GLOBAL_REGISTRY", custom_reg)

    # 1. Prompt config dynamic properties
    assert CANONICAL_PROMPT_CONFIG.output1.min_word_count == 222
    assert CANONICAL_PROMPT_CONFIG.output1.max_word_count == 88888
    assert CANONICAL_PROMPT_CONFIG.output2.min_word_count == 600
    assert CANONICAL_PROMPT_CONFIG.output2.max_word_count == 2900

    # 2. Gate 1 & Gate 2 resolvers
    assert get_output1_word_density_bounds() == (14, 42)
    min_w, max_w, _label = _get_word_count_limits(45, has_diagrams=True, rigor_tier="TIER_1_DEEP_ARCHITECTURE")
    assert min_w == 360
    assert max_w == 1800 + 750

    # 3. Section density, DAG floors, and C-21 speaker tier floors
    assert get_output2_section_density_floors()["business_meaning_min_words"] == 55
    assert get_output2_dag_word_floors() == (333, 277)
    assert get_output2_c21_speaker_tier_floors()["primary"][0] == 175
    assert _get_c21_floors()[SpeakerTier.PRIMARY][0] == 175

    # 4. Judge passive hedging threshold
    assert get_output2_passive_hedging_threshold() == 5
    hedging_text = (
        "Discussions were held regarding the system. "
        "Concerns were raised about latency. "
        "It was noted that testing is needed. "
        "Options were explored by the team."
    )
    # 4 matches <= overridden threshold of 5 -> None
    assert check_passive_hedging_defect(hedging_text) is None

    # 5. Copilot speech limits
    speech_limits = get_copilot_speech_limits()
    assert speech_limits["insight_max_words"] == 12
    assert speech_limits["multi_line_cap_words"] == 48
    long_assertion = [{
        "assertion_id": "a-1",
        "claim_text": " ".join(f"token{i}" for i in range(25)),
        "predicate": "BLOCKS",
    }]
    res_speech = synthesize_typed_insight_comment(long_assertion)
    assert res_speech.body == "" or len(res_speech.body.split()) <= 12

    # 6. Audio utterance assembly & WhisperX alignment
    assert get_audio_max_words_per_utterance() == 64
    cfg = AssemblyConfig()
    assert cfg.max_words_per_utterance == 64
    segs = [
        {
            "speaker": "SPEAKER_00",
            "start": 0.0,
            "end": 1.0,
            "text": " ".join(["alpha"] * 40),
            "words": [{"word": "alpha", "start": 0.0, "end": 0.02} for _ in range(40)],
        },
        {
            "speaker": "SPEAKER_00",
            "start": 1.2,
            "end": 2.0,
            "text": " ".join(["beta"] * 30),
            "words": [{"word": "beta", "start": 1.2, "end": 1.22} for _ in range(30)],
        },
    ]
    # 40 + 30 = 70 > 64 -> should NOT merge into one utterance
    utts = whisperx_segments_to_utterances(segs)
    assert len(utts) == 2

    # 7. Grounding delivery metrics
    assert get_grounding_delivery_max_words() == 420
    res_pass = evaluate_text_metrics(" ".join(["grounded"] * 420))
    assert res_pass.passed_count is True
    res_fail = evaluate_text_metrics(" ".join(["grounded"] * 421))
    assert res_fail.passed_count is False

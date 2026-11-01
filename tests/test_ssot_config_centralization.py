"""Unit and adversarial tests for SSOT Configuration Centralization (C_CFG_1-6)."""
from __future__ import annotations

import ast
from pathlib import Path

from dual_pipeline.agent_sdk.config import (
    DEFAULT_COMPACTION_TOKEN_THRESHOLD,
    DEFAULT_MAX_MODEL_CALLS,
    DEFAULT_MAX_SUBAGENT_DEPTH,
    DEFAULT_MAX_TOOL_CALLS,
)
from dual_pipeline.asr_decode_config import DEFAULT_ASR_DECODE_CONFIG
from dual_pipeline.canonical_pipeline.evaluator import CANONICAL_MUTATION_BUDGET
from dual_pipeline.config.pipeline_settings import (
    ENABLE_ATTACHMENT_MUTATION_AUTOMATION,
    MAX_JUDGE_RETRIES_DEFAULT,
    MAX_NETWORK_RETRIES,
    MAX_PAYLOAD_RETRIES,
    NETWORK_BASE_DELAY_SECONDS,
    NETWORK_MAX_DELAY_SECONDS,
)
from dual_pipeline.config_hasher import compute_active_config_snapshot, compute_config_canonical_sha256
from dual_pipeline.parameters import (
    get_agent_sdk_defaults,
    get_asr_decode_parameters,
    get_audio_streaming_parameters,
    get_authorization_parameters,
    get_canonical_mutation_budget,
    get_decision_service_parameters,
    get_default_assumption_ttl_days,
    get_document_security_parameters,
    get_graph_consolidation_parameters,
    get_graph_retrieval_parameters,
    get_graph_signal_parameters,
    get_index_core_parameters,
    get_local_mlx_model,
    get_max_subagent_context_tokens,
    get_pipeline_settings_parameters,
    get_starvation_turn_ratio_threshold,
)
from tools.lint_hardcoded_parameters import EXEMPT_FILES, ParameterLinterVisitor


def test_pipeline_settings_and_mutation_budget_ssot_parity() -> None:
    """Verify pipeline_settings and evaluator mutation budget match ParameterRegistry SSOT."""
    settings = get_pipeline_settings_parameters()
    assert MAX_PAYLOAD_RETRIES == settings["max_payload_retries"] == 1
    assert MAX_JUDGE_RETRIES_DEFAULT == settings["max_judge_retries_default"] == 1
    assert MAX_NETWORK_RETRIES == settings["max_network_retries"] == 5
    assert NETWORK_BASE_DELAY_SECONDS == settings["network_base_delay_seconds"] == 0.5
    assert NETWORK_MAX_DELAY_SECONDS == settings["network_max_delay_seconds"] == 30.0
    assert ENABLE_ATTACHMENT_MUTATION_AUTOMATION == settings["enable_attachment_mutation_automation"] is False
    assert CANONICAL_MUTATION_BUDGET == get_canonical_mutation_budget() == 30


def test_agent_sdk_and_audio_streaming_ssot_parity() -> None:
    """Verify Agent SDK defaults, audio streaming parameters, and ASR decode config match SSOT."""
    sdk = get_agent_sdk_defaults()
    assert DEFAULT_MAX_MODEL_CALLS == sdk["default_max_model_calls"] == 50
    assert DEFAULT_MAX_TOOL_CALLS == sdk["default_max_tool_calls"] == 100
    assert DEFAULT_COMPACTION_TOKEN_THRESHOLD == sdk["default_compaction_token_threshold"] == 80000
    assert DEFAULT_MAX_SUBAGENT_DEPTH == sdk["default_max_subagent_depth"] == 2

    audio = get_audio_streaming_parameters()
    assert audio["sample_rate_hz"] == 16000
    assert audio["ring_buffer_seconds"] == 60
    assert audio["voiceprint_similarity_threshold"] == 0.82

    asr = get_asr_decode_parameters()
    assert DEFAULT_ASR_DECODE_CONFIG.temperature == asr["temperature"] == 0.0
    assert DEFAULT_ASR_DECODE_CONFIG.no_speech_threshold == asr["no_speech_threshold"] == 0.60
    assert DEFAULT_ASR_DECODE_CONFIG.compression_ratio_threshold == asr["compression_ratio_threshold"] == 2.40
    assert DEFAULT_ASR_DECODE_CONFIG.language == asr["language"] == "en"


def test_local_mlx_model_profiles_and_fallback() -> None:
    """Verify all 6 local MLX model profiles resolve from SSOT and unknown profiles fall back cleanly."""
    assert get_local_mlx_model("instruct_8b") == "mlx-community/Meta-Llama-3.1-8B-Instruct-4bit"
    assert get_local_mlx_model("instruct_14b") == "mlx-community/Qwen2.5-14B-Instruct-4bit"
    assert get_local_mlx_model("instruct_32b") == "mlx-community/Qwen2.5-32B-Instruct-4bit"
    assert get_local_mlx_model("reasoning_14b") == "mlx-community/DeepSeek-R1-Distill-Qwen-14B-4bit"
    assert get_local_mlx_model("vision_7b") == "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"
    assert get_local_mlx_model("whisper_draft") == "mlx-community/whisper_tiny"
    assert get_local_mlx_model("unknown_profile") == "mlx-community/Qwen2.5-14B-Instruct-4bit"


def test_config_hasher_includes_all_canonical_digests() -> None:
    """Verify compute_active_config_snapshot includes parameters, release policy, model registry, and ASR decode digests."""
    snapshot = compute_active_config_snapshot()
    for key in ("parameters_digest", "release_policy_digest", "model_registry_digest", "asr_decode_digest"):
        assert key in snapshot and isinstance(snapshot[key], str) and len(snapshot[key]) >= 16
    digest = compute_config_canonical_sha256(snapshot)
    assert len(digest) == 64


def test_adversarial_ast_linter_flags_hardcoded_mlx_model_and_confined_constants() -> None:
    """Adversarial test: verify AST linter catches hardcoded mlx-community strings and duplicate confined constants."""
    bad_source = (
        'CANONICAL_MUTATION_BUDGET = 99\n'
        'MAX_PAYLOAD_RETRIES = 3\n'
        'LOCAL_MODEL = "mlx-community/Qwen2.5-14B-Instruct-4bit"\n'
    )
    tree = ast.parse(bad_source)
    visitor = ParameterLinterVisitor("dual_pipeline/some_new_module.py", bad_source.splitlines())
    visitor.visit(tree)
    rules = [v.rule for v in visitor.violations]
    assert rules.count("DUPLICATE_SSOT_CONSTANT") == 2
    assert rules.count("HARDCODED_MODEL_STRING") == 1


def test_sweep2_subsystem_parameters_and_exemption_elimination() -> None:
    """Verify Sweep 2 centralized subsystem parameters and removal of production MLX exemptions."""
    cons = get_graph_consolidation_parameters()
    assert cons["default_node_similarity_threshold"] == 0.65
    assert cons["default_literal_similarity_threshold"] == 0.85
    assert cons["default_evidence_similarity_threshold"] == 0.90
    assert cons["min_confidence_margin"] == 0.05
    assert cons["max_confidence_margin"] == 0.30
    assert cons["min_modularity"] == 0.60

    g_ret = get_graph_retrieval_parameters()
    assert g_ret["default_confidence_score"] == 0.80
    assert g_ret["temporal_decay_lambda"] == 0.34657
    assert g_ret["max_temporal_boost"] == 0.10
    assert g_ret["max_confidence_boost"] == 0.08

    g_sig = get_graph_signal_parameters()
    assert g_sig["penalty_mutex_conflict"] == 5.0
    assert g_sig["penalty_orphan_edge"] == 50.0
    assert g_sig["min_composite_score"] == 80.0

    idx = get_index_core_parameters()
    assert idx["max_colbert_tokens"] == 32
    assert idx["embedding_batch_size"] == 16
    assert idx["default_colbert_tile_size"] == 64

    doc = get_document_security_parameters()
    assert doc["max_xml_depth"] == 32
    assert doc["max_xml_elements"] == 10000
    assert doc["max_zip_ratio"] == 20.0
    assert doc["max_json_depth"] == 32

    dec = get_decision_service_parameters()
    assert dec["default_fast_path_ttl_sec"] == 30.0
    assert dec["max_context_records"] == 12
    assert dec["max_request_bytes"] == 1048576

    auth = get_authorization_parameters()
    assert auth["max_token_lifetime_seconds"] == 3600
    assert auth["clock_skew_seconds"] == 60

    assert get_max_subagent_context_tokens() == 4000
    assert get_starvation_turn_ratio_threshold() == 0.05
    assert get_default_assumption_ttl_days() == 14

    for removed_exempt in (
        "dual_pipeline/runtime/model_runtime.py",
        "dual_pipeline/live_meeting_recorder_core/mlx_worker.py",
        "pipeline_a_intake/local_stt.py",
    ):
        assert removed_exempt not in EXEMPT_FILES


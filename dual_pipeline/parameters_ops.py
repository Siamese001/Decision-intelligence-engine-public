"""
dual_pipeline/parameters_ops.py

Operational helpers, persistence routines, governed bounds, and domain SSOT
accessors for ParameterRegistry.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ReleaseGatesConfig:
    """SSOT Release Gates configuration bound to config/runtime/parameters.json."""

    @property
    def output1_min_score(self) -> int:
        from .parameters import get_int_parameter
        return get_int_parameter("release_thresholds.output1_production_pass_threshold", 90)

    @property
    def output2_min_score(self) -> int:
        from .parameters import get_int_parameter
        return get_int_parameter("release_thresholds.output2_production_pass_threshold", 90)

    @property
    def output3_min_score(self) -> int:
        from .parameters import get_int_parameter
        return get_int_parameter("release_thresholds.output3_production_pass_threshold", 90)

    @property
    def advisory_score_floor(self) -> int:
        from .parameters import get_int_parameter
        return get_int_parameter("release_thresholds.advisory_score_floor", 80)

HITL_PARAMETER_BOUNDS: Dict[str, Tuple[float, float, float]] = {
    "hitl.bayesian_prior_strength": (1.0, 10.0, 4.0),
    "hitl.human_feedback_weight": (0.5, 2.0, 1.0),
    "hitl.autonomous_feedback_weight": (0.01, 0.20, 0.05),
    "hitl.ambiguity_margin_threshold": (0.05, 0.30, 0.20),
    "hitl.divergence_margin_threshold": (0.10, 0.40, 0.20),
    "hitl.min_autonomous_confidence_floor": (0.50, 0.90, 0.65),
    "hitl.entropy_ambiguity_threshold": (0.50, 0.95, 0.75),
}

COPILOT_PARAMETER_BOUNDS: Dict[str, Tuple[float, float, float]] = {
    "copilot.context.focus_seconds": (5.0, 60.0, 15.0),
    "copilot.context.decay_tau_seconds": (30.0, 1800.0, 300.0),
    "copilot.context.salient_entities_top_k": (3.0, 20.0, 8.0),
    "copilot.context.flow_items_max": (3.0, 25.0, 10.0),
    "copilot.context.focus_weight": (0.3, 0.9, 0.6),
    "copilot.neighborhood_max_hops": (1.0, 3.0, 2.0),
    "copilot.rigor.budget_ms": (50.0, 1000.0, 250.0),
    "copilot.rigor.min_provenance_score": (0.0, 1.0, 0.15),
    "copilot.rerank.lexical": (0.0, 1.0, 0.3),
    "copilot.rerank.structural": (0.0, 1.0, 0.3),
    "copilot.rerank.dense": (0.0, 1.0, 0.4),
}

AUDIO_VOICE_PARAMETER_BOUNDS: Dict[str, Tuple[float, float, float]] = {
    "audio_ingestion.voiceprint_similarity_threshold": (0.65, 0.95, 0.82),
    "audio_ingestion.roster_priority_boost": (0.0, 0.20, 0.08),
    "audio_ingestion.silence_energy_threshold": (0.001, 0.05, 0.005),
    "audio_ingestion.bleed_margin": (0.05, 0.50, 0.25),
    "speaker_attribution.candidate_scorer_voiceprint_weight": (0.20, 0.70, 0.50),
    "speaker_attribution.candidate_scorer_role_weight": (0.05, 0.50, 0.25),
    "speaker_attribution.markov_transition_boost_cap": (0.01, 0.20, 0.08),
    "speaker_attribution.co_attendance_boost_cap": (0.01, 0.15, 0.06),
    "speaker_attribution.cadence_wpm_boost_cap": (0.01, 0.15, 0.06),
}
COPILOT_PARAMETER_BOUNDS.update(AUDIO_VOICE_PARAMETER_BOUNDS)


def validate_audio_voice_parameter_bounds(param_name: str, value: float) -> bool:
    """Validates whether a proposed audio voice recognition parameter value is within governed bounds."""
    bounds = AUDIO_VOICE_PARAMETER_BOUNDS.get(param_name)
    if bounds is None:
        return False
    min_val, max_val, _ = bounds
    try:
        val_f = float(value)
    except (ValueError, TypeError):
        return False
    return min_val <= val_f <= max_val


def validate_hitl_parameter_bounds(param_name: str, value: float) -> bool:
    """Validates whether a proposed HITL parameter value is within governed bounds."""
    bounds = HITL_PARAMETER_BOUNDS.get(param_name)
    if bounds is None:
        return False
    min_val, max_val, _ = bounds
    try:
        val_f = float(value)
    except (ValueError, TypeError):
        return False
    return min_val <= val_f <= max_val


def validate_copilot_parameter_bounds(param_name: str, value: float) -> bool:
    """Validates whether a proposed copilot context parameter value is within governed bounds."""
    bounds = COPILOT_PARAMETER_BOUNDS.get(param_name)
    if bounds is None:
        return False
    min_val, max_val, _ = bounds
    try:
        val_f = float(value)
    except (ValueError, TypeError):
        return False
    return min_val <= val_f <= max_val


def _check_prop_spec(domain: str, k: str, val: Any, spec: Dict[str, Any]) -> List[str]:
    ptype = spec.get("type")
    type_map = {"integer": int, "number": (int, float), "boolean": bool, "string": str}
    if ptype in type_map:
        if not isinstance(val, type_map[ptype]) or (ptype in ("integer", "number") and isinstance(val, bool)):
            return [f"{domain}.{k}: expected {ptype}, got {type(val).__name__}"]
    if not isinstance(val, (int, float)) or isinstance(val, bool):
        return []
    errs: List[str] = []
    if "minimum" in spec and float(val) < float(spec["minimum"]):
        errs.append(f"{domain}.{k}: {val} < minimum {spec['minimum']}")
    if "maximum" in spec and float(val) > float(spec["maximum"]):
        errs.append(f"{domain}.{k}: {val} > maximum {spec['maximum']}")
    return errs


def _check_domain_props(dom: str, d_val: Any, d_spec: Dict[str, Any]) -> List[str]:
    if not isinstance(d_val, dict):
        return []
    errs: List[str] = []
    for k, spec in d_spec.get("properties", {}).items():
        if k in d_val and isinstance(spec, dict):
            errs.extend(_check_prop_spec(dom, k, d_val[k], spec))
    return errs


def validate_parameters_schema(
    data: Optional[Dict[str, Any]] = None,
    schema_path: Optional[Path] = None,
) -> Tuple[bool, List[str]]:
    """Validates parameters dictionary against config/runtime/parameters.schema.json."""
    root = Path(__file__).resolve().parent.parent
    spath = schema_path or (root / "config" / "runtime" / "parameters.schema.json")
    if not spath.exists():
        return False, [f"Missing schema file: {spath}"]
    schema = json.loads(spath.read_text(encoding="utf-8"))
    if data is None:
        from .parameters import _GLOBAL_REGISTRY
        params = _GLOBAL_REGISTRY._load_json()
    else:
        params = data.get("parameters", data) if isinstance(data, dict) else {}
    p_schema = schema.get("properties", {}).get("parameters", {})
    errors = [f"Missing required parameter domain: {r}" for r in p_schema.get("required", []) if r not in params]
    for dom, d_spec in p_schema.get("properties", {}).items():
        errors.extend(_check_domain_props(dom, params.get(dom), d_spec))
    return len(errors) == 0, errors


def save_parameter_file(target_path: Path, data: Dict[str, Any]) -> None:
    """Atomically persists parameter dictionary wrapped in schema envelope."""
    file_dict = {
        "schema_version": "1.0.0",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "parameters": data,
    }
    tmp_file = target_path.with_suffix(".tmp")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(file_dict, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_file, target_path)


def execute_unset(
    registry: Any,
    dotted_key: str,
    persist: bool = True,
    config_path: Optional[Path] = None,
) -> bool:
    """Unsets/deletes a parameter and optionally persists atomically to JSON."""
    target_path = config_path or registry.config_path
    with registry._lock:
        if config_path is not None and config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
                data = dict(raw.get("parameters", raw))
        else:
            data = dict(registry._load_json())

        parts = dotted_key.split(".")
        curr = data
        for p in parts[:-1]:
            if p not in curr or not isinstance(curr[p], dict):
                return False
            curr = curr[p]

        last_key = parts[-1]
        if last_key not in curr:
            return False

        del curr[last_key]
        registry._cached_data = data

        if persist:
            save_parameter_file(target_path, data)
        return True


def execute_compare_and_swap(
    registry: Any,
    dotted_key: str,
    expected_current_value: Any,
    new_value: Any,
    persist: bool = True,
    config_path: Optional[Path] = None,
) -> bool:
    """Atomically sets new_value only if current value matches expected_current_value."""
    with registry._lock:
        curr_val = registry.get(dotted_key)
        matches = curr_val == expected_current_value
        if not matches:
            try:
                if abs(float(curr_val) - float(expected_current_value)) < 1e-6:
                    matches = True
            except (ValueError, TypeError):
                pass
        if not matches:
            raise ValueError(
                f"CAS_CONFLICT: Parameter '{dotted_key}' current value {curr_val} does not match expected {expected_current_value}"
            )
        registry.set(dotted_key, new_value, persist=persist, config_path=config_path)
        return True


def get_output1_word_density_bounds() -> Tuple[int, int]:
    """Return (words_per_minute_floor_multiplier, min_words_per_turn_bypass) for Output 01."""
    from .parameters import get_int_parameter
    return (
        get_int_parameter("output1.words_per_minute_floor_multiplier", 10),
        get_int_parameter("output1.min_words_per_turn_bypass", 35),
    )


def compute_output2_duration_word_bounds(
    duration_minutes: int,
    is_tier3: bool = False,
    has_diagrams: bool = False,
) -> Tuple[int, int, int, int]:
    """Compute unified continuous duration-scaled word bounds from SSOT parameters."""
    from .parameters import get_int_parameter
    d = max(1, int(duration_minutes))
    if is_tier3:
        wpm = get_int_parameter("output2.word_limits.tier3_words_per_min", 4)
        min_flr = get_int_parameter("output2.word_limits.tier3_min_floor", 100)
        max_flr = get_int_parameter("output2.word_limits.tier3_max_floor", 300)
        ceil_wpm = get_int_parameter("output2.word_limits.tier3_ceiling_words_per_min", 30)
        min_ceil = get_int_parameter("output2.word_limits.tier3_min_ceiling", 1500)
        max_ceil = get_int_parameter("output2.word_limits.tier3_max_ceiling", 3000)
        max_e = (
            get_int_parameter("output2.word_limits.tier3_max_exhibit_with_diagrams", 400)
            if has_diagrams
            else get_int_parameter("output2.word_limits.tier3_max_exhibit", 200)
        )
        max_t = get_int_parameter("output2.word_limits.tier3_max_total", 4000)
        return max(min_flr, min(max_flr, int(d * wpm))), max(min_ceil, min(max_ceil, int(d * ceil_wpm))), max_e, max_t

    wpm = get_int_parameter("output2.word_limits.standard_words_per_min", 8)
    min_flr = get_int_parameter("output2.word_limits.standard_min_floor", 100)
    max_flr = get_int_parameter("output2.word_limits.standard_max_floor", 600)
    ceil_wpm = get_int_parameter("output2.word_limits.standard_ceiling_words_per_min", 40)
    min_ceil = get_int_parameter("output2.word_limits.standard_min_ceiling", 1800)
    max_ceil = get_int_parameter("output2.word_limits.standard_max_ceiling", 5000)
    bonus = get_int_parameter("output2.diagram_bonus_words", 600) if has_diagrams else 0
    max_e = (
        get_int_parameter("output2.word_limits.standard_max_exhibit_with_diagrams", 1200)
        if has_diagrams
        else get_int_parameter("output2.word_limits.standard_max_exhibit", 600)
    )
    max_t = get_int_parameter("output2.word_limits.standard_max_total", 8000)
    return max(min_flr, min(max_flr, int(d * wpm))), max(min_ceil, min(max_ceil, int(d * ceil_wpm))) + bonus, max_e, max_t


def get_output2_tier3_word_limits(meeting_dur: Optional[int]) -> Tuple[int, Optional[int], str]:
    """Resolve SSOT word limits for Tier 3 lightweight status syncs."""
    d = meeting_dur or 30
    min_w, max_w, _, _ = compute_output2_duration_word_bounds(d, is_tier3=True, has_diagrams=False)
    return min_w, max_w, f" for a {d}m lightweight status sync"


def get_output2_tier1_word_limits(meeting_dur: Optional[int], has_diagrams: bool = False) -> Tuple[int, Optional[int], str]:
    """Resolve SSOT word limits for Tier 1 deep architecture sessions."""
    d = meeting_dur or 30
    min_w, max_w, _, _ = compute_output2_duration_word_bounds(d, is_tier3=False, has_diagrams=has_diagrams)
    label = f" for a {d}m deep architecture session" if meeting_dur else ""
    return min_w, max_w, label


def get_output2_default_word_limits(meeting_dur: Optional[int]) -> Tuple[int, Optional[int], str]:
    """Resolve SSOT word limits for standard meetings by duration."""
    d = meeting_dur or 30
    min_w, max_w, _, _ = compute_output2_duration_word_bounds(d, is_tier3=False, has_diagrams=False)
    label = f" for a {d}m meeting" if meeting_dur else ""
    return min_w, max_w, label


def get_output2_word_count_limits(
    meeting_dur: Optional[int],
    has_diagrams: bool = False,
    rigor_tier: Optional[str] = None,
) -> Tuple[int, Optional[int], str]:
    """Unified SSOT resolver for Output 02 word count limits across generators, gates, and linters."""
    tier_str = str(rigor_tier).upper() if rigor_tier else ""
    if tier_str == "TIER_3_LIGHTWEIGHT_STATUS":
        return get_output2_tier3_word_limits(meeting_dur)
    if tier_str == "TIER_1_DEEP_ARCHITECTURE" or has_diagrams:
        return get_output2_tier1_word_limits(meeting_dur, has_diagrams)
    return get_output2_default_word_limits(meeting_dur)


def get_output2_section_density_floors() -> Dict[str, int]:
    """Return SSOT word floors for deep Output 02 sections."""
    from .parameters import get_int_parameter
    return {
        "business_meaning_min_words": get_int_parameter("output2.section_density_floors.business_meaning_min_words", 40),
        "immediate_scope_min_words": get_int_parameter("output2.section_density_floors.immediate_scope_min_words", 40),
        "recommendations_min_words": get_int_parameter("output2.section_density_floors.recommendations_min_words", 30),
        "tier3_section_floor": get_int_parameter("output2.section_density_floors.tier3_section_floor", 20),
        "standard_30m_section_floor": get_int_parameter("output2.section_density_floors.standard_30m_section_floor", 60),
    }


def get_output2_c21_speaker_tier_floors() -> Dict[str, Tuple[int, int]]:
    """Return SSOT C-21 (min_words, min_themes) floors keyed by speaker tier value."""
    from .parameters import get_int_parameter
    return {
        "primary": (
            get_int_parameter("output2.c21_speaker_tier_floors.primary_min_words", 150),
            get_int_parameter("output2.c21_speaker_tier_floors.primary_min_themes", 3),
        ),
        "co_lead": (
            get_int_parameter("output2.c21_speaker_tier_floors.co_lead_min_words", 80),
            get_int_parameter("output2.c21_speaker_tier_floors.co_lead_min_themes", 2),
        ),
        "participant": (
            get_int_parameter("output2.c21_speaker_tier_floors.participant_min_words", 40),
            get_int_parameter("output2.c21_speaker_tier_floors.participant_min_themes", 2),
        ),
        "contributor": (
            get_int_parameter("output2.c21_speaker_tier_floors.contributor_min_words", 15),
            get_int_parameter("output2.c21_speaker_tier_floors.contributor_min_themes", 1),
        ),
    }


def get_output2_passive_hedging_threshold() -> int:
    """Return SSOT threshold for passive hedging instances in Output 02."""
    from .parameters import get_int_parameter
    return get_int_parameter("output2.passive_hedging_threshold", 5)


def get_output2_dag_word_floors() -> Tuple[int, int]:
    """Return SSOT (narrative_min_words, perspectives_min_words) for modular recap DAG."""
    from .parameters import get_int_parameter
    return (
        get_int_parameter("output2.dag_narrative_min_words", 800),
        get_int_parameter("output2.dag_perspectives_min_words", 600),
    )


def get_copilot_speech_limits() -> Dict[str, int]:
    """Return SSOT word limits for live copilot spoken commentary."""
    from .parameters import get_int_parameter
    return {
        "cli_default_max_words": get_int_parameter("copilot.speech.cli_default_max_words", 35),
        "insight_max_words": get_int_parameter("copilot.speech.insight_max_words", 40),
        "cumulative_max_words": get_int_parameter("copilot.speech.cumulative_max_words", 90),
        "multi_line_cap_words": get_int_parameter("copilot.speech.multi_line_cap_words", 85),
        "speaker_summary_max_words": get_int_parameter("copilot.speech.speaker_summary_max_words", 130),
        "supporting_details_max_words": get_int_parameter("copilot.speech.supporting_details_max_words", 220),
        "total_content_max_words": get_int_parameter("copilot.speech.total_content_max_words", 350),
        "max_sentence_words": get_int_parameter("copilot.speech.max_sentence_words", 28),
    }


def get_engineering_output_limits() -> Dict[str, int]:
    """Return SSOT word and character limits for engineering completion turns."""
    from .parameters import get_int_parameter
    return {
        "max_words": get_int_parameter("engineering_output.max_words", 300),
        "target_words": get_int_parameter("engineering_output.target_words", 120),
        "target_min_words": get_int_parameter("engineering_output.target_min_words", 100),
        "target_max_words": get_int_parameter("engineering_output.target_max_words", 140),
        "max_chars": get_int_parameter("engineering_output.max_chars", 3500),
        "progress_max_words": get_int_parameter("engineering_output.progress_max_words", 20),
        "pr_queue_max_words": get_int_parameter("engineering_output.pr_queue_max_words", 1000),
        "ssot_pointer_max_words": get_int_parameter("engineering_output.ssot_pointer_max_words", 40),
    }


def get_planning_word_limits() -> Dict[str, int]:
    """Return SSOT word budget parameters for wave-based implementation plans."""
    from .parameters import get_int_parameter
    return {
        "max_plan_words": get_int_parameter("planning.max_plan_words", 1000),
        "max_scaled_plan_words": get_int_parameter("planning.max_scaled_plan_words", 2500),
        "base_plan_words": get_int_parameter("planning.base_plan_words", 400),
        "words_per_wave": get_int_parameter("planning.words_per_wave", 250),
        "words_per_deliverable": get_int_parameter("planning.words_per_deliverable", 25),
    }


def compute_scaled_plan_word_limit(num_waves: int, num_deliverables: int) -> int:
    """Compute maximum allowed plan words scaled by waves and deliverables."""
    limits = get_planning_word_limits()
    base = (
        limits["base_plan_words"]
        + limits["words_per_wave"] * max(0, num_waves)
        + limits["words_per_deliverable"] * max(0, num_deliverables)
    )
    return min(limits["max_scaled_plan_words"], max(limits["max_plan_words"], base))


def get_audio_max_words_per_utterance() -> int:
    """Return SSOT max words per assembled utterance in audio ingestion."""
    from .parameters import get_int_parameter
    return get_int_parameter("audio_ingestion.max_words_per_utterance", 80)


def get_grounding_delivery_max_words() -> int:
    """Return SSOT strict word ceiling for grounding delivery boundary."""
    from .parameters import get_int_parameter
    return get_int_parameter("grounding.delivery_max_words", 500)


def get_runtime_infra_parameters() -> Dict[str, Any]:
    """Return SSOT runtime infrastructure parameters (concurrency, healing, judge, storage, timeouts)."""
    from .parameters import get_bool_parameter, get_float_parameter, get_int_parameter
    wal_ms = get_int_parameter("storage.sqlite_wal_busy_timeout_ms", 30000)
    db_sec = get_float_parameter("timeouts.database_busy_timeout_seconds", float(wal_ms) / 1000.0)
    return {
        "max_retrieval_workers": get_int_parameter("concurrency.max_retrieval_workers", 16),
        "healing_max_retries": get_int_parameter("healing.max_retries", 3),
        "healing_max_token_budget": get_int_parameter("healing.max_token_budget", 8192),
        "enable_dynamic_recap_bypass": get_bool_parameter("judge.enable_dynamic_recap_bypass", True),
        "connection_pool_size": get_int_parameter("storage.connection_pool_size", 5),
        "sqlite_wal_busy_timeout_ms": wal_ms,
        "audio_ingest_seconds": get_float_parameter("timeouts.audio_ingest_seconds", 300.0),
        "database_busy_timeout_seconds": db_sec,
    }


def get_output2_section_score_specs() -> Tuple[Tuple[str, int, float], ...]:
    """Return SSOT Output 2 judge section (name, max_points, pass_floor) tuples."""
    from .parameters import get_float_parameter, get_int_parameter
    return (
        (
            "header_and_overview",
            get_int_parameter("output2.section_weights.header_and_overview", 10),
            get_float_parameter("output2.section_floors.header_and_overview", 5.5),
        ),
        (
            "key_updates_technical_substance",
            get_int_parameter("output2.section_weights.key_updates_technical_substance", 35),
            get_float_parameter("output2.section_floors.key_updates_technical_substance", 19.0),
        ),
        (
            "conditional_extensions",
            get_int_parameter("output2.section_weights.conditional_extensions", 20),
            get_float_parameter("output2.section_floors.conditional_extensions", 11.0),
        ),
        (
            "appendix_stakeholder_perspectives",
            get_int_parameter("output2.section_weights.appendix_stakeholder_perspectives", 35),
            get_float_parameter("output2.section_floors.appendix_stakeholder_perspectives", 19.0),
        ),
    )


def get_canonical_mutation_budget() -> int:
    """Return SSOT maximum mutation budget ceiling across all pipelines."""
    from .parameters import get_int_parameter
    return get_int_parameter("judge.canonical_mutation_budget", 30)


def get_pipeline_settings_parameters() -> Dict[str, Any]:
    """Return SSOT bifurcated fault-tolerance retry and attachment settings."""
    from .parameters import get_bool_parameter, get_float_parameter, get_int_parameter
    return {
        "max_payload_retries": get_int_parameter("pipeline_settings.max_payload_retries", 1),
        "max_judge_retries_default": get_int_parameter("pipeline_settings.max_judge_retries_default", 1),
        "max_network_retries": get_int_parameter("pipeline_settings.max_network_retries", 5),
        "network_base_delay_seconds": get_float_parameter("pipeline_settings.network_base_delay_seconds", 0.5),
        "network_max_delay_seconds": get_float_parameter("pipeline_settings.network_max_delay_seconds", 30.0),
        "enable_attachment_mutation_automation": get_bool_parameter(
            "pipeline_settings.enable_attachment_mutation_automation", False
        ),
    }


def get_agent_sdk_defaults() -> Dict[str, int]:
    """Return SSOT Antigravity SDK agent budget and compaction defaults."""
    from .parameters import get_int_parameter
    return {
        "default_max_model_calls": get_int_parameter("agent_sdk.default_max_model_calls", 50),
        "default_max_tool_calls": get_int_parameter("agent_sdk.default_max_tool_calls", 100),
        "default_compaction_token_threshold": get_int_parameter("agent_sdk.default_compaction_token_threshold", 80000),
        "default_max_subagent_depth": get_int_parameter("agent_sdk.default_max_subagent_depth", 2),
    }


def get_audio_streaming_parameters() -> Dict[str, Any]:
    """Return SSOT audio streaming, context window, and voiceprint similarity parameters."""
    from .parameters import get_float_parameter, get_int_parameter
    return {
        "sample_rate_hz": get_int_parameter("audio_ingestion.sample_rate_hz", 16000),
        "ring_buffer_seconds": get_int_parameter("audio_ingestion.ring_buffer_seconds", 60),
        "diarizer_window_seconds": get_float_parameter("audio_ingestion.diarizer_window_seconds", 5.0),
        "diarizer_step_seconds": get_float_parameter("audio_ingestion.diarizer_step_seconds", 1.0),
        "silence_energy_threshold": get_float_parameter("audio_ingestion.silence_energy_threshold", 0.005),
        "max_window_sec": get_float_parameter("audio_ingestion.max_window_sec", 30.0),
        "min_window_sec": get_float_parameter("audio_ingestion.min_window_sec", 0.2),
        "voiceprint_similarity_threshold": get_float_parameter("audio_ingestion.voiceprint_similarity_threshold", 0.82),
    }


def get_asr_decode_parameters() -> Dict[str, Any]:
    """Return SSOT MLX Whisper decoding parameters."""
    from .parameters import get_bool_parameter, get_float_parameter, get_str_parameter
    return {
        "temperature": get_float_parameter("audio_ingestion.asr_decode.temperature", 0.0),
        "no_speech_threshold": get_float_parameter("audio_ingestion.asr_decode.no_speech_threshold", 0.60),
        "compression_ratio_threshold": get_float_parameter("audio_ingestion.asr_decode.compression_ratio_threshold", 2.40),
        "logprob_threshold": get_float_parameter("audio_ingestion.asr_decode.logprob_threshold", -1.0),
        "language": get_str_parameter("audio_ingestion.asr_decode.language", "en"),
        "fp16": get_bool_parameter("audio_ingestion.asr_decode.fp16", True),
    }


def get_local_mlx_model(profile: str = "instruct_14b") -> str:
    """Return SSOT Apple Silicon Metal local MLX model identifier by profile key."""
    from .parameters import get_str_parameter
    models = {
        "instruct_8b": get_str_parameter("local_mlx_models.instruct_8b", "mlx-community/Meta-Llama-3.1-8B-Instruct-4bit"),
        "instruct_14b": get_str_parameter("local_mlx_models.instruct_14b", "mlx-community/Qwen2.5-14B-Instruct-4bit"),
        "instruct_32b": get_str_parameter("local_mlx_models.instruct_32b", "mlx-community/Qwen2.5-32B-Instruct-4bit"),
        "reasoning_14b": get_str_parameter("local_mlx_models.reasoning_14b", "mlx-community/DeepSeek-R1-Distill-Qwen-14B-4bit"),
        "vision_7b": get_str_parameter("local_mlx_models.vision_7b", "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"),
        "whisper_draft": get_str_parameter("local_mlx_models.whisper_draft", "mlx-community/whisper-large-v3-mlx"),
        "whisper_tiny": get_str_parameter("local_mlx_models.whisper_draft", "mlx-community/whisper-large-v3-mlx"),
    }
    return models.get(profile, models["instruct_14b"])


import dual_pipeline.parameters_subsystems as _sub  # noqa: E402

get_authorization_parameters, get_decision_service_parameters = _sub.get_authorization_parameters, _sub.get_decision_service_parameters
get_default_assumption_ttl_days, get_document_security_parameters = _sub.get_default_assumption_ttl_days, _sub.get_document_security_parameters
get_graph_consolidation_parameters, get_graph_retrieval_parameters = _sub.get_graph_consolidation_parameters, _sub.get_graph_retrieval_parameters
get_graph_signal_parameters, get_index_core_parameters = _sub.get_graph_signal_parameters, _sub.get_index_core_parameters
get_l0_quarantine_thresholds, get_max_subagent_context_tokens, get_starvation_turn_ratio_threshold = _sub.get_l0_quarantine_thresholds, _sub.get_max_subagent_context_tokens, _sub.get_starvation_turn_ratio_threshold



"""Layer 0 Typed Parameter Registry & Accessor Engine (SSOT).
Resolution Order: 1. Env (NEE_PARAM_<KEY>) 2. SSOT (config/runtime/parameters.json) 3. Default.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import threading
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Optional

from config.credentials import CredentialManager
from .parameters_ops import (
    COPILOT_PARAMETER_BOUNDS,
    HITL_PARAMETER_BOUNDS,
    execute_compare_and_swap,
    execute_unset,
    save_parameter_file,
    validate_copilot_parameter_bounds,
    validate_hitl_parameter_bounds,
    validate_parameters_schema,
)


@dataclass
class ParameterRegistry:
    """
    Thread-safe Layer 0 accessor for system parameters and operational thresholds.
    """

    config_path: Path = field(
        default_factory=lambda: (
            Path(__file__).resolve().parent.parent / "config" / "runtime" / "parameters.json"
        )
    )
    _cached_data: Optional[Dict[str, Any]] = None
    _lock: threading.RLock = field(default_factory=threading.RLock)

    @classmethod
    def _env_key_for(cls, dotted_key: str) -> str:
        """Converts dotted key e.g. 'storage.sqlite_wal_busy_timeout_ms' to 'NEE_PARAM_STORAGE_SQLITE_WAL_BUSY_TIMEOUT_MS'."""
        clean = dotted_key.strip().replace(".", "_").replace("-", "_").upper()
        return f"NEE_PARAM_{clean}"

    def _load_json(self) -> Dict[str, Any]:
        with self._lock:
            if self._cached_data is not None:
                return self._cached_data
            if self.config_path.exists():
                try:
                    with open(self.config_path, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                        self._cached_data = raw.get("parameters", raw)
                        return self._cached_data
                except json.JSONDecodeError as e:
                    raise ValueError(f"Fail-Fast: Invalid JSON in {self.config_path}") from e
            self._cached_data = {}
            return self._cached_data

    def clear_cache(self) -> None:
        """Clears cached parameter dictionary."""
        with self._lock:
            self._cached_data = None

    def reload(self) -> Dict[str, Any]:
        """Forces reload of parameter JSON file."""
        self.clear_cache()
        return self._load_json()

    @staticmethod
    def _cast_env_val(val: str, default: Any) -> Any:
        if isinstance(default, bool):
            return val.lower() in ("true", "1", "yes", "on")
        if isinstance(default, int) and not isinstance(default, bool):
            try:
                return int(val)
            except ValueError:
                return val
        if isinstance(default, float):
            try:
                return float(val)
            except ValueError:
                return val
        return val

    @staticmethod
    def _lookup_dotted(data: Dict[str, Any], dotted_key: str) -> tuple[bool, Any]:
        curr: Any = data
        for p in dotted_key.split("."):
            if not isinstance(curr, dict) or p not in curr:
                return False, None
            curr = curr[p]
        return True, curr

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Retrieves parameter by dotted key with env override precedence."""
        env_key = self._env_key_for(dotted_key)
        val = CredentialManager.get_credential(env_key)
        if val is not None:
            return self._cast_env_val(val, default)

        found, val = self._lookup_dotted(self._load_json(), dotted_key)
        return val if found else default

    def get_int(self, dotted_key: str, default: int = 0) -> int:
        """Retrieves integer parameter with env override support."""
        res = self.get(dotted_key, default)
        try:
            return int(res)
        except (ValueError, TypeError):
            return default

    def get_float(self, dotted_key: str, default: float = 0.0) -> float:
        """Retrieves float parameter with env override support."""
        res = self.get(dotted_key, default)
        try:
            return float(res)
        except (ValueError, TypeError):
            return default

    def get_bool(self, dotted_key: str, default: bool = False) -> bool:
        """Retrieves boolean parameter with env override support."""
        res = self.get(dotted_key, default)
        if isinstance(res, bool):
            return res
        if isinstance(res, str):
            return res.lower() in ("true", "1", "yes", "on")
        return bool(res)

    def get_str(self, dotted_key: str, default: str = "") -> str:
        """Retrieves string parameter with env override support."""
        res = self.get(dotted_key, default)
        if res is None:
            return default
        return str(res)

    def set(
        self, dotted_key: str, value: Any, persist: bool = True, config_path: Optional[Path] = None
    ) -> Any:
        """Sets parameter in memory and optionally persists atomically to JSON."""
        if dotted_key in HITL_PARAMETER_BOUNDS and not validate_hitl_parameter_bounds(dotted_key, value):
            min_v, max_v, _ = HITL_PARAMETER_BOUNDS[dotted_key]
            raise ValueError(f"Parameter '{dotted_key}' value {value} out of bounds [{min_v}, {max_v}]")
        if dotted_key in COPILOT_PARAMETER_BOUNDS and not validate_copilot_parameter_bounds(dotted_key, value):
            min_v, max_v, _ = COPILOT_PARAMETER_BOUNDS[dotted_key]
            raise ValueError(f"Parameter '{dotted_key}' value {value} out of bounds [{min_v}, {max_v}]")
        target_path = config_path or self.config_path
        with self._lock:
            if config_path is not None and config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    data = dict(raw.get("parameters", raw))
            else:
                data = dict(self._load_json())
            parts = dotted_key.split(".")
            curr = data
            for p in parts[:-1]:
                if p not in curr or not isinstance(curr[p], dict):
                    curr[p] = {}
                curr = curr[p]
            curr[parts[-1]] = value
            self._cached_data = data

            if persist:
                save_parameter_file(target_path, data)
            return value

    def unset(
        self, dotted_key: str, persist: bool = True, config_path: Optional[Path] = None
    ) -> bool:
        """Unsets/deletes a parameter and optionally persists atomically to JSON."""
        return execute_unset(self, dotted_key, persist=persist, config_path=config_path)

    def compare_and_swap(
        self,
        dotted_key: str,
        expected_current_value: Any,
        new_value: Any,
        persist: bool = True,
        config_path: Optional[Path] = None,
    ) -> bool:
        """Atomically sets new_value only if current value matches expected_current_value."""
        return execute_compare_and_swap(
            self, dotted_key, expected_current_value, new_value, persist=persist, config_path=config_path
        )

    def compute_digest(self) -> str:
        """Computes deterministic RFC-8259 SHA-256 digest of parameters configuration."""
        data = self._load_json()
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# Global Singleton Instance
_GLOBAL_REGISTRY = ParameterRegistry()


def get_parameter(dotted_key: str, default: Any = None) -> Any:
    """Module-level accessor for parameters."""
    return _GLOBAL_REGISTRY.get(dotted_key, default)


def set_parameter(
    dotted_key: str, value: Any, persist: bool = True, config_path: Optional[Path] = None
) -> Any:
    """Module-level setter for parameters with atomic persistence."""
    return _GLOBAL_REGISTRY.set(dotted_key, value, persist=persist, config_path=config_path)


def unset_parameter(
    dotted_key: str, persist: bool = True, config_path: Optional[Path] = None
) -> bool:
    """Module-level unset/delete for parameters with atomic persistence."""
    return _GLOBAL_REGISTRY.unset(dotted_key, persist=persist, config_path=config_path)


def compare_and_swap_parameter(
    dotted_key: str,
    expected_current_value: Any,
    new_value: Any,
    persist: bool = True,
    config_path: Optional[Path] = None,
) -> bool:
    """Module-level compare-and-swap for parameters."""
    return _GLOBAL_REGISTRY.compare_and_swap(
        dotted_key, expected_current_value, new_value, persist=persist, config_path=config_path
    )


def get_int_parameter(dotted_key: str, default: int = 0) -> int:
    """Module-level accessor for integer parameters."""
    return _GLOBAL_REGISTRY.get_int(dotted_key, default)


def get_float_parameter(dotted_key: str, default: float = 0.0) -> float:
    """Module-level accessor for float parameters."""
    return _GLOBAL_REGISTRY.get_float(dotted_key, default)


def get_bool_parameter(dotted_key: str, default: bool = False) -> bool:
    """Module-level accessor for boolean parameters."""
    return _GLOBAL_REGISTRY.get_bool(dotted_key, default)


def get_str_parameter(dotted_key: str, default: str = "") -> str:
    """Module-level accessor for string parameters."""
    return _GLOBAL_REGISTRY.get_str(dotted_key, default)


def get_hitl_ambiguity_margin_threshold() -> float:
    """Returns SSOT threshold for ambiguity margin gating (default: 0.20)."""
    return get_float_parameter("hitl.ambiguity_margin_threshold", 0.20)


def needs_operator_decision(
    top_confidence: Any,
    second_confidence: Any,
    threshold: Optional[float] = None,
) -> bool:
    """SSOT accessor determining whether a confidence spread requires human operator decision.

    Enforces the governed ambiguity margin threshold (default: 0.20 inclusive).
    Uses Decimal arithmetic to prevent IEEE-754 floating point inaccuracies.

    Rules:
    - Returns True if spread <= threshold (inclusive).
    - Returns True if either confidence is NaN, Inf, None, negative, or invalid (fail-closed).
    - If threshold is not provided, dynamically resolves from get_hitl_ambiguity_margin_threshold().
    """
    if top_confidence is None:
        return True
    if second_confidence is None:
        return False

    try:
        t_f = float(top_confidence)
        s_f = float(second_confidence)
    except (ValueError, TypeError):
        return True

    if math.isnan(t_f) or math.isnan(s_f) or math.isinf(t_f) or math.isinf(s_f) or t_f < 0.0 or s_f < 0.0:
        return True

    raw_thresh = get_hitl_ambiguity_margin_threshold() if threshold is None else threshold

    try:
        thresh_f = float(raw_thresh)
    except (ValueError, TypeError):
        thresh_f = 0.20

    if thresh_f < 0.05 or thresh_f > 0.30:
        thresh_f = 0.20

    try:
        dt = Decimal(str(round(t_f, 8)))
        ds = Decimal(str(round(s_f, 8)))
        d_thresh = Decimal(str(round(thresh_f, 8)))
        gap = abs(dt - ds)
        return gap <= d_thresh
    except (InvalidOperation, Exception):
        return True


def get_hitl_divergence_margin_threshold() -> float:
    """Returns SSOT threshold for divergence margin gating (default: 0.20)."""
    return get_float_parameter("hitl.divergence_margin_threshold", 0.20)


def get_hitl_min_autonomous_confidence_floor() -> float:
    """Returns SSOT floor for minimum required top confidence for autonomous resolution (default: 0.65)."""
    return get_float_parameter("hitl.min_autonomous_confidence_floor", 0.65)


def get_hitl_entropy_ambiguity_threshold() -> float:
    """Returns SSOT normalized entropy threshold above which HITL surfacing is mandated (default: 0.75)."""
    return get_float_parameter("hitl.entropy_ambiguity_threshold", 0.75)


def get_hitl_bayesian_prior_strength() -> float:
    """Returns SSOT prior strength N0 for Beta-binomial learning (default: 4.0)."""
    return get_float_parameter("hitl.bayesian_prior_strength", 4.0)


def get_hitl_human_feedback_weight() -> float:
    """Returns SSOT weight for authentic human operator feedback (default: 1.0)."""
    return get_float_parameter("hitl.human_feedback_weight", 1.0)


def get_hitl_autonomous_feedback_weight() -> float:
    """Returns SSOT weight for autonomous engine feedback (default: 0.05)."""
    return get_float_parameter("hitl.autonomous_feedback_weight", 0.05)


def get_hitl_entity_clarification_enabled() -> bool:
    """Returns whether in-dialogue referenced entity clarification is enabled."""
    return get_bool_parameter("hitl.entity_clarification_enabled", True)


def get_hitl_unresolved_entity_margin_threshold() -> float:
    """Returns margin threshold for referenced entity clarification (default: 0.15)."""
    return get_float_parameter("hitl.unresolved_entity_margin_threshold", 0.15)


def get_hitl_uncertainty_display() -> str:
    """Returns display mode for confidence intervals ('always', 'sparse_only', 'never'; default: 'sparse_only')."""
    return get_str_parameter("hitl.uncertainty_display", "sparse_only")


def get_hitl_autonomous_rollout_mode() -> str:
    """Returns rollout mode for autonomous decision resolution ('DISABLED', 'OBSERVE_ONLY', 'ENABLED'; default: 'DISABLED')."""
    return get_str_parameter("hitl.autonomous_rollout_mode", "DISABLED")


def get_hitl_autonomous_kill_switch() -> bool:
    """Returns whether the autonomous resolution kill-switch is engaged (default: False)."""
    return get_bool_parameter("hitl.autonomous_kill_switch", False)


def get_copilot_context_focus_seconds() -> int:
    return get_int_parameter("copilot.context.focus_seconds", 15)
def get_copilot_context_decay_tau_seconds() -> int:
    return get_int_parameter("copilot.context.decay_tau_seconds", 300)
def get_copilot_context_salient_entities_top_k() -> int:
    return get_int_parameter("copilot.context.salient_entities_top_k", 8)
def get_copilot_context_flow_items_max() -> int:
    return get_int_parameter("copilot.context.flow_items_max", 10)
def get_copilot_context_focus_weight() -> float:
    return get_float_parameter("copilot.context.focus_weight", 0.6)
def get_copilot_neighborhood_max_hops() -> int:
    return get_int_parameter("copilot.neighborhood_max_hops", 2)
def get_copilot_rigor_budget_ms() -> float:
    return get_float_parameter("copilot.rigor.budget_ms", 250.0)
def get_copilot_rigor_min_provenance_score() -> float:
    return get_float_parameter("copilot.rigor.min_provenance_score", 0.15)

def get_copilot_rerank_weights() -> dict[str, float]:
    """Returns normalized weights for lexical, structural, and dense reranking."""
    lex, struct, dense = (
        get_float_parameter("copilot.rerank.lexical", 0.3),
        get_float_parameter("copilot.rerank.structural", 0.3),
        get_float_parameter("copilot.rerank.dense", 0.4),
    )
    total = lex + struct + dense
    if total <= 0:
        return {"lexical": 0.3, "structural": 0.3, "dense": 0.4}
    return {"lexical": lex / total, "structural": struct / total, "dense": dense / total}


def get_cross_meeting_context_window_days() -> int:
    """Returns sliding window in days for cross-meeting context retrieval (default: 7)."""
    return get_int_parameter("hitl.cross_meeting_context_window_days", 7)


def get_cross_meeting_max_context_tokens() -> int:
    """Returns token budget cap for cross-meeting context injection (default: 8192)."""
    return get_int_parameter("hitl.cross_meeting_max_context_tokens", 8192)


def get_speaker_disambiguation_escalation_enabled() -> bool:
    """Returns whether speaker disambiguator escalates to HITL on ambiguity instead of silent fallback."""
    return get_bool_parameter("hitl.speaker_disambiguation_escalation_enabled", True)


def get_svp_political_materiality_threshold(is_executive: bool = False) -> int:
    """Returns SSOT threshold for SVP political materiality judge (default: 6 for standard, 8 for executive)."""
    if is_executive:
        return get_int_parameter("judge.svp_executive_materiality_threshold", 8)
    return get_int_parameter("judge.svp_political_materiality_threshold", 6)


def is_deterministic_judge_prefilter_enabled() -> bool:
    """Returns True if deterministic pre-filtering is enabled before calling LLM judges."""
    return get_bool_parameter("judge.enable_deterministic_prefilter", True)


def reload_parameters() -> Dict[str, Any]:
    """Forces reload of global parameter registry."""
    return _GLOBAL_REGISTRY.reload()

def compute_parameters_digest() -> str:
    """Computes SHA-256 digest of parameters JSON."""
    return _GLOBAL_REGISTRY.compute_digest()

import dual_pipeline.parameters_ops as _ops  # noqa: E402
import dual_pipeline.parameters_subsystems as _sub  # noqa: E402

get_agent_sdk_defaults, get_asr_decode_parameters = _ops.get_agent_sdk_defaults, _ops.get_asr_decode_parameters
get_audio_max_words_per_utterance, get_audio_streaming_parameters = _ops.get_audio_max_words_per_utterance, _ops.get_audio_streaming_parameters
get_canonical_mutation_budget, get_copilot_speech_limits = _ops.get_canonical_mutation_budget, _ops.get_copilot_speech_limits
get_engineering_output_limits, get_planning_word_limits = _ops.get_engineering_output_limits, _ops.get_planning_word_limits
compute_scaled_plan_word_limit = _ops.compute_scaled_plan_word_limit
get_grounding_delivery_max_words, get_local_mlx_model = _ops.get_grounding_delivery_max_words, _ops.get_local_mlx_model
get_output1_word_density_bounds, get_output2_c21_speaker_tier_floors = _ops.get_output1_word_density_bounds, _ops.get_output2_c21_speaker_tier_floors
get_output2_dag_word_floors, get_output2_default_word_limits = _ops.get_output2_dag_word_floors, _ops.get_output2_default_word_limits
get_output2_passive_hedging_threshold, get_output2_section_density_floors = _ops.get_output2_passive_hedging_threshold, _ops.get_output2_section_density_floors
get_output2_section_score_specs, get_output2_tier1_word_limits = _ops.get_output2_section_score_specs, _ops.get_output2_tier1_word_limits
get_output2_tier3_word_limits, get_output2_word_count_limits = _ops.get_output2_tier3_word_limits, _ops.get_output2_word_count_limits
compute_output2_duration_word_bounds = _ops.compute_output2_duration_word_bounds
get_pipeline_settings_parameters, get_runtime_infra_parameters = _ops.get_pipeline_settings_parameters, _ops.get_runtime_infra_parameters
get_authorization_parameters, get_decision_service_parameters = _sub.get_authorization_parameters, _sub.get_decision_service_parameters
get_default_assumption_ttl_days, get_document_security_parameters = _sub.get_default_assumption_ttl_days, _sub.get_document_security_parameters
get_graph_consolidation_parameters, get_graph_retrieval_parameters = _sub.get_graph_consolidation_parameters, _sub.get_graph_retrieval_parameters
get_graph_signal_parameters, get_index_core_parameters = _sub.get_graph_signal_parameters, _sub.get_index_core_parameters
get_l0_quarantine_thresholds, get_max_subagent_context_tokens, get_starvation_turn_ratio_threshold = _sub.get_l0_quarantine_thresholds, _sub.get_max_subagent_context_tokens, _sub.get_starvation_turn_ratio_threshold
from dual_pipeline.project_aliases import (  # noqa: E402, lazy-decouple
    UNRESOLVED_PROJECT_SENTINEL, get_domain_for_project, get_known_projects,
    get_project_aliases, get_project_domains, infer_project_from_title, resolve_project_name,
)


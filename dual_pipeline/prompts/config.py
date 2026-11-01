"""
dual_pipeline/prompts/config.py

Centralized configuration and settings SSOT for prompts, gates, and LLM judges.
Dynamically resolves thresholds, token budgets, and word limits from ParameterRegistry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from dual_pipeline.model_registry import ModelTier, resolve_tier_model
from dual_pipeline.parameters import get_float_parameter, get_int_parameter, get_local_mlx_model


@dataclass(frozen=True)
class JudgeExecutionConfig:
    """Execution parameters for LLM judges across Outputs 1, 2, and 3."""
    tier: ModelTier = ModelTier.DIALECTICAL_JUDGE
    default_model: Optional[str] = None
    default_reasoning_effort: str = "high"
    adversarial_model: Optional[str] = None
    adversarial_reasoning_effort: str = "high"
    local_model: str = field(default_factory=lambda: get_local_mlx_model("instruct_14b"))
    _max_input_tokens: Optional[int] = None
    _max_completion_tokens: Optional[int] = None
    _deadline_seconds: Optional[float] = None
    _heartbeat_interval: Optional[float] = None

    @property
    def max_input_tokens(self) -> int:
        return self._max_input_tokens if self._max_input_tokens is not None else get_int_parameter("judge.max_input_tokens", 150000)

    @property
    def max_completion_tokens(self) -> int:
        return self._max_completion_tokens if self._max_completion_tokens is not None else get_int_parameter("judge.max_completion_tokens", 64000)

    @property
    def deadline_seconds(self) -> float:
        return self._deadline_seconds if self._deadline_seconds is not None else get_float_parameter("judge.deadline_seconds", 600.0)

    @property
    def heartbeat_interval(self) -> float:
        return self._heartbeat_interval if self._heartbeat_interval is not None else get_float_parameter("judge.heartbeat_interval_seconds", 15.0)

    def resolve_model_and_effort(
        self,
        requested_model: Optional[str] = None,
        requested_effort: Optional[str] = None,
        use_adversarial: bool = False,
        use_local: bool = False,
    ) -> tuple[str, str]:
        """Resolves target model and reasoning effort from registry or environment."""
        if use_local:
            return self.local_model, "medium"

        reg_model, reg_effort = resolve_tier_model(self.tier)

        if use_adversarial:
            adv_m, adv_e = resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
            return (
                requested_model or self.adversarial_model or adv_m,
                requested_effort or self.adversarial_reasoning_effort or adv_e or "high",
            )

        if requested_model:
            return requested_model, requested_effort or self.default_reasoning_effort

        return (
            self.default_model or reg_model,
            requested_effort or reg_effort or self.default_reasoning_effort,
        )


@dataclass(frozen=True)
class Output1Config:
    """Configuration for Output 01 (Forensic Transcript), backed by ParameterRegistry."""
    _max_tokens: Optional[int] = None
    _min_word_count: Optional[int] = None
    _max_word_count: Optional[int] = None
    _generic_speaker_ceiling_percent: Optional[float] = None
    _single_speaker_collapse_threshold: Optional[float] = None

    @property
    def max_tokens(self) -> int:
        return self._max_tokens if self._max_tokens is not None else get_int_parameter("output1.max_tokens", 96000)

    @property
    def min_word_count(self) -> int:
        return self._min_word_count if self._min_word_count is not None else get_int_parameter("output1.min_word_count", 100)

    @property
    def max_word_count(self) -> int:
        return self._max_word_count if self._max_word_count is not None else get_int_parameter("output1.max_word_count", 50000)

    @property
    def generic_speaker_ceiling_percent(self) -> float:
        return (
            self._generic_speaker_ceiling_percent
            if self._generic_speaker_ceiling_percent is not None
            else get_float_parameter("output1.generic_speaker_ceiling_percent", 15.0)
        )

    @property
    def single_speaker_collapse_threshold(self) -> float:
        return (
            self._single_speaker_collapse_threshold
            if self._single_speaker_collapse_threshold is not None
            else get_float_parameter("output1.single_speaker_collapse_threshold", 0.85)
        )


@dataclass(frozen=True)
class Output2Config:
    """Configuration for Output 02 (Executive Strategic Brief / Recap), backed by ParameterRegistry."""
    _max_tokens: Optional[int] = None
    _min_word_count: Optional[int] = None
    _max_word_count: Optional[int] = None
    _density_floor_short: Optional[int] = None
    _density_floor_long: Optional[int] = None
    _min_themes_per_speaker: Optional[int] = None
    _min_words_per_theme: Optional[int] = None

    @property
    def max_tokens(self) -> int:
        return self._max_tokens if self._max_tokens is not None else get_int_parameter("output2.max_tokens", 60000)

    @property
    def min_word_count(self) -> int:
        return self._min_word_count if self._min_word_count is not None else get_int_parameter("output2.min_word_count", 500)

    @property
    def max_word_count(self) -> int:
        return self._max_word_count if self._max_word_count is not None else get_int_parameter("output2.max_word_count", 5000)

    @property
    def density_floor_short(self) -> int:
        return self._density_floor_short if self._density_floor_short is not None else get_int_parameter("output2.density_floor_short", 30)

    @property
    def density_floor_long(self) -> int:
        return self._density_floor_long if self._density_floor_long is not None else get_int_parameter("output2.density_floor_long", 60)

    @property
    def min_themes_per_speaker(self) -> int:
        return self._min_themes_per_speaker if self._min_themes_per_speaker is not None else get_int_parameter("output2.min_themes_per_speaker", 2)

    @property
    def min_words_per_theme(self) -> int:
        return self._min_words_per_theme if self._min_words_per_theme is not None else get_int_parameter("output2.min_words_per_theme", 30)


@dataclass(frozen=True)
class Output3Config:
    """Configuration for Output 03 (Canonical Signal Projection & Evidence Envelope), backed by ParameterRegistry."""
    _max_tokens: Optional[int] = None
    _min_word_count: Optional[int] = None
    _max_word_count: Optional[int] = None
    _max_mutations_per_batch: Optional[int] = None
    allowed_statement_kinds: tuple[str, ...] = (
        "summary", "technical", "decision", "risk", "strategic", "dependency",
    )
    allowed_entity_prefixes: tuple[str, ...] = (
        "person:", "org:", "system:", "workstream:", "project:", "metric:", "role:", "policy:",
        "deliverable:", "milestone:", "forum:", "model:", "initiative:", "capability:", "pattern:", "decision:", "proposal:", "stakeholder:", "document:",
    )

    @property
    def max_tokens(self) -> int:
        return self._max_tokens if self._max_tokens is not None else get_int_parameter("output3.max_tokens", 80000)

    @property
    def min_word_count(self) -> int:
        return self._min_word_count if self._min_word_count is not None else get_int_parameter("output3.min_word_count", 50)

    @property
    def max_word_count(self) -> int:
        return self._max_word_count if self._max_word_count is not None else get_int_parameter("output3.max_word_count", 20000)

    @property
    def max_mutations_per_batch(self) -> int:
        return self._max_mutations_per_batch if self._max_mutations_per_batch is not None else get_int_parameter("output3.max_mutations_per_batch", 50)


@dataclass(frozen=True)
class EnginePromptConfigSSOT:
    """Global configuration envelope uniting all output deliverables and judges."""
    judge: JudgeExecutionConfig = field(default_factory=JudgeExecutionConfig)
    output1: Output1Config = field(default_factory=Output1Config)
    output2: Output2Config = field(default_factory=Output2Config)
    output3: Output3Config = field(default_factory=Output3Config)


CANONICAL_PROMPT_CONFIG = EnginePromptConfigSSOT()

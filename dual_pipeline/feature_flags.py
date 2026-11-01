"""
dual_pipeline/feature_flags.py

Enterprise Feature Flag Architecture for Pipeline Stages and Experimental Capabilities.
Supports:
1. Three execution modes: ON (active & enforcing), OFF (skipped), and SHADOW (runs with non-blocking error tolerance).
2. Configuration precedence: Runtime Override -> Environment Variable (NEE_FLAG_<NAME>) -> Config File -> Built-in Defaults.
3. Hermetic testability with scoped flag contexts.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional

logger = logging.getLogger("dual_pipeline.feature_flags")


class FeatureFlagState(str, Enum):
    ON = "ON"
    OFF = "OFF"
    SHADOW = "SHADOW"


DEFAULT_FLAGS: Dict[str, FeatureFlagState] = {
    "CASCADE": FeatureFlagState.ON,
    "UPSTREAM_AUDIO_QA": FeatureFlagState.ON,
    "L1_REVIEWER": FeatureFlagState.ON,
    "CHECKPOINTS": FeatureFlagState.ON,
    "DLQ": FeatureFlagState.ON,
    "COST_GUARDRAILS": FeatureFlagState.ON,
    "MULTI_MEETING_DETECTION": FeatureFlagState.ON,
    "EXPERIMENTAL_SPEAKER_DIARIZATION": FeatureFlagState.OFF,
    "EXPERIMENTAL_STREAMING_SYNTHESIS": FeatureFlagState.OFF,
    "BOUNDED_QUERY_RECOVERY": FeatureFlagState.ON,
    "DOCUMENT_OPTIMIZATION": FeatureFlagState.OFF,
}


def _parse_flag_value(val: str) -> FeatureFlagState:
    norm = str(val).strip().upper()
    if norm in ("ON", "1", "TRUE", "YES", "ENABLED"):
        return FeatureFlagState.ON
    if norm in ("OFF", "0", "FALSE", "NO", "DISABLED"):
        return FeatureFlagState.OFF
    if norm in ("SHADOW", "MONITOR", "DARK"):
        return FeatureFlagState.SHADOW
    logger.warning("Unrecognized feature flag value '%s', defaulting to OFF", val)
    return FeatureFlagState.OFF


class FeatureFlagManager:
    """Manages feature flag states with environment overrides and execution scoping."""

    def __init__(self, config_file: Optional[Path] = None) -> None:
        self.config_file = config_file
        self._overrides: Dict[str, FeatureFlagState] = {}
        self._file_defaults: Dict[str, FeatureFlagState] = {}
        self._load_from_file()

    def _load_from_file(self) -> None:
        if self.config_file and self.config_file.is_file():
            try:
                data = json.loads(self.config_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    flags = data.get("flags", data)
                    for k, v in flags.items():
                        self._file_defaults[k.upper()] = _parse_flag_value(str(v))
            except Exception as exc:
                logger.warning("Failed loading feature flags from %s: %s", self.config_file, exc)

    def get_state(self, flag_name: str) -> FeatureFlagState:
        normalized = flag_name.upper()
        # 1. Programmatic runtime overrides
        if normalized in self._overrides:
            return self._overrides[normalized]

        # 2. Environment variable override: NEE_FLAG_<NAME>
        from dual_pipeline.credentials import CredentialManager

        env_var = f"NEE_FLAG_{normalized}"
        if CredentialManager.has_credential(env_var):
            return _parse_flag_value(CredentialManager.get_credential(env_var) or "")

        # 3. Config file defaults
        if normalized in self._file_defaults:
            return self._file_defaults[normalized]

        # 4. Built-in defaults
        return DEFAULT_FLAGS.get(normalized, FeatureFlagState.OFF)

    def is_on(self, flag_name: str) -> bool:
        return self.get_state(flag_name) == FeatureFlagState.ON

    def is_off(self, flag_name: str) -> bool:
        return self.get_state(flag_name) == FeatureFlagState.OFF

    def is_shadow(self, flag_name: str) -> bool:
        return self.get_state(flag_name) == FeatureFlagState.SHADOW

    def is_enabled(self, flag_name: str) -> bool:
        """True if ON or SHADOW (active execution)."""
        return self.get_state(flag_name) in (FeatureFlagState.ON, FeatureFlagState.SHADOW)

    def set_flag(self, flag_name: str, state: FeatureFlagState | str) -> None:
        parsed = state if isinstance(state, FeatureFlagState) else _parse_flag_value(state)
        self._overrides[flag_name.upper()] = parsed

    def clear_overrides(self) -> None:
        self._overrides.clear()

    @contextmanager
    def override(self, flag_name: str, state: FeatureFlagState | str) -> Iterator[None]:
        norm = flag_name.upper()
        prev = self._overrides.get(norm)
        self.set_flag(norm, state)
        try:
            yield
        finally:
            if prev is not None:
                self._overrides[norm] = prev
            else:
                self._overrides.pop(norm, None)

    def execute_stage(
        self,
        flag_name: str,
        stage_fn: Callable[..., Any],
        *args: Any,
        default_return: Any = None,
        **kwargs: Any,
    ) -> Any:
        """
        Executes stage_fn governed by feature flag status:
        - OFF: Stage is skipped cleanly, returning default_return.
        - SHADOW: Stage runs. If an error occurs, it is logged and swallowed, returning default_return.
        - ON: Stage runs normally, raising any errors that occur.
        """
        state = self.get_state(flag_name)
        stage_label = getattr(stage_fn, "__name__", str(stage_fn))

        if state == FeatureFlagState.OFF:
            logger.info("Stage '%s' skipped (flag %s is OFF)", stage_label, flag_name)
            return default_return

        if state == FeatureFlagState.SHADOW:
            logger.info("Stage '%s' running in SHADOW mode (flag %s)", stage_label, flag_name)
            try:
                return stage_fn(*args, **kwargs)
            except Exception as exc:
                logger.warning(
                    "[SHADOW FAILURE] Stage '%s' raised %s: %s (non-fatal, proceeding)",
                    stage_label,
                    type(exc).__name__,
                    exc,
                )
                return default_return

        return stage_fn(*args, **kwargs)


_DEFAULT_MANAGER: Optional[FeatureFlagManager] = None


def get_feature_flag_manager() -> FeatureFlagManager:
    global _DEFAULT_MANAGER
    if _DEFAULT_MANAGER is None:
        _DEFAULT_MANAGER = FeatureFlagManager()
    return _DEFAULT_MANAGER

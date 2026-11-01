"""Policy loading and validation for X1 quality controls."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .._validators import require_positive_int, require_strict_keys
from ..core import digest
from .models import (
    QUALITY_ASSURANCE_PROFILES,
    X1_QUALITY_POLICY_PATH,
    ContentRepairPolicy,
    QualityModelPolicy,
    X1QualityConfig,
    X1QualityMode,
)


def _strict_keys(value: Mapping[str, Any], *, required: set[str], label: str) -> None:
    require_strict_keys(value, required=required, label=label)


def _positive_int(value: Any, *, label: str, allow_zero: bool = False) -> int:
    return require_positive_int(value, label=label, allow_zero=allow_zero)


def _load_model_policy(value: Any, *, label: str) -> QualityModelPolicy:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    _strict_keys(
        value,
        required={
            "provider",
            "model",
            "reasoning_effort",
            "maximum_calls_per_run",
            "maximum_output_tokens",
        },
        label=label,
    )
    provider = str(value["provider"])
    model = str(value["model"])
    effort = str(value["reasoning_effort"])
    if provider != "google":
        raise ValueError(f"{label}.provider must be google")
    if not model.startswith("gemini-"):
        raise ValueError(f"{label}.model must be an explicitly governed Gemini model")
    if effort not in {"minimal", "low", "medium", "high"}:
        raise ValueError(f"{label}.reasoning_effort is invalid")
    return QualityModelPolicy(
        provider=provider,
        model=model,
        reasoning_effort=effort,
        maximum_calls_per_run=_positive_int(
            value["maximum_calls_per_run"], label=f"{label}.maximum_calls_per_run"
        ),
        maximum_output_tokens=_positive_int(
            value["maximum_output_tokens"], label=f"{label}.maximum_output_tokens"
        ),
    )


def load_x1_quality_config(path: Path = X1_QUALITY_POLICY_PATH) -> X1QualityConfig:
    resolved = Path(path).expanduser().resolve()
    raw = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("X1 quality policy must be an object")
    _strict_keys(
        raw,
        required={
            "schema_version",
            "mode",
            "provider_egress_allowed",
            "endpoint_template",
            "api_key_env",
            "eligible_assurance_profiles",
            "maximum_request_chars",
            "challenger",
            "judge",
            "content_repair",
        },
        label="x1-quality",
    )
    if raw["schema_version"] != "2.0.0":
        raise ValueError("X1 quality policy must use schema version 2.0.0")
    mode = X1QualityMode(str(raw["mode"]))
    if not isinstance(raw["provider_egress_allowed"], bool):
        raise ValueError("x1-quality.provider_egress_allowed must be boolean")
    endpoint_template = str(raw["endpoint_template"])
    if endpoint_template != (
        "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    ):
        raise ValueError("X1 quality endpoint must be the governed Google HTTPS endpoint")
    api_key_env = str(raw["api_key_env"])
    if api_key_env != "GOOGLE_API_KEY":
        raise ValueError("X1 quality api_key_env must be GOOGLE_API_KEY")
    assurance_profiles = raw["eligible_assurance_profiles"]
    if not isinstance(assurance_profiles, list) or set(assurance_profiles) != QUALITY_ASSURANCE_PROFILES:
        raise ValueError("X1 quality eligibility must be exactly standard and enhanced")
    content_repair = raw["content_repair"]
    if not isinstance(content_repair, Mapping):
        raise ValueError("x1-quality.content_repair must be an object")
    _strict_keys(
        content_repair,
        required={"maximum_calls_per_run", "eligible_assurance_profiles"},
        label="x1-quality.content_repair",
    )
    repair_profiles = content_repair["eligible_assurance_profiles"]
    if not isinstance(repair_profiles, list) or set(repair_profiles) != {"enhanced"}:
        raise ValueError("content repair must be limited to enhanced assurance")
    from config.credentials import X1_CHALLENGER_DEFAULT_MODEL, X1_JUDGE_DEFAULT_MODEL
    challenger = _load_model_policy(raw["challenger"], label="x1-quality.challenger")
    judge = _load_model_policy(raw["judge"], label="x1-quality.judge")
    if (challenger.model, challenger.reasoning_effort) != (X1_CHALLENGER_DEFAULT_MODEL, "medium"):
        raise ValueError(f"challenger must use {X1_CHALLENGER_DEFAULT_MODEL} at medium reasoning")
    if (judge.model, judge.reasoning_effort) != (X1_JUDGE_DEFAULT_MODEL, "high"):
        raise ValueError(f"judge must use {X1_JUDGE_DEFAULT_MODEL} at high reasoning")
    if challenger.maximum_calls_per_run != 1 or judge.maximum_calls_per_run != 2:
        raise ValueError("X1 quality call caps must be challenger=1 and judge=2")
    maximum_request_chars = _positive_int(
        raw["maximum_request_chars"], label="x1-quality.maximum_request_chars"
    )
    if maximum_request_chars > 256000:
        raise ValueError("X1 quality request ceiling cannot exceed the governed G20 ceiling")
    repair_calls = _positive_int(
        content_repair["maximum_calls_per_run"],
        label="x1-quality.content_repair.maximum_calls_per_run",
        allow_zero=True,
    )
    if repair_calls != 1:
        raise ValueError("content repair must permit exactly one call")
    return X1QualityConfig(
        schema_version="2.0.0",
        mode=mode,
        provider_egress_allowed=bool(raw["provider_egress_allowed"]),
        endpoint_template=endpoint_template,
        api_key_env=api_key_env,
        eligible_assurance_profiles=frozenset(str(value) for value in assurance_profiles),
        maximum_request_chars=maximum_request_chars,
        challenger=challenger,
        judge=judge,
        content_repair=ContentRepairPolicy(
            maximum_calls_per_run=repair_calls,
            eligible_assurance_profiles=frozenset(str(value) for value in repair_profiles),
        ),
        config_digest=digest(raw),
    )

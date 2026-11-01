"""Validation and loading helpers for L2 policies."""
from __future__ import annotations

import re
from typing import Any, Mapping

from config.credentials import CredentialManager

from .models import (
    ModelPolicy,
    PricingPolicy,
    ProviderFailureCode,
    QueryAssuranceProfile,
)


def _strict_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
    label: str,
) -> None:
    allowed = required | (optional or set())
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - allowed)
    if missing or unknown:
        raise ValueError(f"{label} keys are invalid; missing={missing}, unknown={unknown}")



def _positive_int(value: Any, *, label: str, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    return value


def _price(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) < 0:
        raise ValueError(f"{label} must be a nonnegative number")
    return float(value)


def _load_model_policy(
    value: Any,
    *,
    expected_profile: QueryAssuranceProfile,
    label: str,
) -> ModelPolicy:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    required = {
        "provider",
        "model_family",
        "model",
        "assurance_profile",
        "reasoning_effort",
        "maximum_primary_calls_per_run",
        "maximum_repair_calls_per_run",
        "maximum_escalation_calls_per_run",
        "pricing_usd_per_million_tokens",
    }
    _strict_keys(value, required=required, label=label)
    profile = QueryAssuranceProfile(str(value["assurance_profile"]))
    if profile is not expected_profile:
        raise ValueError(
            f"{label}.assurance_profile must be {expected_profile.value}"
        )
    model = str(value["model"])
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,127}", model):
        raise ValueError(f"{label}.model must be a governed model identifier")
    effort = str(value["reasoning_effort"])
    provider = str(value.get("provider", "openai"))
    if provider not in ("openai", "google"):
        raise ValueError(f"{label}.provider must be openai or google")
    from dual_pipeline.model_registry import is_model_approved, get_approved_models
    if not is_model_approved(model, provider):
        raise ValueError(
            f"{label}.model '{model}' is not an approved model for provider '{provider}'. "
            f"Approved: {sorted(get_approved_models(provider))}"
        )
    if provider == "google":
        valid_efforts = {"none", "low", "medium", "high", "xhigh", "max"}
    else:
        valid_efforts = {
            QueryAssuranceProfile.STANDARD: {"high", "xhigh"},
            QueryAssuranceProfile.ENHANCED: {"high", "max"},
        }[profile]
    if effort not in valid_efforts:
        raise ValueError(f"{label}.reasoning_effort must be one of {sorted(list(valid_efforts))}")
    prices = value["pricing_usd_per_million_tokens"]
    if not isinstance(prices, Mapping):
        raise ValueError(f"{label}.pricing_usd_per_million_tokens must be an object")
    _strict_keys(prices, required={"input", "cached_input", "output"}, label=f"{label}.pricing")
    policy = ModelPolicy(
        provider=provider,
        model_family=str(value["model_family"]),
        model=model,
        assurance_profile=profile,
        reasoning_effort=effort,
        maximum_primary_calls_per_run=_positive_int(
            value["maximum_primary_calls_per_run"], label=f"{label}.maximum_primary_calls_per_run"
        ),
        maximum_repair_calls_per_run=_positive_int(
            value["maximum_repair_calls_per_run"],
            label=f"{label}.maximum_repair_calls_per_run",
            allow_zero=True,
        ),
        maximum_escalation_calls_per_run=_positive_int(
            value["maximum_escalation_calls_per_run"],
            label=f"{label}.maximum_escalation_calls_per_run",
            allow_zero=True,
        ),
        pricing=PricingPolicy(
            input_per_million=_price(prices["input"], label=f"{label}.pricing.input"),
            cached_input_per_million=_price(
                prices["cached_input"], label=f"{label}.pricing.cached_input"
            ),
            output_per_million=_price(prices["output"], label=f"{label}.pricing.output"),
        ),
        context_window_tokens=_positive_int(
            value.get("context_window_tokens", 128000), label=f"{label}.context_window_tokens"
        ),
    )
    if policy.provider not in ("openai", "google"):
        raise ValueError(f"{label}.provider must be openai or google")
    expected_repairs = 0 if profile is QueryAssuranceProfile.STANDARD else 1
    if policy.maximum_primary_calls_per_run != 1:
        raise ValueError(f"{label} primary cap must be one")
    if policy.maximum_repair_calls_per_run != expected_repairs:
        raise ValueError(f"{label} repair cap must be {expected_repairs}")
    allowed_escalations = {0} if profile is QueryAssuranceProfile.STANDARD else {1, 2}
    if policy.maximum_escalation_calls_per_run not in allowed_escalations:
        raise ValueError(
            f"{label} quality-escalation cap must be one of {sorted(allowed_escalations)}"
        )
    return policy


def external_provider_offline(environment: Mapping[str, str] | None = None) -> bool:
    values = CredentialManager.get_process_environment() if environment is None else environment
    return any(
        str(values.get(name, "")).strip().casefold() in {"1", "true", "yes", "on"}
        for name in ("NEE_EXTERNAL_L2_OFFLINE", "NEE_OFFLINE")
    )


def resolve_openai_model_id(model_policy: ModelPolicy) -> str:
    """Return the model selected by the immutable OpenAI policy snapshot."""

    if model_policy.provider != "openai":
        from ..l2_providers_core.types import L2ProviderError
        raise L2ProviderError(
            ProviderFailureCode.UNSUPPORTED_MODEL,
            "selected model does not belong to the governed OpenAI provider",
        )
    return model_policy.model

"""Policy configuration and query retrieval governance for L2 execution."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from ..core import digest, read_json
from .constants import (
    L2_POLICY_PATH,
    QUERY_TYPE_POLICY_MATRIX,
    RETRIEVAL_MODE_LANES,
)
from .helpers import (
    _load_model_policy,
    _positive_int,
    _strict_keys,
)
from .intent import canonical_query_type
from .models import (
    CallPurpose,
    L2Config,
    ModelPolicy,
    OutputPolicy,
    QueryAssuranceProfile,
    QueryTypePolicy,
    ReliabilityPolicy,
    RequestBudgetPolicy,
)


def query_type_policy(query_type: str) -> QueryTypePolicy:
    canonical = canonical_query_type(query_type)
    try:
        return QUERY_TYPE_POLICY_MATRIX[canonical]
    except KeyError as exc:
        raise ValueError(f"unsupported query type: {query_type}") from exc


def retrieval_lanes_for_mode(retrieval_mode: str) -> tuple[str, ...]:
    """Resolve a fixed query retrieval mode without accepting caller lane lists."""

    try:
        return RETRIEVAL_MODE_LANES[retrieval_mode]
    except KeyError as exc:
        raise ValueError(f"unsupported query retrieval mode: {retrieval_mode}") from exc


def query_retrieval_policy(query_type: str) -> tuple[str, tuple[str, ...]]:
    """Return the fixed retrieval posture for a governed query route.

    Candidate material is never admitted by a caller-supplied lane list.
    Candidate review and strategic reasoning are explicit postures: the latter
    may consider labeled candidate and external context without converting
    either into approved internal fact.
    """

    canonical = canonical_query_type(query_type)
    query_type_policy(canonical)
    if canonical == "candidate_promotion_review":
        retrieval_mode = "candidate_review"
    elif canonical == "project_status_current":
        retrieval_mode = "status_snapshot_current"
    elif canonical == "project_status_history":
        retrieval_mode = "status_snapshot_history"
    elif canonical == "opportunity_nba":
        retrieval_mode = "strategic"
    else:
        retrieval_mode = "factual"
    return retrieval_mode, retrieval_lanes_for_mode(retrieval_mode)


def query_model_policy_for_assurance(
    config: L2Config,
    assurance_profile: str,
) -> ModelPolicy:
    """Bind query assurance to its governed model policy."""

    return config.model_for_assurance(assurance_profile)


def _validate_provider_settings(provider_class: str, endpoint: str, api_key_env: str) -> None:
    if provider_class not in ("openai", "google", "unified"):
        raise ValueError("governed L2 provider class must be openai, google, or unified")
    if provider_class == "openai":
        if endpoint != "https://api.openai.com/v1/responses":
            raise ValueError("the governed OpenAI endpoint must use HTTPS Responses API")
        if api_key_env != "OPENAI_API_KEY":
            raise ValueError("api_key_env must be OPENAI_API_KEY")
    elif provider_class == "google":
        if not endpoint.startswith("https://generativelanguage.googleapis.com"):
            raise ValueError("the governed Google endpoint must use HTTPS Google Generative Language API")
        if api_key_env not in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
            raise ValueError("api_key_env must be GOOGLE_API_KEY or GEMINI_API_KEY")


def _get_allowed_model_set() -> set[str]:
    from dual_pipeline.model_registry import ModelTier, get_tier_spec

    allowed_models: set[str] = set()
    for tier in ModelTier:
        spec = get_tier_spec(tier)
        if "default_model" in spec:
            allowed_models.add(spec["default_model"])
        for fb in spec.get("allowed_fallbacks", []):
            allowed_models.add(fb)
    return allowed_models


def _load_purpose_models(raw_purposes: Any, allowed_models: set[str]) -> dict[str, ModelPolicy]:
    purpose_models: dict[str, ModelPolicy] = {}
    if not isinstance(raw_purposes, Mapping):
        return purpose_models
    for p_name, p_val in raw_purposes.items():
        if isinstance(p_val, Mapping):
            pol = _load_model_policy(
                p_val,
                expected_profile=QueryAssuranceProfile.STANDARD,
                label=f"l2.purpose_models.{p_name}",
            )
            if pol.model not in allowed_models:
                raise ValueError(
                    f"l2.purpose_models.{p_name} must use governed model in {sorted(allowed_models)}, got model={pol.model}"
                )
            purpose_models[str(p_name)] = pol
    return purpose_models


def load_l2_config(path: Path = L2_POLICY_PATH) -> L2Config:
    resolved = Path(path).expanduser().resolve()
    raw = read_json(resolved)
    if not isinstance(raw, Mapping):
        raise ValueError("L2 policy configuration must be an object")
    required = {
        "schema_version",
        "mode",
        "provider_class",
        "provider_egress_allowed",
        "endpoint",
        "api_key_env",
        "local_env_file",
        "standard",
        "enhanced",
        "output",
        "reliability",
        "request_budget",
    }
    optional = {"purpose_models", "prompt_cache_enabled"}
    _strict_keys(raw, required=required, optional=optional, label="l2")
    if raw["schema_version"] != "4.0.0" or raw["mode"] != "conditional":
        raise ValueError("L2 configuration must use schema 4.0.0 in conditional mode")
    provider_class = str(raw["provider_class"])
    if not isinstance(raw["provider_egress_allowed"], bool):
        raise ValueError("provider_egress_allowed must be boolean")
    endpoint = str(raw["endpoint"])
    api_key_env = str(raw["api_key_env"])
    _validate_provider_settings(provider_class, endpoint, api_key_env)
    local_env_file = str(raw["local_env_file"])
    if Path(local_env_file).is_absolute() or Path(local_env_file).name != local_env_file:
        raise ValueError("local_env_file must be one repository-root filename")
    output = raw["output"]

    reliability = raw["reliability"]
    request_budget = raw["request_budget"]
    if (
        not isinstance(output, Mapping)
        or not isinstance(reliability, Mapping)
        or not isinstance(request_budget, Mapping)
    ):
        raise ValueError(
            "output, reliability, and request-budget policies must be objects"
        )
    _strict_keys(
        output,
        required={"structured_output_required", "maximum_output_tokens"},
        label="l2.output",
    )
    _strict_keys(
        reliability,
        required={"retry_count", "timeout_seconds"},
        label="l2.reliability",
    )
    _strict_keys(
        request_budget,
        required={"maximum_provider_request_chars"},
        label="l2.request_budget",
    )
    if output["structured_output_required"] is not True:
        raise ValueError("structured output must be required")
    retry_count = _positive_int(
        reliability["retry_count"], label="l2.reliability.retry_count", allow_zero=True
    )
    if retry_count != 0:
        raise ValueError("external L2 retry_count must be zero")
    allowed_models = _get_allowed_model_set()
    for profile_name in ("standard", "enhanced"):
        profile_value = raw[profile_name]
        m = profile_value.get("model")
        mf = profile_value.get("model_family")
        if m not in allowed_models or mf not in allowed_models:
            raise ValueError(
                f"l2.{profile_name} must use governed model in {sorted(allowed_models)}, got model={m}, family={mf}"
            )
    purpose_models = _load_purpose_models(raw.get("purpose_models"), allowed_models)

    return L2Config(
        schema_version="4.0.0",
        mode="conditional",
        provider_class=provider_class,
        provider_egress_allowed=bool(raw["provider_egress_allowed"]),
        endpoint=endpoint,
        api_key_env=api_key_env,
        local_env_file=local_env_file,
        standard=_load_model_policy(
            raw["standard"],
            expected_profile=QueryAssuranceProfile.STANDARD,
            label="l2.standard",
        ),
        enhanced=_load_model_policy(
            raw["enhanced"],
            expected_profile=QueryAssuranceProfile.ENHANCED,
            label="l2.enhanced",
        ),
        output=OutputPolicy(
            structured_output_required=True,
            maximum_output_tokens=_positive_int(
                output["maximum_output_tokens"], label="l2.output.maximum_output_tokens"
            ),
        ),
        reliability=ReliabilityPolicy(retry_count=0, timeout_seconds=_positive_int(
            reliability["timeout_seconds"], label="l2.reliability.timeout_seconds"
        )),
        request_budget=RequestBudgetPolicy(
            maximum_provider_request_chars=_positive_int(
                request_budget["maximum_provider_request_chars"],
                label="l2.request_budget.maximum_provider_request_chars",
            ),
        ),
        config_digest=digest(raw),
        purpose_models=purpose_models,
        prompt_cache_enabled=bool(raw.get("prompt_cache_enabled", True)),
    )


def query_model_policy_for_purpose(
    config: L2Config,
    purpose: CallPurpose | str,
    assurance_profile: str = "standard",
) -> ModelPolicy:
    """Resolve model policy with per-purpose routing awareness."""
    return config.model_for_purpose(purpose, assurance_profile)


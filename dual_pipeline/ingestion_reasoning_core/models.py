"""Dataclasses and protocols for ingestion models, policies, and providers."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol


@dataclass(frozen=True)
class IngestionModelPolicy:
    provider: str
    model: str
    model_tier: str
    reasoning_effort: str


@dataclass(frozen=True)
class IngestionReasoningPolicy:
    raw: Mapping[str, Any]
    path: Path
    config_digest: str

    @property
    def endpoint(self) -> str:
        return str(self.raw["endpoint"])

    @property
    def api_key_env(self) -> str:
        return str(self.raw["api_key_env"])

    @property
    def local_env_file(self) -> str:
        return str(self.raw["local_env_file"])

    @property
    def provider_egress_allowed(self) -> bool:
        return bool(self.raw["provider_egress_allowed"])

    @property
    def maximum_egress_classification(self) -> str:
        return str(self.raw["maximum_egress_classification"])

    @property
    def timeout_seconds(self) -> int:
        return int(self.raw["reliability"]["timeout_seconds"])

    @property
    def maximum_output_tokens(self) -> int:
        return int(self.raw["output"]["maximum_output_tokens"])

    @property
    def routing(self) -> Mapping[str, Any]:
        return self.raw["complexity_routing"]

    def model_for_tier(self, tier: str) -> IngestionModelPolicy:
        value = self.raw["models"][tier]
        return IngestionModelPolicy(
            provider=str(value["provider"]),
            model=str(value["model"]),
            model_tier=str(value["model_tier"]),
            reasoning_effort=str(value["reasoning_effort"]),
        )


@dataclass(frozen=True)
class IngestionProviderRequest:
    purpose: str
    packet: Mapping[str, Any] | None
    output_schema: Mapping[str, Any]
    original_response: Any = None
    validation_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class IngestionProviderResponse:
    output: Any
    response_digest: str
    requested_model: str
    delivered_model: str
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0


class IngestionReasoningProvider(Protocol):
    provider_id: str

    def generate(
        self, request: IngestionProviderRequest
    ) -> IngestionProviderResponse: ...


ProviderFactory = Callable[[IngestionModelPolicy], IngestionReasoningProvider]


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], label: str
) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ValueError(f"{label} has invalid keys; missing={missing}, extra={extra}")


def _validate_policy_endpoint(raw: dict[str, Any]) -> None:
    from urllib.parse import urlparse

    parsed_endpoint = urlparse(str(raw["endpoint"]))
    if (
        parsed_endpoint.scheme != "https"
        or parsed_endpoint.netloc != "api.openai.com"
        or parsed_endpoint.path != "/v1/responses"
    ):
        raise ValueError("ingestion reasoning endpoint must be OpenAI Responses API")
    for field in ("api_key_env", "local_env_file"):
        if not isinstance(raw[field], str) or not raw[field]:
            raise ValueError(f"{field} must be a nonempty string")


def _validate_policy_models(models: Any) -> None:
    if not isinstance(models, dict) or set(models) != {"normal", "high", "most_complex"}:
        raise ValueError("ingestion reasoning models must declare three exact tiers")
    from dual_pipeline.model_registry import ModelRegistry, ModelTier
    def_model, _ = ModelRegistry.resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
    binding = ModelRegistry.get_binding(ModelTier.FRONTIER_HIGH_REASONING)
    allowed_models = {def_model, *binding.allowed_fallbacks}
    required_efforts = {"normal": "low", "high": "xhigh", "most_complex": "max"}
    for tier, required_effort in required_efforts.items():
        value = models[tier]
        if not isinstance(value, dict):
            raise ValueError(f"model tier {tier} must be an object")
        _require_exact_keys(value, {"provider", "model", "model_tier", "reasoning_effort"}, f"model tier {tier}")
        if (
            value["provider"] != "openai"
            or value["model"] not in allowed_models
            or value["model_tier"] != tier
            or value["reasoning_effort"] != required_effort
        ):
            raise ValueError(f"model tier {tier} violates the governed tier mapping")
        from dual_pipeline.model_registry import BANNED_ROUTING_ALIASES
        if value["model"] in BANNED_ROUTING_ALIASES:
            raise ValueError(f"the routing alias {value['model']} is forbidden; pin an exact tier")


def _validate_policy_controls(raw: dict[str, Any]) -> None:
    limits = raw["call_limits"]
    if not isinstance(limits, dict):
        raise ValueError("call_limits must be an object")
    _require_exact_keys(limits, {"maximum_primary_calls_per_run", "maximum_repair_calls_per_run", "maximum_escalation_calls_per_run"}, "call_limits")
    if limits != {"maximum_primary_calls_per_run": 1, "maximum_repair_calls_per_run": 1, "maximum_escalation_calls_per_run": 0}:
        raise ValueError("ingestion reasoning call caps must be primary=1 repair=1 escalation=0")

    output = raw["output"]
    if not isinstance(output, dict):
        raise ValueError("output must be an object")
    _require_exact_keys(output, {"structured_output_required", "maximum_output_tokens"}, "output")
    if output["structured_output_required"] is not True:
        raise ValueError("strict structured output is required")
    if (
        isinstance(output["maximum_output_tokens"], bool)
        or not isinstance(output["maximum_output_tokens"], int)
        or not 256 <= output["maximum_output_tokens"] <= 65536
    ):
        raise ValueError("maximum_output_tokens is outside the governed range")

    reliability = raw["reliability"]
    if not isinstance(reliability, dict):
        raise ValueError("reliability must be an object")
    _require_exact_keys(reliability, {"retry_count", "timeout_seconds"}, "reliability")
    if reliability["retry_count"] != 0:
        raise ValueError("automatic provider retries are forbidden")
    if (
        isinstance(reliability["timeout_seconds"], bool)
        or not isinstance(reliability["timeout_seconds"], int)
        or not 1 <= reliability["timeout_seconds"] <= 300
    ):
        raise ValueError("timeout_seconds is outside the governed range")


def _validate_policy_routing(routing: Any) -> None:
    if not isinstance(routing, dict):
        raise ValueError("complexity_routing must be an object")
    routing_keys = {
        "low_max_candidates", "normal_max_candidates", "most_complex_min_candidates",
        "most_complex_prompt_chars", "maximum_provider_request_chars",
        "most_complex_min_ambiguous_identities", "high_candidate_types",
    }
    _require_exact_keys(routing, routing_keys, "complexity_routing")
    numeric_fields = (
        "low_max_candidates", "normal_max_candidates", "most_complex_min_candidates",
        "most_complex_prompt_chars", "maximum_provider_request_chars",
        "most_complex_min_ambiguous_identities",
    )
    if any(
        isinstance(routing[field], bool)
        or not isinstance(routing[field], int)
        or routing[field] < 1
        for field in numeric_fields
    ):
        raise ValueError("complexity thresholds must be positive integers")
    if not (
        routing["low_max_candidates"]
        < routing["normal_max_candidates"]
        < routing["most_complex_min_candidates"]
    ):
        raise ValueError("candidate complexity thresholds must be strictly increasing")
    high_types = routing["high_candidate_types"]
    if (
        not isinstance(high_types, list)
        or not high_types
        or len(set(high_types)) != len(high_types)
        or any(not isinstance(value, str) or not value for value in high_types)
    ):
        raise ValueError("high_candidate_types must be a unique nonempty string list")


def validate_policy_raw(raw: dict[str, Any]) -> None:
    from .constants import CLASSIFICATION_ORDER

    if not isinstance(raw, dict):
        raise ValueError("ingestion reasoning policy must be an object")
    expected_keys = {
        "schema_version", "default_mode", "provider_class", "provider_egress_allowed",
        "maximum_egress_classification", "endpoint", "api_key_env", "local_env_file",
        "models", "call_limits", "output", "reliability", "complexity_routing",
    }
    _require_exact_keys(raw, expected_keys, "ingestion reasoning policy")
    if raw["schema_version"] != "1.1.0":
        raise ValueError("unsupported ingestion reasoning policy version")
    if raw["default_mode"] not in {"shadow", "advisory"}:
        raise ValueError("ingestion reasoning default mode is invalid")
    if raw["provider_class"] != "openai":
        raise ValueError("ingestion reasoning provider class must be openai")
    if not isinstance(raw["provider_egress_allowed"], bool):
        raise ValueError("provider_egress_allowed must be boolean")
    if raw["maximum_egress_classification"] not in CLASSIFICATION_ORDER:
        raise ValueError("maximum egress classification is invalid")

    _validate_policy_endpoint(raw)
    _validate_policy_models(raw["models"])
    _validate_policy_controls(raw)
    _validate_policy_routing(raw["complexity_routing"])

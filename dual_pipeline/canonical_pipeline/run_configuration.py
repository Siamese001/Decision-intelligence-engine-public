"""Resolve separate extraction and judge settings without mutating shared providers."""
from __future__ import annotations

from copy import copy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunConfiguration:
    provider: Any
    extraction_effort: str | None
    judge_effort: str | None
    policy_profile: Any = None


def _effort(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty reasoning effort string")
    return value.strip()


def prepare_run_configuration(
    provider: Any, metadata: dict[str, Any], *, provider_type: str,
    default_effort: str | None, extraction_effort: str | None = None,
    judge_effort: str | None = None, legacy_judge_effort: str | None = None,
    judge_model: str | None = None,
    modality: Any = None,
) -> RunConfiguration:
    """Specific run arguments override metadata; the old run effort remains a judge alias.

    No model is replaced and no lower effort is selected automatically. The shared
    provider keeps its constructor settings for later or concurrent requests.
    """
    from dual_pipeline.canonical_pipeline.policy_profiles import (
        deep_isolate_provider,
        resolve_policy_profile,
    )

    requested_extraction = _effort(
        extraction_effort if extraction_effort is not None else metadata.get("extraction_effort"),
        "extraction_effort",
    )
    requested_judge = _effort(judge_effort if judge_effort is not None else
                             legacy_judge_effort if legacy_judge_effort is not None else
                             metadata.get("judge_effort"), "judge_effort")
    effective_extraction = requested_extraction or _effort(getattr(provider, "reasoning_effort", None),
                                                          "provider reasoning_effort") or default_effort
    effective_judge = requested_judge or default_effort
    if requested_judge is None and judge_model is None and metadata.get("judge_tier"):
        from dual_pipeline.model_registry import ModelTier, resolve_tier_model
        tier = {"fast": ModelTier.FAST_ROUTING, "standard": ModelTier.FRONTIER_REASONING}.get(
            str(metadata["judge_tier"]).strip().lower(), ModelTier.DIALECTICAL_JUDGE)
        _, effective_judge = resolve_tier_model(tier)
    scoped_provider = deep_isolate_provider(provider)
    if requested_extraction is not None:
        scoped_provider.reasoning_effort = requested_extraction
    metadata["extraction_effort"] = effective_extraction
    metadata["judge_effort"] = effective_judge

    profile = None
    target_mod = modality or metadata.get("modality") or metadata.get("canonical_modality")
    if target_mod:
        try:
            profile = resolve_policy_profile(target_mod, flags=metadata)
        except Exception:
            profile = None

    observed = {"status": "NOT_CALLED", "provider_type": provider_type,
                "requested_model": getattr(provider, "d03_model", None),
                "requested_effort": effective_extraction,
                "declared_revision": metadata.get("extraction_model_revision"),
                "effective_model": None, "effective_effort": None}
    if isinstance(observed["requested_model"], str) and observed["requested_model"].startswith("gemini"):
        from dual_pipeline.transcript_stages_core.external_synthesizer import _resolve_gemini_thinking_level
        observed.update(effective_model=observed["requested_model"], effective_effort=effective_extraction,
                        thinking_level=_resolve_gemini_thinking_level(observed["requested_model"], None, effective_extraction),
                        model_identity="configured_only")
    scoped_provider._document_execution_receipt = observed
    metadata["_extraction_execution"] = observed
    return RunConfiguration(scoped_provider, effective_extraction, effective_judge, policy_profile=profile)


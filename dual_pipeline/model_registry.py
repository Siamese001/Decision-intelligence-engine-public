#!/usr/bin/env python3
"""
dual_pipeline/model_registry.py

Declarative Model Capability Tier Registry and Parameter Immunity Engine.
Abstracts concrete model vendor strings into governed capability tiers
(FRONTIER_HIGH_REASONING, FRONTIER_REASONING, SURGICAL_HEALER, FAST_ROUTING, DIALECTICAL_JUDGE).

Enforces:
- AGENTS.md Invariant 7 (Model-Neutral Artifact Naming & LLM Neutrality)
- Parameter immunity: automatic stripping of forbidden parameters (temperature, top_p)
  for reasoning models (gpt-5.6-luna, gpt-5.6-sol, gpt-5.6-terra)
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from config.credentials import (
    GOOGLE_APPROVED_MODELS,
    MODEL_ALIASES,
    OPENAI_APPROVED_MODELS,
    CredentialManager,
    canonicalize_model_name,
    sanitize_model_call_kwargs,
)

from .parameters import get_float_parameter


class AcousticPinViolationError(ValueError):
    """Raised when an acoustic model pin or environment override violates SSOT."""
    pass

@dataclass(frozen=True)
class AcousticSubModelPin:
    model_id: str
    revision: str
    snapshot_sha256: str

@dataclass(frozen=True)
class AcousticPin:
    model_id: str
    revision: str
    snapshot_sha256: str
    sub_models: List[AcousticSubModelPin] = field(default_factory=list)


logger = logging.getLogger("dual_pipeline.model_registry")

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_JSON_PATH = WORKSPACE_ROOT / "config" / "runtime" / "model_registry.json"


class ModelTier(str, Enum):
    """Governed model capability tiers separating domain roles from ephemeral vendor IDs."""
    FRONTIER_HIGH_REASONING = "FRONTIER_HIGH_REASONING"
    FRONTIER_REASONING = "FRONTIER_REASONING"
    SURGICAL_HEALER = "SURGICAL_HEALER"
    FAST_ROUTING = "FAST_ROUTING"
    COPILOT_FAST_SNAP = "COPILOT_FAST_SNAP"
    QUERY_REASONING = "QUERY_REASONING"
    DIALECTICAL_JUDGE = "DIALECTICAL_JUDGE"
    EMBEDDING_MULTI_VECTOR = "EMBEDDING_MULTI_VECTOR"
    ACOUSTIC_MLX_WHISPER = "ACOUSTIC_MLX_WHISPER"
    ACOUSTIC_FASTER_WHISPER = "ACOUSTIC_FASTER_WHISPER"
    ACOUSTIC_WHISPERX = "ACOUSTIC_WHISPERX"
    ACOUSTIC_DIARIZATION = "ACOUSTIC_DIARIZATION"
    CLOUD_AUDIO_TRANSCRIBE = "CLOUD_AUDIO_TRANSCRIBE"
    ANTIGRAVITY_AGENT_FLASH = "ANTIGRAVITY_AGENT_FLASH"
    ANTIGRAVITY_AGENT_PRO = "ANTIGRAVITY_AGENT_PRO"
    LOCAL_JUDGE_INSTRUCT = "LOCAL_JUDGE_INSTRUCT"
    LOCAL_JUDGE_REASONING = "LOCAL_JUDGE_REASONING"


@dataclass(frozen=True)
class ModelBindingSpec:
    """Strongly typed model binding specification for an agent or engine component."""
    tier: str
    model_id: str
    provider: str
    reasoning_effort: Optional[str]
    timeout: float = 60.0
    strip_temperature: bool = True
    strip_top_p: bool = True
    allowed_fallbacks: List[str] = field(default_factory=list)
    model_revision: Optional[str] = None
    manifest_sha256: Optional[str] = None
    supports_batch: bool = False
    context_window_tokens: int = 128000


BANNED_REGISTRY_MODELS: Set[str] = {
    "gemini-2.5-flash",  # allow-retired-model
    "gpt-4.1-mini",
    "gpt-4.1",
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-4",
    "o1",
    "o1-mini",
    "o1-preview",
    "o3",
    "o3-mini",
}

BANNED_ROUTING_ALIASES: Set[str] = {
    "gpt-5.6",
}


FALLBACK_TIERS: Dict[str, Dict[str, Any]] = {
    "FRONTIER_HIGH_REASONING": {"default_model": "gpt-6.1-sol", "provider": "openai", "reasoning_effort": "max", "timeout": 300.0, "parameter_immunity": {"strip_temperature": True, "strip_top_p": True}, "allowed_fallbacks": ["gpt-6.1-sol", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"], "supports_batch": True},
    "FRONTIER_REASONING": {"default_model": "gpt-6.1-sol", "provider": "openai", "reasoning_effort": "high", "timeout": 300.0, "parameter_immunity": {"strip_temperature": True, "strip_top_p": True}, "allowed_fallbacks": ["gpt-6-sol", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-luna"], "supports_batch": True},
    "SURGICAL_HEALER": {"default_model": "gpt-6-luna", "provider": "openai", "reasoning_effort": "medium", "timeout": 300.0, "parameter_immunity": {"strip_temperature": True, "strip_top_p": True}, "allowed_fallbacks": ["gpt-5.6-luna", "gpt-6-sol", "gpt-6.1-sol", "gpt-5.6-sol", "gpt-5.6-terra"], "supports_batch": True},
    "FAST_ROUTING": {"default_model": "gpt-6-luna", "provider": "openai", "reasoning_effort": "low", "timeout": 15.0, "parameter_immunity": {"strip_temperature": True, "strip_top_p": True}, "allowed_fallbacks": ["gpt-6.1-sol", "gpt-5.6-luna", "gpt-5.6-terra"]},
    "COPILOT_FAST_SNAP": {"default_model": "gpt-6-luna", "provider": "openai", "reasoning_effort": "medium", "timeout": 7.0, "max_completion_tokens": 1500, "parameter_immunity": {"strip_temperature": True, "strip_top_p": True}, "allowed_fallbacks": ["gpt-5.6-luna"]},
    "QUERY_REASONING": {"default_model": "gpt-6.1-sol", "provider": "openai", "reasoning_effort": "high", "timeout": 60.0, "parameter_immunity": {"strip_temperature": True, "strip_top_p": True}, "allowed_fallbacks": ["gpt-6-sol", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-luna"], "supports_batch": True},
    "DIALECTICAL_JUDGE": {"default_model": "gpt-6-luna", "provider": "openai", "reasoning_effort": "max", "timeout": 300.0, "parameter_immunity": {"strip_temperature": True, "strip_top_p": True}, "allowed_fallbacks": ["gpt-6.1-sol", "gpt-5.6-sol", "gpt-5.6-terra"], "supports_batch": True},
    "EMBEDDING_MULTI_VECTOR": {"default_model": "bge-m3", "canonical_model_id": "BAAI/bge-m3", "provider": "local", "device": "mps", "fp16": True},
    "LOCAL_JUDGE_INSTRUCT": {"default_model": "qwen_2_5_14b_instruct", "canonical_model_id": "mlx-community/Qwen2.5-14B-Instruct-4bit", "provider": "local_mlx", "device": "mps", "allowed_fallbacks": []},
    "LOCAL_JUDGE_REASONING": {"default_model": "deepseek_r1_distill_qwen_14b", "canonical_model_id": "mlx-community/DeepSeek-R1-Distill-Qwen-14B-4bit", "provider": "local_mlx", "device": "mps", "allowed_fallbacks": []},
    "ACOUSTIC_MLX_WHISPER": {"default_model": "mlx-community/whisper-large-v3-mlx", "provider": "local_mlx", "device": "mps", "model_revision": "49e6aa286ad60c14352c404340ded53710378a11", "manifest_sha256": "ee8ad4cf5a7ee15641fa1a15cd8e41743844ecde04f56ab22e1608f28d2c5358", "allowed_fallbacks": []},
    "ACOUSTIC_FASTER_WHISPER": {"default_model": "large-v3", "provider": "local_ctranslate2", "device": "cpu", "model_revision": "ed34499fda4258169112467d025b6ec865ec01fe", "manifest_sha256": "a2f6479b183be604db0caae6e625a695e26b1df3f6f17e3be9687e14f8ab4f86", "allowed_fallbacks": []},
    "ACOUSTIC_WHISPERX": {"default_model": "large-v3", "provider": "local_whisperx", "device": "mps", "model_revision": "ed34499fda4258169112467d025b6ec865ec01fe", "manifest_sha256": "a2f6479b183be604db0caae6e625a695e26b1df3f6f17e3be9687e14f8ab4f86", "allowed_fallbacks": []},
    "ACOUSTIC_DIARIZATION": {
        "default_model": "pyannote/speaker-diarization-3.1", "provider": "pyannote", "device": "mps",
        "model_revision": "84fd25912480287da0247647c3d2b4853cb3ee5d",
        "manifest_sha256": "75128d8e7afe4a42f3db170645d5b2309e7d96d1de46aab737b07d835568a2e7",
        "allowed_fallbacks": ["pause_clustering"],
        "sub_models": [{"model_id": "pyannote/segmentation-3.0", "revision": "e66f3d3b9eb0873085418a7b813d3b369bf160bb", "snapshot_sha256": "7c13108e0909dab7a1b817a6527fa7db8d5df08d4ed537f08dd9799054d42028"}],
    },
    "CLOUD_AUDIO_TRANSCRIBE": {"default_model": "blocked_by_security_policy", "provider": "blocked", "timeout": 0.0, "allowed_fallbacks": [], "enabled": False, "block_reason": "FORBIDDEN_RAW_TRANSCRIPT_EXTERNAL_EGRESS"},
    "ANTIGRAVITY_AGENT_FLASH": {"default_model": "gemini-3.8-flash", "provider": "google", "reasoning_effort": "high", "timeout": 60.0, "parameter_immunity": {"strip_temperature": True, "strip_top_p": True}, "allowed_fallbacks": ["gemini-3.5-flash-lite"]},
    "ANTIGRAVITY_AGENT_PRO": {"default_model": "gemini-3.8-flash", "provider": "google", "timeout": 120.0, "parameter_immunity": {"strip_temperature": False, "strip_top_p": False}, "allowed_fallbacks": ["gemini-3.8-flash", "gemini-3.5-flash-lite"]},
}

_REGISTRY_LOCK = threading.Lock()
_CACHED_TIERS: Optional[Dict[str, Any]] = None
_CACHED_APPROVED_MODELS: Optional[Dict[str, Set[str]]] = None


def compute_policy_digest() -> str:
    """Computes deterministic RFC-8259 SHA-256 digest of config/runtime/model_registry.json."""
    if REGISTRY_JSON_PATH.is_file():
        try:
            data = json.loads(REGISTRY_JSON_PATH.read_text(encoding="utf-8"))
            canonical = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
            return hashlib.sha256(canonical).hexdigest()
        except Exception:
            pass
    return "UNAVAILABLE_POLICY_DIGEST"


def _load_approved_models_from_disk() -> Dict[str, Set[str]]:
    approved_map: Dict[str, Set[str]] = {"openai": set(), "google": set(), "local": set()}
    if REGISTRY_JSON_PATH.is_file():
        try:
            raw = json.loads(REGISTRY_JSON_PATH.read_text(encoding="utf-8"))
            raw_appr = raw.get("approved_models", {})
            for p, m_list in raw_appr.items():
                approved_map[p.lower()] = {str(m).strip() for m in m_list if str(m).strip()}
        except Exception as exc:
            logger.debug("Failed reading approved_models from model_registry.json: %s", exc)
    if not approved_map["openai"]:
        approved_map["openai"] = set(OPENAI_APPROVED_MODELS)
    if not approved_map["google"]:
        approved_map["google"] = set(GOOGLE_APPROVED_MODELS)
    return approved_map


def get_approved_models(provider: Optional[str] = None) -> Set[str]:
    """Returns approved model identifiers for a provider (or all providers if None)."""
    global _CACHED_APPROVED_MODELS
    with _REGISTRY_LOCK:
        if _CACHED_APPROVED_MODELS is None:
            _CACHED_APPROVED_MODELS = _load_approved_models_from_disk()

        if provider is None:
            all_models: Set[str] = set()
            for s in _CACHED_APPROVED_MODELS.values():
                all_models.update(s)
            return all_models

        p_norm = provider.strip().lower()
        if p_norm.startswith("local") or p_norm in ("mlx", "pyannote"):
            p_norm = "local"
        return set(_CACHED_APPROVED_MODELS.get(p_norm, set()))


def is_model_approved(model: str, provider: Optional[str] = None) -> bool:
    """Returns True if the canonical model name is in the approved model set."""
    if not model or not str(model).strip():
        return False
    m_clean = canonicalize_model_name(str(model).strip())
    if m_clean in BANNED_REGISTRY_MODELS:
        return False
    approved = get_approved_models(provider)
    return m_clean in approved


def validate_dispatch_model(model: str, provider: str, tier: Optional[str] = None) -> str:
    """
    Validates model against provider's approved policy before transport dispatch.
    Fails closed with ValueError on unapproved or provider-mismatched models.
    """
    if not model or not str(model).strip():
        raise ValueError("Model identifier cannot be empty for dispatch")
    m_clean = canonicalize_model_name(str(model).strip())
    p_norm = provider.strip().lower()
    if p_norm.startswith("local") or p_norm in ("mlx", "pyannote"):
        p_norm = "local"

    if m_clean in BANNED_REGISTRY_MODELS:
        raise ValueError(
            f"Model '{m_clean}' is strictly banned by policy governance and cannot be dispatched."
        )

    approved = get_approved_models(p_norm)
    if m_clean not in approved:
        tier_ctx = f" for tier '{tier}'" if tier else ""
        raise ValueError(
            f"Model '{m_clean}' is not approved for provider '{provider}'{tier_ctx}. "
            f"Approved {provider} models: {sorted(approved)}"
        )
    return m_clean


def reload_registry() -> Dict[str, Any]:
    """Clears cache and forces reload of model registry configuration."""
    global _CACHED_TIERS, _CACHED_APPROVED_MODELS
    with _REGISTRY_LOCK:
        _CACHED_TIERS = None
        _CACHED_APPROVED_MODELS = None
    return _load_registry()


def _load_registry() -> Dict[str, Any]:
    """Loads declarative model tiers from canonical JSON config with memoization."""
    global _CACHED_TIERS
    with _REGISTRY_LOCK:
        if _CACHED_TIERS is not None:
            return dict(_CACHED_TIERS)

        # Canonical standard library JSON source of truth
        if REGISTRY_JSON_PATH.is_file():
            try:
                raw = json.loads(REGISTRY_JSON_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and "model_tiers" in raw:
                    _CACHED_TIERS = raw["model_tiers"]
                    return dict(_CACHED_TIERS)
            except Exception as exc:
                logger.debug("Failed reading model_registry.json: %s", exc)
        # Fallback to compiled defaults if JSON is unparseable or absent
        _CACHED_TIERS = FALLBACK_TIERS
        return dict(_CACHED_TIERS)


def get_tier_spec(tier: ModelTier | str) -> Dict[str, Any]:
    """Returns the configuration dictionary for a given model tier."""
    tier_key = tier.value if isinstance(tier, ModelTier) else str(tier)
    tiers = _load_registry()
    if tier_key in tiers:
        spec = tiers[tier_key]
    elif tier_key in FALLBACK_TIERS:
        spec = FALLBACK_TIERS[tier_key]
    else:
        raise KeyError(f"Unknown model tier '{tier_key}'. Available: {list(tiers.keys())}")

    if tier_key == ModelTier.ACOUSTIC_DIARIZATION.value:
        from config.credentials import CredentialManager, ensure_ssot_env_loaded
        ensure_ssot_env_loaded()
        if not CredentialManager.get_credential("HF_TOKEN"):
            logger.warning(
                "[ACOUSTIC_DIARIZATION] HF_TOKEN is not configured in process environment or env_di. "
                "Gated model pyannote/speaker-diarization-3.1 will fail and fall back to pause_clustering."
            )
    return spec


def get_binding(tier: ModelTier | str) -> ModelBindingSpec:
    """Returns a strongly typed ModelBindingSpec for the given tier."""
    tier_key = tier.value if isinstance(tier, ModelTier) else str(tier)
    spec = get_tier_spec(tier_key)
    immunity = spec.get("parameter_immunity", {})
    return ModelBindingSpec(
        tier=tier_key,
        model_id=spec.get("default_model", "gpt-6.1-sol"),
        provider=spec.get("provider", "openai"),
        reasoning_effort=spec.get("reasoning_effort"),
        timeout=float(spec.get("timeout", get_float_parameter("timeouts.llm_sync_seconds", 60.0))),
        strip_temperature=immunity.get("strip_temperature", True),
        strip_top_p=immunity.get("strip_top_p", True),
        allowed_fallbacks=list(spec.get("allowed_fallbacks", [])),
        model_revision=spec.get("model_revision"),
        manifest_sha256=spec.get("manifest_sha256"),
        supports_batch=spec.get("supports_batch", False),
    )


def is_batch_supported_for_model(model_name: str) -> bool:
    """Returns True if the given model name is part of any tier that supports batching."""
    m_clean = canonicalize_model_name(model_name)
    tiers = _load_registry()
    for spec in tiers.values():
        if spec.get("supports_batch", False):
            if canonicalize_model_name(spec.get("default_model", "")) == m_clean:
                return True
            for fallback in spec.get("allowed_fallbacks", []):
                if canonicalize_model_name(fallback) == m_clean:
                    return True
    return False


def get_acoustic_pinning(tier: ModelTier | str) -> Dict[str, Optional[str]]:
    """Return pinned model_revision and manifest_sha256 for acoustic model tiers."""
    binding = get_binding(tier)
    return {
        "model_id": binding.model_id,
        "model_revision": binding.model_revision,
        "manifest_sha256": binding.manifest_sha256,
    }


def _validate_model_candidate(cand: str, binding: ModelBindingSpec, tier_key: str, label: str = "") -> str:
    prefix = f"{label} model " if label else "Model "
    cand_clean = canonicalize_model_name(cand)
    if cand_clean in BANNED_REGISTRY_MODELS:
        logger.warning("%s'%s' is strictly banned. Falling back to default '%s'", prefix, cand_clean, binding.model_id)
        return binding.model_id

    # Provider-isolated validation: NEVER check cross-provider union
    if binding.provider == "openai":
        approved_openai = get_approved_models("openai")
        if cand_clean in approved_openai:
            return cand_clean
        logger.warning(
            "%s'%s' is not an approved OpenAI model (approved: %s). Falling back to default '%s'",
            prefix, cand_clean, sorted(approved_openai), binding.model_id
        )
        return binding.model_id
    elif binding.provider == "google":
        approved_google = get_approved_models("google")
        if cand_clean in approved_google:
            return cand_clean
        logger.warning(
            "%s'%s' is not an approved Google model (approved: %s). Falling back to default '%s'",
            prefix, cand_clean, sorted(approved_google), binding.model_id
        )
        return binding.model_id
    elif binding.provider.startswith("local") or binding.provider in ("pyannote", "mlx"):
        approved_local = get_approved_models("local")
        if cand_clean in approved_local or cand_clean in binding.allowed_fallbacks:
            return cand_clean
        return binding.model_id

    logger.warning("Configured %s'%s' for tier %s not in governed allowlist. Using default '%s'", prefix.lower(), cand_clean, tier_key, binding.model_id)
    return binding.model_id


_TIER_ENV_KEYS: Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]] = {
    ModelTier.FAST_ROUTING.value: (("FAST_ROUTING_MODEL",), ("FAST_ROUTING_REASONING_EFFORT",)),
    ModelTier.COPILOT_FAST_SNAP.value: (
        ("COPILOT_MODEL", "COPILOT_FAST_SNAP_MODEL"),
        ("COPILOT_REASONING_EFFORT", "COPILOT_FAST_SNAP_REASONING_EFFORT"),
    ),
    ModelTier.QUERY_REASONING.value: (
        ("QUERY_REASONING_MODEL", "QUERY_MODEL", "OPENAI_MODEL"),
        ("QUERY_REASONING_REASONING_EFFORT", "QUERY_REASONING_EFFORT", "OPENAI_REASONING_EFFORT"),
    ),
    ModelTier.FRONTIER_HIGH_REASONING.value: (
        ("FRONTIER_HIGH_REASONING_MODEL", "OPENAI_MODEL"),
        ("FRONTIER_HIGH_REASONING_REASONING_EFFORT", "OPENAI_REASONING_EFFORT"),
    ),
    ModelTier.FRONTIER_REASONING.value: (
        ("FRONTIER_REASONING_MODEL", "OPENAI_MODEL"),
        ("FRONTIER_REASONING_EFFORT", "OPENAI_REASONING_EFFORT"),
    ),
    ModelTier.DIALECTICAL_JUDGE.value: (
        ("DIALECTICAL_JUDGE_MODEL", "OPENAI_MODEL"),
        ("DIALECTICAL_JUDGE_REASONING_EFFORT", "OPENAI_REASONING_EFFORT"),
    ),
    ModelTier.SURGICAL_HEALER.value: (
        ("SURGICAL_HEALER_MODEL", "OPENAI_MODEL"),
        ("SURGICAL_HEALER_REASONING_EFFORT", "OPENAI_REASONING_EFFORT"),
    ),
    ModelTier.ANTIGRAVITY_AGENT_FLASH.value: (
        ("ANTIGRAVITY_AGENT_FLASH_MODEL", "GEMINI_MODEL", "GOOGLE_MODEL"),
        ("ANTIGRAVITY_AGENT_FLASH_REASONING_EFFORT", "GEMINI_REASONING_EFFORT"),
    ),
    ModelTier.ANTIGRAVITY_AGENT_PRO.value: (
        ("ANTIGRAVITY_AGENT_PRO_MODEL", "GEMINI_MODEL", "GOOGLE_MODEL"),
        ("ANTIGRAVITY_AGENT_PRO_REASONING_EFFORT", "GEMINI_REASONING_EFFORT"),
    ),
}


def _first_credential(keys: Tuple[str, ...]) -> Optional[str]:
    for key in keys:
        val = CredentialManager.get_credential(key)
        if val:
            return val
    return None


def resolve_tier_model(tier: ModelTier | str) -> Tuple[str, Optional[str]]:
    """Resolves concrete model and reasoning effort based on ModelTier."""
    binding = get_binding(tier)
    tier_key = tier.value if isinstance(tier, ModelTier) else str(tier)

    from config.credentials import ensure_ssot_env_loaded
    ensure_ssot_env_loaded()

    default_keys = ((f"{tier_key.upper()}_MODEL",), (f"{tier_key.upper()}_REASONING_EFFORT",))
    model_keys, effort_keys = _TIER_ENV_KEYS.get(tier_key, default_keys)
    env_model = _first_credential(model_keys)
    env_effort = _first_credential(effort_keys)

    if env_model and env_model.strip():
        model_id = _validate_model_candidate(canonicalize_model_name(env_model.strip()), binding, tier_key)
    else:
        model_id = binding.model_id

    effort = env_effort.strip() if env_effort and env_effort.strip() else binding.reasoning_effort
    return model_id, effort



def _validate_acoustic_pin(pin_name: str, tier_data: Dict[str, Any]) -> AcousticPin:
    model_id = tier_data.get("default_model")
    revision = tier_data.get("model_revision")
    sha256 = tier_data.get("manifest_sha256")

    if not model_id or not revision or not sha256:
        raise AcousticPinViolationError(f"Missing pin fields for {pin_name}")

    if sha256 == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855":
        raise AcousticPinViolationError(f"Rejected empty-input SHA256 for {pin_name}")

    sub_models = []
    for sub in tier_data.get("sub_models", []):
        sub_sha256 = sub.get("snapshot_sha256")
        if sub_sha256 == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855":
            raise AcousticPinViolationError(f"Rejected empty-input SHA256 for sub-model {sub.get('model_id')}")
        sub_models.append(AcousticSubModelPin(
            model_id=sub.get("model_id"),
            revision=sub.get("revision"),
            snapshot_sha256=sub_sha256
        ))

    return AcousticPin(model_id=model_id, revision=revision, snapshot_sha256=sha256, sub_models=sub_models)

def resolve_acoustic_asr() -> AcousticPin:
    CredentialManager.ensure_ssot_env_loaded()
    env_override = CredentialManager.get_credential("ACOUSTIC_MLX_WHISPER_MODEL")
    registry = _load_registry()
    tier_data = registry.get(ModelTier.ACOUSTIC_MLX_WHISPER.value)
    if not tier_data:
        raise AcousticPinViolationError("ACOUSTIC_MLX_WHISPER tier missing from registry")

    pin = _validate_acoustic_pin(ModelTier.ACOUSTIC_MLX_WHISPER.value, tier_data)

    # Reject non-SSOT env overrides
    if env_override and env_override.strip() != pin.model_id:
        raise AcousticPinViolationError(f"Env override {env_override} does not match SSOT pin {pin.model_id}")
    return pin

def resolve_acoustic_diarizer() -> AcousticPin:
    CredentialManager.ensure_ssot_env_loaded()
    env_override = CredentialManager.get_credential("ACOUSTIC_DIARIZATION_MODEL")
    registry = _load_registry()
    tier_data = registry.get(ModelTier.ACOUSTIC_DIARIZATION.value)
    if not tier_data:
        raise AcousticPinViolationError("ACOUSTIC_DIARIZATION tier missing from registry")

    pin = _validate_acoustic_pin(ModelTier.ACOUSTIC_DIARIZATION.value, tier_data)

    # Reject non-SSOT env overrides
    if env_override and env_override.strip() != pin.model_id:
        raise AcousticPinViolationError(f"Env override {env_override} does not match SSOT pin {pin.model_id}")
    return pin

def resolve_query_tier_model(tier: ModelTier | str = ModelTier.QUERY_REASONING) -> Tuple[str, Optional[str]]:
    """Resolves model for deliberative knowledge graph and decision query reasoning."""
    return resolve_tier_model(tier)


def resolve_copilot_tier_model(tier: ModelTier | str = ModelTier.COPILOT_FAST_SNAP) -> Tuple[str, Optional[str]]:
    """Resolves model for copilot real-time tasks checking COPILOT_MODEL from env_di first."""
    binding = get_binding(tier)
    tier_key = tier.value if isinstance(tier, ModelTier) else str(tier)

    CredentialManager.ensure_ssot_env_loaded()

    env_model = (
        CredentialManager.get_credential("COPILOT_MODEL")
        or CredentialManager.get_credential("COPILOT_FAST_SNAP_MODEL")
        or CredentialManager.get_credential(f"{tier_key.upper()}_MODEL")
    )
    env_effort = (
        CredentialManager.get_credential("COPILOT_REASONING_EFFORT")
        or CredentialManager.get_credential("COPILOT_FAST_SNAP_REASONING_EFFORT")
        or CredentialManager.get_credential(f"{tier_key.upper()}_REASONING_EFFORT")
    )
    if not env_model and tier_key in (ModelTier.FRONTIER_REASONING.value, ModelTier.FRONTIER_HIGH_REASONING.value):
        env_model = CredentialManager.get_credential("OPENAI_MODEL")
        env_effort = env_effort or CredentialManager.get_credential("OPENAI_REASONING_EFFORT")

    if env_model and env_model.strip():
        model_id = _validate_model_candidate(canonicalize_model_name(env_model.strip()), binding, tier_key, label="Copilot")
    else:
        model_id = binding.model_id

    effort = env_effort.strip() if env_effort and env_effort.strip() else binding.reasoning_effort
    return model_id, effort


def resolve_tier_model_name(tier: ModelTier | str) -> str:
    """Resolves a capability tier into its default model identifier string."""
    model, _ = resolve_tier_model(tier)
    return model


def resolve_embedding_model_id() -> str:
    """Resolves the canonical HuggingFace repository identifier for the embedding tier."""
    spec = get_tier_spec(ModelTier.EMBEDDING_MULTI_VECTOR)
    return str(spec.get("canonical_model_id") or spec.get("default_model", "BAAI/bge-m3"))


def prepare_call_kwargs(
    tier: ModelTier | str,
    override_kwargs: Optional[Dict[str, Any]] = None,
    dynamic_env: bool = True,
) -> Dict[str, Any]:
    """Prepares sanitized kwargs for model dispatch conforming to parameter immunity."""
    binding = get_binding(tier)
    tier_key = tier.value if isinstance(tier, ModelTier) else str(tier)
    kwargs = dict(override_kwargs or {})

    if dynamic_env:
        model_id, effort = resolve_tier_model(tier)
        target_m = canonicalize_model_name(kwargs.get("model", model_id))
        effective_effort = effort or binding.reasoning_effort
        if effective_effort and "reasoning_effort" not in kwargs:
            kwargs["reasoning_effort"] = effective_effort
    else:
        target_m = canonicalize_model_name(kwargs.get("model", binding.model_id))
        if binding.reasoning_effort and "reasoning_effort" not in kwargs:
            kwargs["reasoning_effort"] = binding.reasoning_effort

    kwargs["model"] = _validate_model_candidate(target_m, binding, tier_key)
    return sanitize_model_call_kwargs(kwargs["model"], kwargs)


def prepare_copilot_call_kwargs(
    tier: ModelTier | str = ModelTier.COPILOT_FAST_SNAP,
    override_kwargs: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Prepares sanitized kwargs for copilot real-time tasks using dynamic env_di model resolution."""
    model_id, effort = resolve_copilot_tier_model(tier)
    binding = get_binding(tier)
    tier_key = tier.value if isinstance(tier, ModelTier) else str(tier)
    kwargs = dict(override_kwargs or {})
    target_m = canonicalize_model_name(kwargs.get("model", model_id))
    kwargs["model"] = _validate_model_candidate(target_m, binding, tier_key, label="Copilot")

    from config.credentials import REASONING_MODEL_PREFIXES
    m_lower = str(kwargs["model"]).lower()
    is_reasoning = any(m_lower.startswith(pfx) for pfx in REASONING_MODEL_PREFIXES)
    if is_reasoning:
        effective_effort = effort or binding.reasoning_effort
        if effective_effort and "reasoning_effort" not in kwargs:
            kwargs["reasoning_effort"] = effective_effort
    return sanitize_model_call_kwargs(kwargs["model"], kwargs)


class ModelRegistry:
    """Convenience class facade for model tier registry lookups."""
    get_tier_spec = staticmethod(get_tier_spec)
    get_binding = staticmethod(get_binding)
    resolve_tier_model = staticmethod(resolve_tier_model)
    resolve_tier_model_name = staticmethod(resolve_tier_model_name)
    resolve_embedding_model_id = staticmethod(resolve_embedding_model_id)
    resolve_copilot_tier_model = staticmethod(resolve_copilot_tier_model)
    resolve_acoustic_asr = staticmethod(resolve_acoustic_asr)
    resolve_acoustic_diarizer = staticmethod(resolve_acoustic_diarizer)
    resolve_query_tier_model = staticmethod(resolve_query_tier_model)
    prepare_call_kwargs = staticmethod(prepare_call_kwargs)
    prepare_copilot_call_kwargs = staticmethod(prepare_copilot_call_kwargs)
    canonicalize_model_name = staticmethod(canonicalize_model_name)
    is_batch_supported_for_model = staticmethod(is_batch_supported_for_model)
    BANNED_REGISTRY_MODELS = BANNED_REGISTRY_MODELS


__all__ = [
    "ModelRegistry", "ModelTier", "ModelBindingSpec", "MODEL_ALIASES", "OPENAI_APPROVED_MODELS",
    "GOOGLE_APPROVED_MODELS", "BANNED_REGISTRY_MODELS", "canonicalize_model_name", "get_tier_spec",
    "get_binding", "resolve_tier_model", "resolve_tier_model_name", "resolve_embedding_model_id",
    "resolve_copilot_tier_model",
    "resolve_acoustic_asr", "resolve_acoustic_diarizer", "AcousticPin", "AcousticPinViolationError",
    "resolve_query_tier_model", "prepare_call_kwargs", "prepare_copilot_call_kwargs",
    "is_batch_supported_for_model", "reload_registry", "FALLBACK_TIERS",
]

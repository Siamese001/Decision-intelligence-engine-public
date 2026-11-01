"""Connection strategy resolution and authentication management for Antigravity SDK.

Resolves backend connection targets across:
- Gemini Developer API (Direct API key)
- Vertex AI Standard Mode (Application Default Credentials / gcloud ADC)
- Vertex AI Express Mode (Vertex endpoint with dedicated API key)
- LiteRT Local Mode (Offline on-device execution, e.g. Gemma 4 26B)
- Local OpenAI-compatible Mode (Local sidecars like Ollama / LM Studio)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional

from dual_pipeline.model_registry import ModelTier, resolve_tier_model_name


class ConnectionStrategy(str, Enum):
    """Supported connection strategies for Antigravity agents."""

    GEMINI_DEVELOPER_API = "gemini_developer_api"
    VERTEX_AI_STANDARD = "vertex_ai_standard"
    VERTEX_AI_EXPRESS = "vertex_ai_express"
    LITERT_LOCAL = "litert_local"
    OPENAI_COMPATIBLE = "openai_compatible"


@dataclass(frozen=True)
class ConnectionResolutionResult:
    """Immutable resolved connection configuration without raw credential leaks."""

    strategy: ConnectionStrategy
    is_cloud: bool
    is_offline: bool
    vertex_enabled: bool
    project_id: Optional[str] = None
    location: Optional[str] = None
    model_name: Optional[str] = None
    has_api_key: bool = False
    details: str = ""

    def summary(self) -> str:
        """Sanitized string summary that never exposes credential secrets."""
        mode = "Cloud" if self.is_cloud else "Offline/Local"
        return (
            f"Strategy={self.strategy.value} | Mode={mode} | "
            f"Vertex={self.vertex_enabled} | Project={self.project_id or 'none'} | "
            f"Location={self.location or 'none'} | HasApiKey={self.has_api_key}"
        )


def _resolve_litert(
    env_map: Mapping[str, str], model_override: Optional[str]
) -> Optional[ConnectionResolutionResult]:
    """Resolve LiteRT local model if configured and present on disk."""
    litert_path = env_map.get("LITERT_MODEL_PATH")
    if litert_path and os.path.exists(litert_path):
        return ConnectionResolutionResult(
            strategy=ConnectionStrategy.LITERT_LOCAL,
            is_cloud=False,
            is_offline=True,
            vertex_enabled=False,
            model_name=model_override or os.path.basename(litert_path),
            has_api_key=False,
            details=f"LiteRT model resolved from {litert_path}",
        )
    return None


def _resolve_vertex(
    env_map: Mapping[str, str], model_override: Optional[str]
) -> Optional[ConnectionResolutionResult]:
    """Resolve Vertex AI Standard (ADC) or Express (API Key) mode."""
    vertex_project = env_map.get("VERTEX_PROJECT") or env_map.get("GOOGLE_CLOUD_PROJECT")
    vertex_location = env_map.get("VERTEX_LOCATION", "us-central1")
    use_vertex = env_map.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("true", "1")
    vertex_use_adc = env_map.get("VERTEX_USE_ADC", "").lower() == "true"
    vertex_api_key = env_map.get("VERTEX_API_KEY")

    if use_vertex or (vertex_project and vertex_use_adc):
        return ConnectionResolutionResult(
            strategy=ConnectionStrategy.VERTEX_AI_STANDARD,
            is_cloud=True,
            is_offline=False,
            vertex_enabled=True,
            project_id=vertex_project,
            location=vertex_location,
            model_name=model_override or resolve_tier_model_name(ModelTier.ANTIGRAVITY_AGENT_PRO),
            has_api_key=bool(vertex_api_key),
            details="Vertex AI Standard Mode with ADC",
        )

    if vertex_api_key and vertex_project:
        return ConnectionResolutionResult(
            strategy=ConnectionStrategy.VERTEX_AI_EXPRESS,
            is_cloud=True,
            is_offline=False,
            vertex_enabled=True,
            project_id=vertex_project,
            location=vertex_location,
            model_name=model_override or resolve_tier_model_name(ModelTier.ANTIGRAVITY_AGENT_PRO),
            has_api_key=True,
            details="Vertex AI Express Mode with API Key",
        )
    return None


def resolve_connection_strategy(
    env: Optional[Mapping[str, str]] = None,
    requested_strategy: Optional[ConnectionStrategy] = None,
    model_override: Optional[str] = None,
) -> ConnectionResolutionResult:
    """Resolve the appropriate connection strategy using provided environment or os.environ.

    Enforces credential isolation: credentials are never echoed into logs or returned
    raw in the metadata structure.
    """
    env_map = env if env is not None else os.environ

    # 1. Explicit request takes priority if provided
    if requested_strategy is not None:
        return _build_explicit_resolution(requested_strategy, env_map, model_override)

    # 2. Check for explicit environment selector
    env_strategy = env_map.get("ANTIGRAVITY_CONNECTION_STRATEGY")
    if env_strategy:
        try:
            matched = ConnectionStrategy(env_strategy.strip().lower())
            return _build_explicit_resolution(matched, env_map, model_override)
        except ValueError:
            pass

    # 3. Automatic resolution heuristics
    litert_res = _resolve_litert(env_map, model_override)
    if litert_res is not None:
        return litert_res

    vertex_res = _resolve_vertex(env_map, model_override)
    if vertex_res is not None:
        return vertex_res

    # Priority D: Gemini Developer API (Default cloud mode)
    gemini_key = env_map.get("GEMINI_API_KEY")
    has_gemini_key = bool(gemini_key and gemini_key.strip())
    return ConnectionResolutionResult(
        strategy=ConnectionStrategy.GEMINI_DEVELOPER_API,
        is_cloud=True,
        is_offline=False,
        vertex_enabled=False,
        model_name=model_override or resolve_tier_model_name(ModelTier.ANTIGRAVITY_AGENT_FLASH),
        has_api_key=has_gemini_key,
        details="Gemini Developer API (Default)",
    )


def _build_explicit_resolution(
    strategy: ConnectionStrategy,
    env_map: Mapping[str, str],
    model_override: Optional[str] = None,
) -> ConnectionResolutionResult:
    """Build resolution result for an explicitly requested strategy."""
    if strategy == ConnectionStrategy.LITERT_LOCAL:
        model_path = env_map.get("LITERT_MODEL_PATH", "")
        return ConnectionResolutionResult(
            strategy=strategy,
            is_cloud=False,
            is_offline=True,
            vertex_enabled=False,
            model_name=model_override or (os.path.basename(model_path) if model_path else "gemma-4-26b"),
            has_api_key=False,
            details="Explicit LiteRT Local configuration",
        )

    if strategy in (ConnectionStrategy.VERTEX_AI_STANDARD, ConnectionStrategy.VERTEX_AI_EXPRESS):
        project = env_map.get("VERTEX_PROJECT") or env_map.get("GOOGLE_CLOUD_PROJECT")
        location = env_map.get("VERTEX_LOCATION", "us-central1")
        has_key = bool(env_map.get("VERTEX_API_KEY"))
        return ConnectionResolutionResult(
            strategy=strategy,
            is_cloud=True,
            is_offline=False,
            vertex_enabled=True,
            project_id=project,
            location=location,
            model_name=model_override or resolve_tier_model_name(ModelTier.ANTIGRAVITY_AGENT_PRO),
            has_api_key=has_key,
            details=f"Explicit {strategy.value}",
        )

    if strategy == ConnectionStrategy.OPENAI_COMPATIBLE:
        return ConnectionResolutionResult(
            strategy=strategy,
            is_cloud=False,
            is_offline=False,
            vertex_enabled=False,
            model_name=model_override or env_map.get("OPENAI_MODEL", "local-model"),
            has_api_key=bool(env_map.get("OPENAI_API_KEY")),
            details="Explicit OpenAI-compatible local server",
        )

    # Default to GEMINI_DEVELOPER_API
    has_key = bool(env_map.get("GEMINI_API_KEY"))
    return ConnectionResolutionResult(
        strategy=ConnectionStrategy.GEMINI_DEVELOPER_API,
        is_cloud=True,
        is_offline=False,
        vertex_enabled=False,
        model_name=model_override or resolve_tier_model_name(ModelTier.ANTIGRAVITY_AGENT_FLASH),
        has_api_key=has_key,
        details="Explicit Gemini Developer API",
    )

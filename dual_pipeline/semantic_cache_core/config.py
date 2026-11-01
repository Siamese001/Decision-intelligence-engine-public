"""Configuration and policy loading for semantic cache."""
from __future__ import annotations

import json
import math
from importlib.resources import files
from pathlib import Path
from typing import Any

from ..core import (
    assert_safe_mutable_path,
    digest,
    is_link_or_reparse,
    read_json,
    utc_now,
    write_json,
)
from .constants import CONFIG_SCHEMA_VERSION, INITIAL_RUNTIME_MODE, MODES
from .models import SemanticCachePolicy


def _policy_value() -> dict[str, Any]:
    resource = files("dual_pipeline.resources").joinpath("semantic_cache_policy.json")
    value = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("semantic cache policy must be an object")
    return value


def load_semantic_cache_policy() -> SemanticCachePolicy:
    value = _policy_value()
    expected = {
        "policy_id",
        "policy_version",
        "similarity_threshold",
        "max_candidates",
        "ttl_seconds",
        "allow_external_l2_replay",
        "cacheable_outcomes",
    }
    threshold = value.get("similarity_threshold")
    candidates = value.get("max_candidates")
    ttl = value.get("ttl_seconds")
    outcomes = value.get("cacheable_outcomes")
    if (
        set(value) != expected
        or not isinstance(value.get("policy_id"), str)
        or not value["policy_id"]
        or not isinstance(value.get("policy_version"), str)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(float(threshold))
        or not 0.9 <= float(threshold) <= 1.0
        or not isinstance(candidates, int)
        or not 1 <= candidates <= 4096
        or not isinstance(ttl, int)
        or not 60 <= ttl <= 31_536_000
        or not isinstance(value.get("allow_external_l2_replay"), bool)
        or not isinstance(outcomes, list)
        or not outcomes
        or any(not isinstance(item, str) or not item for item in outcomes)
    ):
        raise ValueError("semantic cache policy contains invalid values")
    return SemanticCachePolicy(
        policy_id=value["policy_id"],
        policy_version=value["policy_version"],
        similarity_threshold=float(threshold),
        max_candidates=candidates,
        ttl_seconds=ttl,
        allow_external_l2_replay=value["allow_external_l2_replay"],
        cacheable_outcomes=tuple(sorted(set(outcomes))),
        policy_digest=digest(value),
    )


def semantic_cache_config_path(runtime_root: Path) -> Path:
    return Path(runtime_root).expanduser().absolute().resolve() / "config" / "semantic_cache.json"


def _runtime_config(policy: SemanticCachePolicy, mode: str) -> dict[str, Any]:
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "mode": mode,
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "policy_digest": policy.policy_digest,
        "updated_at": utc_now(),
    }


def configure_semantic_cache(runtime_root: Path, mode: str) -> dict[str, Any]:
    """Persist the one authoritative operational mode for a runtime."""

    if mode not in MODES:
        raise ValueError(f"unsupported semantic cache mode: {mode}")
    root = Path(runtime_root).expanduser().absolute()
    if is_link_or_reparse(root):
        raise RuntimeError("semantic cache runtime root cannot be a link or reparse point")
    root = root.resolve()
    path = semantic_cache_config_path(root)
    assert_safe_mutable_path(root, path, label="semantic cache runtime configuration")
    policy = load_semantic_cache_policy()
    value = _runtime_config(policy, mode)
    write_json(path, value)
    return value | {"path": str(path)}


def _load_or_initialize_config(
    runtime_root: Path, policy: SemanticCachePolicy
) -> tuple[dict[str, Any], str]:
    path = semantic_cache_config_path(runtime_root)
    if not path.is_file():
        value = configure_semantic_cache(runtime_root, INITIAL_RUNTIME_MODE)
        return {key: item for key, item in value.items() if key != "path"}, "initialized"
    value = read_json(path)
    expected = {
        "schema_version",
        "mode",
        "policy_id",
        "policy_version",
        "policy_digest",
        "updated_at",
    }
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("semantic cache runtime configuration has an unexpected shape")
    if (
        value["schema_version"] != CONFIG_SCHEMA_VERSION
        or value["mode"] not in MODES
        or value["policy_id"] != policy.policy_id
        or value["policy_version"] != policy.policy_version
        or value["policy_digest"] != policy.policy_digest
    ):
        raise ValueError("semantic cache runtime configuration is not policy-compatible")
    return value, "runtime_config"

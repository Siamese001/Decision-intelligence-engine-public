#!/usr/bin/env python3
"""Bind retained local semantic caches to validated active BGE-M3 pairs.

This maintenance command does not load an encoder or write graph/index data.
It first validates each supplied active pair and sidecar, then clears every
cache entry whose encoder binding is absent or differs from that validated
pair.  Roots must be explicit and remain under this checkout's ``.local``
directory.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dual_pipeline.core import digest, is_link_or_reparse, read_json, write_json
from dual_pipeline.release import ReleaseManager
from dual_pipeline.semantic_cache import (
    MODES,
    SemanticCache,
    configure_semantic_cache,
    load_semantic_cache_policy,
    semantic_cache_config_path,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LOCAL_ROOT = (REPOSITORY_ROOT / ".local").resolve()


def _under(root: Path, ancestor: Path) -> bool:
    return root == ancestor or ancestor in root.parents


def _validated_pair(root: Path) -> dict[str, Any]:
    active_path = root / "active_pair.json"
    if not active_path.is_file():
        raise ValueError(f"runtime root has no active pair: {root}")
    raw_pair = read_json(active_path)
    graph_relative = raw_pair.get("graph_path")
    if not isinstance(graph_relative, str) or not graph_relative:
        raise ValueError(f"active pair has no graph path: {active_path}")
    manager = ReleaseManager(root, root / graph_relative)
    manager.active_pair()
    return raw_pair


def rebuild(runtime_roots: list[Path]) -> dict[str, Any]:
    if not runtime_roots:
        raise ValueError("at least one --runtime-root is required")
    results: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for supplied in runtime_roots:
        if is_link_or_reparse(supplied):
            raise ValueError(f"runtime root cannot be a link or reparse point: {supplied}")
        root = supplied.expanduser().absolute().resolve()
        if root in seen:
            continue
        seen.add(root)
        if not _under(root, LOCAL_ROOT):
            raise ValueError(f"runtime root must be under {LOCAL_ROOT}: {root}")
        pair = _validated_pair(root)
        config_path = semantic_cache_config_path(root)
        policy_migrated = False
        if config_path.is_file():
            prior_config = read_json(config_path)
            mode = prior_config.get("mode")
            if not isinstance(mode, str) or mode not in MODES:
                raise ValueError(f"semantic cache configuration has no valid mode: {config_path}")
            policy = load_semantic_cache_policy()
            if (
                prior_config.get("policy_id") != policy.policy_id
                or prior_config.get("policy_version") != policy.policy_version
                or prior_config.get("policy_digest") != policy.policy_digest
            ):
                configure_semantic_cache(root, mode)
                policy_migrated = True
        cache = SemanticCache(root)
        result = cache.rebuild_from_validated_active_pair(pair)
        if result["status"] not in {"BOUND", "REBUILT"}:
            raise RuntimeError(f"semantic cache binding failed for {root}: {result}")
        results.append(
            {
                "runtime_root": str(root),
                "active_pair": {
                    key: pair[key]
                    for key in (
                        "generation_id",
                        "graph_sha256",
                        "sidecar_sha256",
                        "embedding_backend",
                        "model_id",
                        "model_revision",
                        "model_manifest_digest",
                    )
                },
                "cache_database": str(cache.path),
                "policy_migrated": policy_migrated,
                "binding_result": result,
                "cache_status": cache.status(),
            }
        )
    return {
        "status": "PASS",
        "operation": "rebuild_bge_m3_semantic_cache",
        "runtime_count": len(results),
        "purged_entry_count": sum(
            int(item["binding_result"]["purged_entry_count"]) for item in results
        ),
        "runtimes": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = rebuild(args.runtime_root)
    result["canonical_digest"] = digest(result)
    if args.output is not None:
        write_json(args.output.expanduser().absolute(), result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

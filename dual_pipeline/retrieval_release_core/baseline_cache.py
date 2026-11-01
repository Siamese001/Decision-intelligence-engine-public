"""Persistent and in-memory baseline query cache for retrieval release evaluation."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from ..core import digest, read_json, write_json_exclusive

logger = logging.getLogger("dual_pipeline.retrieval_release.baseline_cache")

CACHE_SCHEMA_VERSION = "1.0.0"


def _case_cache_key(
    baseline_graph_sha256: str,
    baseline_sidecar_sha256: str,
    case: dict[str, Any],
) -> str:
    """Builds a deterministic digest key for a test case against a baseline pair."""
    key_payload = [
        baseline_graph_sha256,
        baseline_sidecar_sha256,
        case["query"],
        case["lane"],
        case["classification"],
        sorted(case.get("audiences", [])),
        case.get("top_k", 20),
        case.get("retrieval_mode"),
        case.get("facet_filters"),
        case.get("allowed_assertion_ids"),
    ]
    return digest(key_payload)


class BaselineQueryCache:
    """Caches baseline retrieval evaluation results across cases and runs."""

    def __init__(self, cache_file: Path) -> None:
        self.cache_file = cache_file
        self._entries: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if self.cache_file.is_file():
            try:
                data = read_json(self.cache_file)
                if data.get("schema_version") == CACHE_SCHEMA_VERSION:
                    self._entries = data.get("entries", {})
            except Exception as exc:
                logger.debug(f"[BASELINE-CACHE] Could not read cache {self.cache_file}: {exc}")
                self._entries = {}

    def get(
        self,
        baseline_graph_sha256: str,
        baseline_sidecar_sha256: str,
        case: dict[str, Any],
    ) -> tuple[list[str], list[dict[str, Any]], dict[str, bool]] | None:
        key = _case_cache_key(baseline_graph_sha256, baseline_sidecar_sha256, case)
        entry = self._entries.get(key)
        if entry is not None:
            return (
                entry["baseline_ids"],
                entry["baseline_rank_diagnostics"],
                entry["baseline_components"],
            )
        return None

    def set(
        self,
        baseline_graph_sha256: str,
        baseline_sidecar_sha256: str,
        case: dict[str, Any],
        result: tuple[list[str], list[dict[str, Any]], dict[str, bool]],
    ) -> None:
        key = _case_cache_key(baseline_graph_sha256, baseline_sidecar_sha256, case)
        baseline_ids, baseline_rank_diagnostics, baseline_components = result
        self._entries[key] = {
            "baseline_ids": baseline_ids,
            "baseline_rank_diagnostics": baseline_rank_diagnostics,
            "baseline_components": baseline_components,
        }
        self._dirty = True

    def flush(self) -> None:
        if not self._dirty:
            return
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": CACHE_SCHEMA_VERSION,
                "entries": self._entries,
            }
            tmp_path = self.cache_file.with_name(f".{self.cache_file.name}.tmp")
            write_json_exclusive(tmp_path, payload)
            os.replace(tmp_path, self.cache_file)
            self._dirty = False
            logger.debug(
                f"[BASELINE-CACHE] Flushed {len(self._entries)} entries to {self.cache_file}"
            )
        except Exception as exc:
            logger.debug(f"[BASELINE-CACHE] Failed to flush cache: {exc}")

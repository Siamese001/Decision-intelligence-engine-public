"""
dual_pipeline/document_pipeline_core/audit_dedup.py

Deduplication and measurement layer for candidate healing and release pair validation.
Enforces invariant: normalize/validate once per immutable content digest;
mutation automatically invalidates cache reuse.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, List, Optional, Tuple, Union

_CANDIDATE_HEALING_CACHE: Dict[str, Tuple[str, List[str], float]] = {}
_HEALING_COUNTERS: Dict[str, Any] = {
    "raw_calls": 0,
    "cache_hits": 0,
    "executions": 0,
    "saved_calls": 0,
    "elapsed_saved_sec": 0.0,
}

_PAIR_VALIDATION_CACHE: Dict[Tuple[Any, ...], Tuple[Dict[str, Any], float]] = {}
_VALIDATION_COUNTERS: Dict[str, Any] = {
    "validate_calls": 0,
    "cache_hits": 0,
    "executions": 0,
    "saved_validations": 0,
    "elapsed_saved_sec": 0.0,
}

_META_DIGEST_KEYS = (
    "source_name",
    "modality",
    "document_type",
    "version",
    "is_document",
    "output3_only",
    "project",
    "slug",
    "document_title",
    "title",
)


def _candidate_digest(
    f3_input: Union[str, Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
    source_text: Optional[str] = None,
    stage_context: str = "document_pipeline",
) -> str:
    hasher = hashlib.sha256()
    hasher.update(f"ctx:{stage_context}|".encode("utf-8"))
    data = None
    if isinstance(f3_input, dict):
        data = f3_input
    elif isinstance(f3_input, str):
        try:
            data = json.loads(f3_input)
        except Exception:
            data = None
    if data is not None and isinstance(data, dict):
        hasher.update(json.dumps(data, sort_keys=True).encode("utf-8"))
    else:
        hasher.update(str(f3_input).strip().encode("utf-8"))
    if source_text:
        hasher.update(b"|source:")
        hasher.update(hashlib.sha256(source_text.encode("utf-8")).digest())
    if metadata:
        meta_subset = {k: v for k, v in metadata.items() if k in _META_DIGEST_KEYS}
        hasher.update(b"|meta:")
        hasher.update(json.dumps(meta_subset, sort_keys=True, default=str).encode("utf-8"))
    return hasher.hexdigest()


def healed_output_3_cached(
    f3_input: Union[str, Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
    source_text: Optional[str] = None,
    stage_context: str = "document_pipeline",
) -> Tuple[str, List[str]]:
    """Execute DeterministicHealer.heal_output_3 with immutable digest memoization."""
    from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer

    _HEALING_COUNTERS["raw_calls"] += 1
    digest = _candidate_digest(
        f3_input, metadata=metadata, source_text=source_text, stage_context=stage_context
    )

    if digest in _CANDIDATE_HEALING_CACHE:
        _HEALING_COUNTERS["cache_hits"] += 1
        _HEALING_COUNTERS["saved_calls"] += 1
        entry = _CANDIDATE_HEALING_CACHE[digest]
        healed_str, changes = entry[0], entry[1]
        saved_sec = float(entry[2]) if len(entry) > 2 else 0.0
        _HEALING_COUNTERS["elapsed_saved_sec"] = round(
            float(_HEALING_COUNTERS.get("elapsed_saved_sec") or 0.0) + max(saved_sec, 1e-6), 6
        )
        return healed_str, list(changes)

    start = time.monotonic()
    meta = dict(metadata or {})
    if source_text and "source_text" not in meta:
        meta["source_text"] = source_text

    healed_str, changes = DeterministicHealer.heal_output_3(
        f3_input, metadata=meta, stage_context=stage_context
    )
    elapsed = max(time.monotonic() - start, 1e-6)

    _CANDIDATE_HEALING_CACHE[digest] = (healed_str, list(changes), elapsed)
    healed_digest = _candidate_digest(
        healed_str, metadata=metadata, source_text=source_text, stage_context=stage_context
    )
    if healed_digest != digest:
        _CANDIDATE_HEALING_CACHE[healed_digest] = (healed_str, list(changes), elapsed)
    _HEALING_COUNTERS["executions"] += 1
    return healed_str, list(changes)


def invalidate_candidate_healing_cache() -> None:
    _CANDIDATE_HEALING_CACHE.clear()


def get_healing_counters() -> Dict[str, Any]:
    return dict(_HEALING_COUNTERS)


def reset_healing_counters() -> None:
    _HEALING_COUNTERS.update(
        raw_calls=0,
        cache_hits=0,
        executions=0,
        saved_calls=0,
        elapsed_saved_sec=0.0,
    )
    _CANDIDATE_HEALING_CACHE.clear()


def _file_stat_sig(path_obj: Any) -> Tuple[int, int]:
    try:
        if path_obj is not None and path_obj.is_file():
            st = path_obj.stat()
            return (int(st.st_mtime_ns), int(st.st_size))
    except Exception:
        pass
    return (-1, -1)


def validate_pair_cached(manager: Any, pair: Dict[str, Any]) -> Dict[str, Any]:
    """Execute release pair validation with exact file and SHA cache reuse."""
    _VALIDATION_COUNTERS["validate_calls"] += 1
    graph_path = manager._resolve(pair["graph_path"]) if pair.get("graph_path") else None
    sidecar_path = manager._resolve(pair["sidecar_path"]) if pair.get("sidecar_path") else None
    g_mtime, g_size = _file_stat_sig(graph_path)
    s_mtime, s_size = _file_stat_sig(sidecar_path)
    cache_key = (
        str(pair.get("graph_path")),
        str(pair.get("graph_sha256")),
        g_mtime,
        g_size,
        str(pair.get("sidecar_path")),
        str(pair.get("sidecar_sha256")),
        s_mtime,
        s_size,
        str(pair.get("generation_id")),
    )

    if cache_key in _PAIR_VALIDATION_CACHE and g_mtime >= 0 and s_mtime >= 0:
        _VALIDATION_COUNTERS["cache_hits"] += 1
        _VALIDATION_COUNTERS["saved_validations"] += 1
        cached_entry = _PAIR_VALIDATION_CACHE[cache_key]
        res_dict = cached_entry[0] if isinstance(cached_entry, tuple) else cached_entry
        saved_sec = float(cached_entry[1]) if isinstance(cached_entry, tuple) and len(cached_entry) > 1 else 0.0
        _VALIDATION_COUNTERS["elapsed_saved_sec"] = round(
            float(_VALIDATION_COUNTERS.get("elapsed_saved_sec") or 0.0) + max(saved_sec, 1e-6), 6
        )
        return dict(res_dict)

    start = time.monotonic()
    result = manager._uncached_validate_pair(pair)
    elapsed = max(time.monotonic() - start, 1e-6)

    _PAIR_VALIDATION_CACHE[cache_key] = (dict(result), elapsed)
    _VALIDATION_COUNTERS["executions"] += 1
    return result


def invalidate_pair_validation_cache() -> None:
    _PAIR_VALIDATION_CACHE.clear()


def get_validation_counters() -> Dict[str, Any]:
    return dict(_VALIDATION_COUNTERS)


def reset_validation_counters() -> None:
    _VALIDATION_COUNTERS.update(
        validate_calls=0,
        cache_hits=0,
        executions=0,
        saved_validations=0,
        elapsed_saved_sec=0.0,
    )
    _PAIR_VALIDATION_CACHE.clear()

"""Persistent and in-memory section evaluation caching."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY
from dual_pipeline.storage.governed_context import GovernedStorageContext

logger = logging.getLogger(__name__)

_SECTION_EVALUATION_CACHE: Dict[str, Dict[str, Any]] = {}


def _has_fatal_codes(eval_data: Dict[str, Any]) -> bool:
    if not isinstance(eval_data, dict):
        return False
    fatal = eval_data.get("fatal_codes") or eval_data.get("fatal_defect_codes") or []
    return len(fatal) > 0


def compute_section_cache_key(
    sec_name: str,
    sec_hash: str,
    context_hash: Optional[str] = None,
    *,
    thresholds_hash: Optional[str] = None,
    evaluator_code_sha256: Optional[str] = None,
) -> str:
    """Compute canonical section evaluation cache key binding content, context, thresholds, and evaluator code."""
    base = f"{sec_name}:{sec_hash}:{context_hash}" if context_hash else f"{sec_name}:{sec_hash}"
    t_hash = thresholds_hash
    if t_hash is None:
        pol_str = f"{getattr(CANONICAL_RELEASE_POLICY, 'policy_id', '2.0.0')}:{getattr(CANONICAL_RELEASE_POLICY, 'output1_pass_threshold', 90)}:{getattr(CANONICAL_RELEASE_POLICY, 'output2_pass_threshold', 90)}:{getattr(CANONICAL_RELEASE_POLICY, 'output3_pass_threshold', 90)}"
        t_hash = hashlib.sha256(pol_str.encode("utf-8")).hexdigest()[:12]
    e_sha = evaluator_code_sha256
    if e_sha is None:
        e_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
    return f"{base}:thr_{t_hash}:eval_{e_sha}"


def _is_passing_section_eval(data: Optional[Dict[str, Any]]) -> bool:
    return bool(isinstance(data, dict) and data.get("passed", False) and not _has_fatal_codes(data))


def _load_section_eval_from_db(
    cache_key: str, db_path_fn: Optional[Callable[[], Path]] = None
) -> Optional[Dict[str, Any]]:
    try:
        from .eval_cache import _get_cache_db_path
        db_path = db_path_fn() if db_path_fn else _get_cache_db_path()
        if not db_path.exists():
            return None
        with GovernedStorageContext.read_context(db_path) as conn:
            row = conn.execute(
                "SELECT evaluation_json, passed FROM section_evaluations WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row and row[1]:
                data = json.loads(row[0])
                if _is_passing_section_eval(data):
                    _SECTION_EVALUATION_CACHE[cache_key] = data
                    return dict(data)
    except Exception as exc:
        logger.debug("Disk section cache lookup failed for %s: %s", cache_key, exc)
    return None


def get_cached_section_evaluation(
    sec_name: str,
    sec_hash: str,
    context_hash: Optional[str] = None,
    db_path_fn: Optional[Callable[[], Path]] = None,
    *,
    thresholds_hash: Optional[str] = None,
    evaluator_code_sha256: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve passing evaluation for a specific markdown section by its content hash and context."""
    if not sec_name or not sec_hash:
        return None
    cache_key = compute_section_cache_key(
        sec_name,
        sec_hash,
        context_hash,
        thresholds_hash=thresholds_hash,
        evaluator_code_sha256=evaluator_code_sha256,
    )
    cached = _SECTION_EVALUATION_CACHE.get(cache_key)
    if _is_passing_section_eval(cached):
        return dict(cached)  # type: ignore[arg-type]
    return _load_section_eval_from_db(cache_key, db_path_fn)


def store_cached_section_evaluation(
    sec_name: str,
    sec_hash: str,
    eval_data: Dict[str, Any],
    context_hash: Optional[str] = None,
    db_path_fn: Optional[Callable[[], Path]] = None,
    *,
    thresholds_hash: Optional[str] = None,
    evaluator_code_sha256: Optional[str] = None,
) -> None:
    """Store passing section evaluation in section cache (rejected if fatal_codes are non-empty)."""
    if not isinstance(eval_data, dict) or _has_fatal_codes(eval_data):
        return
    cache_key = compute_section_cache_key(
        sec_name,
        sec_hash,
        context_hash,
        thresholds_hash=thresholds_hash,
        evaluator_code_sha256=evaluator_code_sha256,
    )
    if eval_data.get("passed", False):
        _SECTION_EVALUATION_CACHE[cache_key] = eval_data
        try:
            from .eval_cache import _get_cache_db_path, _init_sqlite_cache
            _init_sqlite_cache()
            db_path = db_path_fn() if db_path_fn else _get_cache_db_path()
            with GovernedStorageContext.write_transaction(db_path) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO section_evaluations (cache_key, passed, evaluation_json) VALUES (?, ?, ?)",
                    (cache_key, 1, json.dumps(eval_data, default=str)),
                )
        except Exception as exc:
            logger.debug("Failed updating persistent section cache: %s", exc)


def clear_section_evaluation_cache() -> None:
    """Clears section evaluation in-memory cache."""
    _SECTION_EVALUATION_CACHE.clear()

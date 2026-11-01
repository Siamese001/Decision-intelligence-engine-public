"""Evaluation caching module for transcript QA.

Maintains content-addressable in-memory and persistent SQLite cache,
and validates pinned release evaluations.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY
from dual_pipeline.storage.governed_context import GovernedStorageContext
from .eval_pin_validator import check_pin
from .eval_section_cache import (
    _SECTION_EVALUATION_CACHE,
    clear_section_evaluation_cache,
    compute_section_cache_key,
    get_cached_section_evaluation,
    store_cached_section_evaluation,
)
from .eval_short_circuit import (
    _build_skip_eval,
    _collect_failed_gates,
    _is_section_passed,
    build_short_circuit_eval,
)

logger = logging.getLogger(__name__)

CACHE_KEY_SCHEMA_VERSION = "2.1.0"

# Content-addressable evaluation cache: key -> evaluation scorecard dict
_EVALUATION_CACHE: Dict[str, Dict[str, Any]] = {}


def _sanitize_attachment_block(meta: Dict[str, Any], f1_text: str) -> str:
    raw_att = str(meta.get("attachment_prompt_block") or meta.get("collateral") or meta.get("collateral_text") or "")
    if not raw_att:
        return f1_text
    from dual_pipeline.sanitizer import LocalPreEgressSanitizer
    clean_att = str(LocalPreEgressSanitizer.sanitize_text(raw_att))
    return f"{f1_text}\n\n{clean_att}" if clean_att else f1_text


def _get_cache_db_path() -> Path:
    from storage.credentials import CredentialManager
    base = CredentialManager.get_env("NEE_CACHE_DIR", ".local/runtime/cache") or ".local/runtime/cache"
    path = Path(base) / "judge_evaluation_cache.sqlite"
    with contextlib.suppress(Exception):
        path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _init_sqlite_cache() -> None:
    try:
        db_path = _get_cache_db_path()
        with GovernedStorageContext.write_transaction(db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS judge_evaluations (
                    cache_key TEXT PRIMARY KEY,
                    composite_score INTEGER,
                    verdict TEXT,
                    evaluation_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS section_evaluations (
                    cache_key TEXT PRIMARY KEY,
                    passed INTEGER,
                    evaluation_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
    except Exception as exc:
        logger.debug("Persistent judge cache init unavailable: %s", exc)


def compute_c0_receipt_digest(metadata: Optional[Dict[str, Any]]) -> str:
    """Compute deterministic SHA-256 digest of C0 roster, title, and role receipts."""
    if not metadata or not isinstance(metadata, dict):
        return "none"
    if metadata.get("c0_receipt_digest"):
        return str(metadata["c0_receipt_digest"])
    c0_components = {
        "c05": metadata.get("c05_receipt_digest"),
        "c06": metadata.get("c06_receipt_digest"),
        "attendees": metadata.get("attendees") or metadata.get("roster"),
        "hitl_role_resolutions": metadata.get("hitl_role_resolutions"),
        "speaker_roles": metadata.get("speaker_roles"),
        "c0_roster": metadata.get("c0_roster"),
    }
    active = {k: v for k, v in c0_components.items() if v is not None}
    if not active:
        return "none"
    canonical = json.dumps(active, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _detect_output_num(cache_key: str) -> Optional[int]:
    if cache_key.startswith("out1:") or ":output_1:" in cache_key or cache_key.startswith("judge:output_1:"):
        return 1
    if cache_key.startswith("out2:") or ":output_2:" in cache_key or cache_key.startswith("judge:output_2:"):
        return 2
    if cache_key.startswith("out3:") or ":output_3:" in cache_key or cache_key.startswith("judge:output_3:"):
        return 3
    return None


def _has_fatal_codes(ev: Dict[str, Any]) -> bool:
    if not isinstance(ev, dict):
        return False
    fatal = ev.get("fatal_codes") or ev.get("fatal_defect_codes") or []
    return len(fatal) > 0


def is_borderline_or_hitl(ev: Dict[str, Any]) -> bool:
    """Borderline results ([85, 90), HITL_REQUIRED, fatal_codes, or spread exceeded) are never cached."""
    if not isinstance(ev, dict):
        return True
    if _has_fatal_codes(ev):
        return True
    verdict = str(ev.get("verdict", "")).upper()
    status = str(ev.get("status", "")).upper()
    if verdict == "HITL_REQUIRED" or status == "HITL_REQUIRED":
        return True
    defect_codes = ev.get("defect_codes") or []
    if "ERR_SAMPLE_SPREAD_EXCEEDED" in defect_codes or "ERR_PROVIDER_429_HITL" in defect_codes:
        return True
    sc = ev.get("composite_score")
    if sc is not None and isinstance(sc, (int, float)) and not isinstance(sc, bool):
        if 80.0 <= float(sc) < 90.0:
            return True
    return False


def check_cache(cache_key: str, min_score: Optional[int] = None, allow_failure: bool = False) -> Optional[Dict[str, Any]]:
    """Retrieve entry from in-memory evaluation cache or persistent disk cache."""
    output_num = _detect_output_num(cache_key)
    from dual_pipeline.release_policy import validate_micro_judge_scorecard

    cached = _EVALUATION_CACHE.get(cache_key)
    if cached and not is_borderline_or_hitl(cached):
        score = cached.get("composite_score", 0)
        verdict = cached.get("verdict")
        is_valid, _ = validate_micro_judge_scorecard(cached, output_num=output_num)
        if is_valid and (allow_failure or (min_score is not None and score >= min_score and verdict in ("PASS", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION"))):
            res = dict(cached)
            res["source"] = "CONTENT_CACHE"
            return res

    try:
        db_path = _get_cache_db_path()
        if db_path.exists():
            with GovernedStorageContext.read_context(db_path) as conn:
                row = conn.execute(
                    "SELECT evaluation_json, composite_score, verdict FROM judge_evaluations WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone()
                if row:
                    eval_json, score, verdict = row
                    data = json.loads(eval_json)
                    if not is_borderline_or_hitl(data):
                        is_valid, _ = validate_micro_judge_scorecard(data, output_num=output_num)
                        if is_valid and (allow_failure or (min_score is not None and score >= min_score and verdict in ("PASS", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION"))):
                            _EVALUATION_CACHE[cache_key] = data
                            res = dict(data)
                            res["source"] = "CONTENT_CACHE"
                            return res
    except Exception as exc:
        logger.debug("Disk cache lookup failed for %s: %s", cache_key, exc)

    return None


def get_judge_prompt_hash(artifact_num: int, archetype: Optional[Any] = None) -> str:
    """Compute truncated SHA-256 hash of the compiled judge prompt for cache key addressing."""
    try:
        from dual_pipeline.prompts import (
            MeetingArchetype,
            OUTPUT1_CONTRACT,
            OUTPUT2_CONTRACT,
            OUTPUT3_CONTRACT,
        )
        arch = archetype if isinstance(archetype, MeetingArchetype) else MeetingArchetype.GENERAL
        contracts = {
            1: OUTPUT1_CONTRACT,
            2: OUTPUT2_CONTRACT,
            3: OUTPUT3_CONTRACT,
        }
        contract = contracts.get(artifact_num)
        if not contract:
            raise ValueError(f"Unknown artifact number {artifact_num}")
        text = contract.compile_judge_prompt(arch)
        if not text:
            raise ValueError(f"Empty compiled prompt for output_{artifact_num}")
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    except Exception as exc:
        raise RuntimeError(f"Authoritative prompt hash unavailable for output_{artifact_num}: {exc}") from exc


def resolve_pins_and_cache(
    f1_text: str,
    f2_text: str,
    f3_text: str,
    model: str,
    reasoning_effort: str,
    pinned: Dict[str, Any],
    source_text: Optional[str] = None,
    *,
    metadata: Optional[Dict[str, Any]] = None,
    archetype: Optional[Any] = None,
    rubric_version: str = "2.0",
    topology: str = "decomposed_concurrent",
    policy_version: Optional[str] = None,
    allow_failure: bool = False,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], str, str, str]:
    """Resolve evaluations from pinned evaluations dictionary, in-memory cache, or disk cache."""
    meta = metadata or {}
    c0_digest = compute_c0_receipt_digest(meta)
    arch = archetype or meta.get("archetype") or meta.get("meeting_archetype")
    arch_str = str(arch.value if hasattr(arch, "value") else (arch or "GENERAL")).upper()
    rubric_ver = str(rubric_version or meta.get("rubric_version") or "2.0")
    topo_str = str(topology or meta.get("topology") or "decomposed_concurrent")
    pol_ver = str(policy_version or getattr(CANONICAL_RELEASE_POLICY, "policy_id", "2.0.0"))

    collateral = meta.get("collateral") or meta.get("collateral_text") or meta.get("collateral_sha256")
    col_tag = f":col_{hashlib.sha256(str(collateral).encode('utf-8')).hexdigest()[:16]}" if collateral else ""
    prov = str(meta.get("provider", "default"))
    prov_tag = f":prov_{prov}" if prov != "default" else ""
    rev = str(meta.get("model_revision", ""))
    rev_tag = f":rev_{rev[:16]}" if rev else ""
    cov = str(meta.get("evaluated_span_digest") or meta.get("coverage_digest") or "")
    cov_tag = f":cov_{cov[:16]}" if cov else ""

    h1 = hashlib.sha256(f1_text.encode("utf-8")).hexdigest()
    h2 = hashlib.sha256(f2_text.encode("utf-8")).hexdigest()
    h3 = hashlib.sha256(f3_text.encode("utf-8")).hexdigest()
    p1_hash = get_judge_prompt_hash(1, archetype=arch)
    p2_hash = get_judge_prompt_hash(2, archetype=arch)
    p3_hash = get_judge_prompt_hash(3, archetype=arch)

    f1_for_cit = _sanitize_attachment_block(meta, f1_text)

    out1 = check_pin(
        pinned, "output_1", "output_01", CANONICAL_RELEASE_POLICY.output1_pass_threshold, h1,
        text=f1_text, expected_c0_digest=c0_digest, expected_prompt_hash=p1_hash,
        expected_rubric_version=rubric_ver, expected_topology=topo_str,
    )
    out2 = check_pin(
        pinned, "output_2", "output_02", CANONICAL_RELEASE_POLICY.output2_pass_threshold, h2,
        text=f2_text, expected_c0_digest=c0_digest, expected_prompt_hash=p2_hash,
        expected_rubric_version=rubric_ver, expected_topology=topo_str,
        f1_text=f1_for_cit,
    )
    out3 = check_pin(
        pinned, "output_3", "output_03", CANONICAL_RELEASE_POLICY.output3_pass_threshold, h3,
        text=f3_text, expected_c0_digest=c0_digest, expected_prompt_hash=p3_hash,
        expected_rubric_version=rubric_ver, expected_topology=topo_str,
        f1_text=f1_for_cit,
    )

    src_tag = f":{hashlib.sha256(source_text.encode('utf-8')).hexdigest()[:16]}" if source_text else ""
    dep_tag = f":c0_{c0_digest}:arch_{arch_str}:rub_{rubric_ver}:topo_{topo_str}:pol_{pol_ver}{col_tag}{prov_tag}{rev_tag}{cov_tag}"

    k1 = f"out1:{h1}{src_tag}:{model}:{reasoning_effort}:{p1_hash}{dep_tag}"
    k2 = f"out2:{h2}{src_tag}:{model}:{reasoning_effort}:{p2_hash}{dep_tag}"
    k3 = f"out3:{h3}{src_tag}:{model}:{reasoning_effort}:{p3_hash}{dep_tag}"

    allow_fail = meta.get("allow_cached_failure", allow_failure)
    if out1 is None:
        out1 = check_cache(k1, CANONICAL_RELEASE_POLICY.output1_pass_threshold, allow_failure=allow_fail)
    if out2 is None:
        out2 = check_cache(k2, CANONICAL_RELEASE_POLICY.output2_pass_threshold, allow_failure=allow_fail)
    if out3 is None:
        out3 = check_cache(k3, CANONICAL_RELEASE_POLICY.output3_pass_threshold, allow_failure=allow_fail)

    return out1, out2, out3, k1, k2, k3


def update_evaluation_cache(
    k1: str, out1_eval: Optional[Dict[str, Any]],
    k2: str = "", out2_eval: Optional[Dict[str, Any]] = None,
    k3: str = "", out3_eval: Optional[Dict[str, Any]] = None,
) -> None:
    """Store valid completed evaluations into content-addressable in-memory and persistent disk cache."""
    from dual_pipeline.release_policy import validate_micro_judge_scorecard
    valid_verdicts = ("PASS", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION")
    raw_items = [(k1, out1_eval), (k2, out2_eval), (k3, out3_eval)]
    items = [
        (k, ev, _detect_output_num(k))
        for k, ev in raw_items if k and ev
    ]
    for k, ev, out_num in items:
        if (
            ev
            and ev.get("verdict") in valid_verdicts
            and ev.get("status") not in ("SKIPPED", "NOT_APPLICABLE", "FAIL")
            and ev.get("reason") != "DETERMINISTIC_QA_FAILED"
            and not is_borderline_or_hitl(ev)
        ):
            is_valid, _ = validate_micro_judge_scorecard(ev, output_num=out_num)
            if is_valid:
                _EVALUATION_CACHE[k] = ev

    try:
        _init_sqlite_cache()
        db_path = _get_cache_db_path()
        with GovernedStorageContext.write_transaction(db_path) as conn:
            for k, ev, out_num in items:
                if (
                    ev
                    and ev.get("verdict") in valid_verdicts
                    and ev.get("status") not in ("SKIPPED", "NOT_APPLICABLE", "FAIL")
                    and ev.get("reason") != "DETERMINISTIC_QA_FAILED"
                    and not is_borderline_or_hitl(ev)
                ):
                    is_valid, _ = validate_micro_judge_scorecard(ev, output_num=out_num)
                    if is_valid:
                        conn.execute(
                            "INSERT OR REPLACE INTO judge_evaluations (cache_key, composite_score, verdict, evaluation_json) VALUES (?, ?, ?, ?)",
                            (k, int(ev.get("composite_score", 0)), str(ev.get("verdict", "")), json.dumps(ev, default=str)),
                        )
    except Exception as exc:
        logger.debug("Failed updating persistent judge cache: %s", exc)


def clear_evaluation_cache() -> None:
    """Clear all in-memory evaluation and section caches and persistent DB for test isolation."""
    _EVALUATION_CACHE.clear()
    clear_section_evaluation_cache()
    try:
        db_path = _get_cache_db_path()
        if db_path.exists():
            with GovernedStorageContext.write_transaction(db_path) as conn:
                clear_evals_sql = "DELETE FROM judge_evaluations"
                clear_sections_sql = "DELETE FROM section_evaluations"
                conn.execute(clear_evals_sql)
                conn.execute(clear_sections_sql)
    except Exception as exc:
        logger.debug("Failed clearing persistent judge cache: %s", exc)


def invalidate_c0_cache(c0_digest: Optional[str] = None) -> int:
    """Purge in-memory and persistent SQLite cache entries associated with a specific C0 digest, or all if None."""
    global _EVALUATION_CACHE
    purged = 0
    tag = f":c0_{c0_digest}" if c0_digest else ":c0_"
    to_delete = [k for k in _EVALUATION_CACHE if tag in k]
    for k in to_delete:
        del _EVALUATION_CACHE[k]
        purged += 1

    try:
        db_path = _get_cache_db_path()
        if db_path.exists():
            with GovernedStorageContext.write_transaction(db_path) as conn:
                if c0_digest:
                    del_digest_sql = "DELETE FROM judge_evaluations WHERE cache_key LIKE ?"
                    cur = conn.execute(del_digest_sql, (f"%:c0_{c0_digest}%",))
                else:
                    del_all_sql = "DELETE FROM judge_evaluations"
                    cur = conn.execute(del_all_sql)
                purged += cur.rowcount
    except Exception as exc:
        logger.debug("Failed purging persistent C0 cache: %s", exc)

    return purged


__all__ = [
    "CACHE_KEY_SCHEMA_VERSION",
    "compute_c0_receipt_digest",
    "compute_section_cache_key",
    "check_pin",
    "is_borderline_or_hitl",
    "check_cache",
    "get_judge_prompt_hash",
    "resolve_pins_and_cache",
    "update_evaluation_cache",
    "clear_evaluation_cache",
    "invalidate_c0_cache",
    "get_cached_section_evaluation",
    "store_cached_section_evaluation",
    "build_short_circuit_eval",
]

"""Public facade and <2ms pre-query dirty-check guard for sub-second candidate-to-canonical fast-promotion."""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.core import ROOT, connect_sqlite
from dual_pipeline.graph_writer_core.fast_promotion import (
    FastPromotionResult,
    fast_promote_candidates,
)
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root

_DEFAULT_PROMOTE_CONFIDENCE = 0.75
_HYPOTHESIS_MIN_CONFIDENCE = 0.85

# Memoized file signatures for clean graphs: path -> (mtime_ns, size, wal_mtime_ns, wal_size, min_conf)
_CLEAN_GRAPH_SIGNATURES: dict[str, tuple[int, int, int, int, float]] = {}

_DIRTY_CANDIDATE_PROBE_SQL = """
SELECT 1
FROM assertion a
JOIN node subj ON a.subject_node_id = subj.node_id AND subj.active = 1
LEFT JOIN node obj ON a.object_node_id = obj.node_id
WHERE (
    (a.canonical_status = 'candidate' AND COALESCE(a.canonical_eligible, 0) = 0 AND COALESCE(a.currentness_state, 'current') = 'current')
    OR (
      a.canonical_status = 'historical_candidate'
      AND a.superseded_at IS NULL
      AND COALESCE(a.validation_state, '') NOT IN ('rejected', 'superseded', 'retired')
    )
  )
  AND a.superseded_by_assertion_id IS NULL
  AND LOWER(COALESCE(a.assertion_type, '')) NOT IN ('open_question', 'question', 'contradiction', 'supersession')
  AND COALESCE(a.confidence_score, 0.0) >= CASE
        WHEN LOWER(COALESCE(a.assertion_type, '')) IN ('assumption', 'hypothesis') THEN MAX(?, ?)
        ELSE ?
      END
  AND (a.object_node_id IS NULL OR obj.active = 1)
  AND EXISTS (
    SELECT 1 FROM assertion_evidence ae
    WHERE ae.assertion_id = a.assertion_id AND ae.evidence_relationship = 'supports'
  )
LIMIT 1
"""


def _graph_file_signature(graph_path: Path, min_confidence: float) -> tuple[int, int, int, int, float]:
    st = graph_path.stat()
    wal_path = graph_path.with_name(f"{graph_path.name}-wal")
    if wal_path.is_file():
        wst = wal_path.stat()
        return (st.st_mtime_ns, st.st_size, wst.st_mtime_ns, wst.st_size, float(min_confidence))
    return (st.st_mtime_ns, st.st_size, 0, 0, float(min_confidence))


def has_unpromoted_candidates(
    graph_path: Path | str,
    *,
    min_confidence: float = _DEFAULT_PROMOTE_CONFIDENCE,
) -> bool:
    """Sub-2ms check for promotable staged candidate assertions in the target graph."""
    p = Path(graph_path).resolve()
    if not p.is_file():
        return False
    sig = _graph_file_signature(p, min_confidence)
    if _CLEAN_GRAPH_SIGNATURES.get(str(p)) == sig:
        return False
    conn = connect_sqlite(p, readonly=True)
    try:
        row = conn.execute(
            _DIRTY_CANDIDATE_PROBE_SQL,
            (min_confidence, _HYPOTHESIS_MIN_CONFIDENCE, min_confidence),
        ).fetchone()
        if row is not None:
            from dual_pipeline.graph_writer_core.fast_promotion import _plan_candidate_promotions
            to_promote, to_dedup, _ = _plan_candidate_promotions(conn, source_id=None, min_confidence=min_confidence)
            if not to_promote and not to_dedup:
                row = None
        if row is None:
            row = conn.execute(
                "SELECT 1 FROM node n WHERE n.active = 1 AND n.status = 'candidate' "
                "AND EXISTS (SELECT 1 FROM assertion a WHERE a.canonical_status = 'canonical' "
                "AND (a.subject_node_id = n.node_id OR a.object_node_id = n.node_id)) LIMIT 1"
            ).fetchone()
    except Exception:
        row = None
    finally:
        conn.close()
    if row is None:
        _CLEAN_GRAPH_SIGNATURES[str(p)] = sig
        return False
    return True


def ensure_staged_candidates_promoted(
    graph_path: Path | str | None = None,
    *,
    runtime_root: Path | str | None = None,
    min_confidence: float = _DEFAULT_PROMOTE_CONFIDENCE,
    approval_reference: str | None = None,
) -> tuple[Path, FastPromotionResult | None]:
    """Ensure all eligible staged candidates are promoted before a read query executes."""
    rt_root = Path(runtime_root).resolve() if runtime_root else resolve_authoritative_runtime_root(ROOT)
    ap = rt_root / "active_pair.json"
    live_active_db = (
        (rt_root / json.loads(ap.read_text(encoding="utf-8"))["graph_path"]).resolve()
        if ap.is_file()
        else None
    )
    target_db = (
        Path(graph_path).resolve()
        if graph_path is not None
        else (live_active_db or (ROOT / "graph" / "nee_graph.sqlite").resolve())
    )
    if target_db.name == "nee_graph.sqlite" and target_db.parent.name == "graph":
        return target_db, None
    if not target_db.is_file() or not has_unpromoted_candidates(target_db, min_confidence=min_confidence):
        return target_db, None

    is_live_active = live_active_db is not None and target_db == live_active_db
    res = fast_promote_candidates(
        None if is_live_active else target_db,
        runtime_root=rt_root,
        min_confidence=min_confidence,
        approval_reference=approval_reference,
        promote_generation=is_live_active,
        dry_run=False,
    )
    final_db = Path(res.graph_path).resolve() if res.graph_path else target_db
    if final_db.is_file():
        _CLEAN_GRAPH_SIGNATURES[str(final_db)] = _graph_file_signature(final_db, min_confidence)
    if target_db.is_file():
        _CLEAN_GRAPH_SIGNATURES[str(target_db)] = _graph_file_signature(target_db, min_confidence)
    return final_db, res


__all__ = [
    "FastPromotionResult",
    "ensure_staged_candidates_promoted",
    "fast_promote_candidates",
    "has_unpromoted_candidates",
]

"""Graph Signal Evaluation Engine enforcing the GS-10 Non-Degradation Standard."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dual_pipeline.core import connect_sqlite, digest, utc_now

from .constants import (
    GRAPH_SIGNAL_SCHEMA_VERSION,
    MAX_ORPHAN_RATIO,
    MIN_APPROVED_LANE_PURITY,
    MIN_GROUNDING_DEPTH,
    MUTEX_CARDINALITY_PREDICATES,
    PENALTY_MUTEX_CONFLICT,
    PENALTY_ORPHAN_EDGE,
    SIGNAL_WEIGHTS,
    TYPED_PREDICATES_EXCLUDED,
)


def _parse_epoch(date_str: Optional[str]) -> Optional[float]:
    if not date_str:
        return None
    cleaned = date_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _are_compatible_scopes(o1: str, t1: str, o2: str, t2: str) -> bool:
    org_ok = "unknown_org" in (o1, o2) or o1 == o2
    track_ok = "general_track" in (t1, t2) or t1 == t2
    return org_ok and track_ok


def _find_conflicting_subjects_for_group(conn_g: Any, obj_id: str, pred: str) -> list[str]:
    from .mutex_invalidation import _resolve_subject_org_and_track

    a_rows = conn_g.execute(
        """SELECT assertion_id, subject_node_id FROM assertion
           WHERE currentness_state = 'current' AND canonical_status = 'canonical'
             AND object_node_id = ? AND predicate = ?""",
        (obj_id, pred),
    ).fetchall()
    resolved = [(subj, *_resolve_subject_org_and_track(conn_g, subj, aid)) for aid, subj in a_rows]
    conflict_subjs = {
        s
        for i, (s1, o1, t1) in enumerate(resolved)
        for s2, o2, t2 in resolved[i + 1 :]
        if _are_compatible_scopes(o1, t1, o2, t2)
        for s in (s1, s2)
    }
    return sorted(conflict_subjs)


def _compute_mutex_conflicts(conn_g: Any) -> tuple[list[dict[str, Any]], int]:
    mutex_placeholders = ",".join("?" for _ in MUTEX_CARDINALITY_PREDICATES)
    mutex_rows = conn_g.execute(
        f"""SELECT object_node_id, predicate, count(*) as cnt
              FROM assertion WHERE currentness_state = 'current' AND canonical_status = 'canonical'
               AND predicate IN ({mutex_placeholders}) AND object_node_id IS NOT NULL
               AND (object_node_id LIKE 'workstream%' OR object_node_id LIKE 'entity-%' OR object_node_id LIKE 'pod%')
             GROUP BY object_node_id, predicate HAVING cnt > 1""",
        tuple(MUTEX_CARDINALITY_PREDICATES),
    ).fetchall()
    mutex_conflicts = []
    for obj_id, pred, _ in mutex_rows:
        subjs = _find_conflicting_subjects_for_group(conn_g, obj_id, pred)
        if subjs:
            mutex_conflicts.append({"target": obj_id, "predicate": pred, "count": len(subjs), "subjects": subjs})
    h_mutex = sum(max(1, c["count"] - 1) for c in mutex_conflicts)
    return mutex_conflicts, h_mutex


def evaluate_graph_signal(
    graph_path: Path,
    sidecar_path: Optional[Path] = None,
    now_epoch: Optional[float] = None,
) -> Dict[str, Any]:
    """Computes the 10-dimensional GS-10 Graph Signal Vector on a graph and sidecar pair."""
    resolved_graph = Path(graph_path).expanduser().resolve()
    if not resolved_graph.is_file():
        raise FileNotFoundError(f"Graph database not found: {resolved_graph}")

    if now_epoch is None:
        now_epoch = datetime.now(timezone.utc).timestamp()

    conn_g = connect_sqlite(resolved_graph, readonly=True)
    try:
        conn_g.row_factory = None
        # 1. Active Nodes and Edges
        v_active = conn_g.execute("SELECT count(*) FROM node").fetchone()[0]
        e_active = conn_g.execute(
            "SELECT count(*) FROM assertion WHERE currentness_state = 'current'"
        ).fetchone()[0]
        density_rho = e_active / max(1, v_active)

        # 2. Epistemic Typedness Index (tau)
        typed_cnt = conn_g.execute(
            """SELECT count(*) FROM assertion
                WHERE currentness_state = 'current'
                  AND predicate NOT IN (?, ?)
                  AND assertion_type IS NOT NULL""",
            tuple(TYPED_PREDICATES_EXCLUDED),
        ).fetchone()[0]
        typedness_tau = typed_cnt / max(1, e_active)

        # 3. Calibrated Confidence Mean
        conf_rows = conn_g.execute(
            """SELECT confidence_score FROM assertion
                WHERE currentness_state = 'current'
                  AND confidence_score IS NOT NULL"""
        ).fetchall()
        conf_sum = sum(float(r[0]) for r in conf_rows if r[0] is not None)
        confidence_mean = conf_sum / max(1, e_active)

        # 4. Provenance Grounding Depth (gamma)
        grounded_cnt = conn_g.execute(
            """SELECT count(DISTINCT a.assertion_id)
                 FROM assertion a
                 JOIN assertion_evidence ae ON ae.assertion_id = a.assertion_id
                WHERE a.currentness_state = 'current'
                  AND ae.evidence_relationship = 'supports'"""
        ).fetchone()[0]
        grounding_depth_gamma = grounded_cnt / max(1, e_active)

        # 5. Temporal Freshness Index (phi)
        lambda_decay = 0.34657  # half-life of ~60 days (lambda = ln(2)/2 per 30d)
        date_rows = conn_g.execute(
            """SELECT recorded_at, created_at FROM assertion
                WHERE currentness_state = 'current'"""
        ).fetchall()
        freshness_sum = 0.0
        for r_at, c_at in date_rows:
            ep = _parse_epoch(r_at) or _parse_epoch(c_at) or (now_epoch - 86400 * 90)
            delta_days = max(0.0, (now_epoch - ep) / 86400.0)
            freshness_sum += math.exp(-lambda_decay * (delta_days / 30.0))
        temporal_freshness_phi = freshness_sum / max(1, e_active)

        # 6. Mutex Conflict Entropy (H_mutex) on target workstreams/pods
        mutex_conflicts, h_mutex = _compute_mutex_conflicts(conn_g)

        # 7. Orphan Edge Ratio
        orphan_cnt = conn_g.execute(
            """SELECT count(*) FROM assertion a
                WHERE a.currentness_state = 'current'
                  AND (
                      a.primary_source_id NOT IN (SELECT source_id FROM source)
                      OR a.subject_node_id NOT IN (SELECT node_id FROM node)
                  )"""
        ).fetchone()[0]
        orphan_ratio = orphan_cnt / max(1, e_active)

        # 8. Trait Completeness Coverage (lambda_cov)
        full_trait_cnt = conn_g.execute(
            """SELECT count(*) FROM assertion a
                WHERE a.currentness_state = 'current'
                  AND a.assertion_type IS NOT NULL
                  AND a.confidence_score IS NOT NULL
                  AND a.origin_type IS NOT NULL
                  AND a.validation_state IS NOT NULL
                  AND EXISTS (
                      SELECT 1 FROM assertion_evidence ae
                       WHERE ae.assertion_id = a.assertion_id
                         AND ae.evidence_relationship = 'supports'
                  )"""
        ).fetchone()[0]
        trait_completeness_lambda = full_trait_cnt / max(1, e_active)

    finally:
        conn_g.close()

    # 9. Approved Lane Purity in sidecar
    approved_lane_purity_pi = 1.0
    sidecar_metrics: Dict[str, Any] = {}
    if sidecar_path is not None and Path(sidecar_path).is_file():
        conn_s = connect_sqlite(sidecar_path, readonly=True)
        try:
            total_appr = conn_s.execute(
                "SELECT count(*) FROM retrieval_record WHERE lane = 'approved'"
            ).fetchone()[0]
            impure_appr = conn_s.execute(
                """SELECT count(*) FROM retrieval_record
                    WHERE lane = 'approved'
                      AND (
                          json_extract(artifact_json, '$.currentness_state') = 'historical'
                          OR json_extract(artifact_json, '$.canonical_status') = 'superseded'
                      )"""
            ).fetchone()[0]
            approved_lane_purity_pi = (
                (total_appr - impure_appr) / max(1, total_appr)
            )
            sidecar_metrics = {
                "approved_record_count": total_appr,
                "impure_approved_count": impure_appr,
                "candidate_record_count": conn_s.execute(
                    "SELECT count(*) FROM retrieval_record WHERE lane = 'candidate'"
                ).fetchone()[0],
            }
        finally:
            conn_s.close()

    # 10. Composite Score Q_net
    base_score = (
        SIGNAL_WEIGHTS["typedness"] * typedness_tau
        + SIGNAL_WEIGHTS["confidence"] * confidence_mean
        + SIGNAL_WEIGHTS["grounding"] * grounding_depth_gamma
        + SIGNAL_WEIGHTS["freshness"] * temporal_freshness_phi
        + SIGNAL_WEIGHTS["purity"] * approved_lane_purity_pi
        + SIGNAL_WEIGHTS["trait_completeness"] * trait_completeness_lambda
    ) * 100.0

    penalties = (h_mutex * PENALTY_MUTEX_CONFLICT) + (orphan_cnt * PENALTY_ORPHAN_EDGE)
    composite_score_q = max(0.0, min(100.0, base_score - penalties))

    metrics = {
        "v_active": v_active,
        "e_active": e_active,
        "density_rho": round(density_rho, 4),
        "typedness_tau": round(typedness_tau, 4),
        "confidence_mean": round(confidence_mean, 4),
        "grounding_depth_gamma": round(grounding_depth_gamma, 4),
        "temporal_freshness_phi": round(temporal_freshness_phi, 4),
        "mutex_conflict_entropy_h": h_mutex,
        "approved_lane_purity_pi": round(approved_lane_purity_pi, 4),
        "orphan_edge_ratio": round(orphan_ratio, 4),
        "trait_completeness_lambda": round(trait_completeness_lambda, 4),
        "composite_score_q": round(composite_score_q, 2),
    }

    return {
        "schema_version": GRAPH_SIGNAL_SCHEMA_VERSION,
        "evaluated_at": utc_now(),
        "graph_path": str(resolved_graph),
        "sidecar_path": str(sidecar_path) if sidecar_path else None,
        "metrics": metrics,
        "sidecar_metrics": sidecar_metrics,
        "mutex_conflicts": mutex_conflicts,
        "signal_digest": digest(metrics),
    }


def evaluate_differential_signal(
    pre_eval: Dict[str, Any],
    post_eval: Dict[str, Any],
) -> Dict[str, Any]:
    """Evaluates the differential pre/post signal vector and returns pass/fail verdict."""
    m_pre = pre_eval["metrics"]
    m_post = post_eval["metrics"]

    deltas = {
        "delta_density_rho": round(m_post["density_rho"] - m_pre["density_rho"], 4),
        "delta_typedness_tau": round(m_post["typedness_tau"] - m_pre["typedness_tau"], 4),
        "delta_confidence_mean": round(m_post["confidence_mean"] - m_pre["confidence_mean"], 4),
        "delta_grounding_depth": round(m_post["grounding_depth_gamma"] - m_pre["grounding_depth_gamma"], 4),
        "delta_temporal_freshness": round(m_post["temporal_freshness_phi"] - m_pre["temporal_freshness_phi"], 4),
        "delta_mutex_conflict_entropy": m_post["mutex_conflict_entropy_h"] - m_pre["mutex_conflict_entropy_h"],
        "delta_approved_lane_purity": round(m_post["approved_lane_purity_pi"] - m_pre["approved_lane_purity_pi"], 4),
        "delta_orphan_edge_ratio": round(m_post["orphan_edge_ratio"] - m_pre["orphan_edge_ratio"], 4),
        "delta_trait_completeness": round(m_post["trait_completeness_lambda"] - m_pre["trait_completeness_lambda"], 4),
        "delta_composite_score_q": round(m_post["composite_score_q"] - m_pre["composite_score_q"], 2),
    }

    # Strict Fail-Closed Invariants
    failures: List[str] = []
    if deltas["delta_mutex_conflict_entropy"] > 0:
        failures.append(
            f"MUTEX_CONFLICTS_PRESENT: {deltas['delta_mutex_conflict_entropy']} active multi-lead/delivery conflicts detected"
        )
    if m_post["approved_lane_purity_pi"] < MIN_APPROVED_LANE_PURITY:
        failures.append(
            f"APPROVED_LANE_IMPURITY: Purity {m_post['approved_lane_purity_pi']} < {MIN_APPROVED_LANE_PURITY} (stale assertions polluting approved lane)"
        )
    if m_post["orphan_edge_ratio"] > MAX_ORPHAN_RATIO:
        failures.append(
            f"ORPHAN_ASSERTIONS_DETECTED: {m_post['orphan_edge_ratio']} orphan ratio exceeds threshold {MAX_ORPHAN_RATIO}"
        )
    if deltas["delta_composite_score_q"] < -0.5:
        failures.append(
            f"SIGNAL_DEGRADATION: Composite score decreased by {deltas['delta_composite_score_q']} points"
        )
    if m_post["grounding_depth_gamma"] < MIN_GROUNDING_DEPTH:
        failures.append(
            f"INSUFFICIENT_GROUNDING: Grounding depth {m_post['grounding_depth_gamma']} < {MIN_GROUNDING_DEPTH}"
        )

    passed = len(failures) == 0

    return {
        "schema_version": GRAPH_SIGNAL_SCHEMA_VERSION,
        "evaluated_at": utc_now(),
        "passed": passed,
        "verdict": "PASS" if passed else "FAIL_CLOSED",
        "pre_metrics": m_pre,
        "post_metrics": m_post,
        "deltas": deltas,
        "violations": failures,
    }

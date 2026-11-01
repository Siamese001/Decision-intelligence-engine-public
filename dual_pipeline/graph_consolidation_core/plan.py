"""
dual_pipeline/graph_consolidation_core/plan.py

Builds a contract-valid consolidation proposal plan from read-only detection passes.
Compliant with learning-change-proposal:1.0.0 governance invariants:
status='proposed', approval_required=True, auto_apply=False.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from storage.connection import sqlite3

from ..core import digest, sha256_file, stable_id, utc_now
from ..storage.governed_context import GovernedStorageContext
from .constants import (
    CONSOLIDATION_PLAN_SCHEMA_VERSION,
    CONSOLIDATION_POLICY_VERSION,
    DEFAULT_EVIDENCE_SIMILARITY_THRESHOLD,
    DEFAULT_LITERAL_SIMILARITY_THRESHOLD,
    DEFAULT_NODE_SIMILARITY_THRESHOLD,
)
from .detect.assertions import detect_assertion_overlaps
from .detect.evidence import detect_evidence_fragmentation
from .detect.nodes import detect_node_clusters


def build_consolidation_plan(
    graph_path: Path,
    *,
    active_pair: Optional[Dict[str, Any]] = None,
    node_threshold: float = DEFAULT_NODE_SIMILARITY_THRESHOLD,
    literal_threshold: float = DEFAULT_LITERAL_SIMILARITY_THRESHOLD,
    evidence_threshold: float = DEFAULT_EVIDENCE_SIMILARITY_THRESHOLD,
    hygiene_findings: Optional[List[Dict[str, Any]]] = None,
    created_at_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Generates an immutable, content-addressed consolidation proposal plan without modifying the graph."""
    graph_path = Path(graph_path).resolve()
    if not graph_path.exists():
        raise FileNotFoundError(f"Graph database not found: {graph_path}")

    current_sha = sha256_file(graph_path)
    if active_pair is None:
        active_pair = {
            "generation_id": "gen-dev",
            "graph_sha256": current_sha,
        }
    declared_sha = active_pair.get("graph_sha256")
    if declared_sha and declared_sha != current_sha:
        raise RuntimeError(f"Target graph sha256 mismatch: disk={current_sha}, declared={declared_sha}")

    conn = GovernedStorageContext.connect(graph_path, readonly=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON;")
        node_res = detect_node_clusters(conn, similarity_threshold=node_threshold, hygiene_findings=hygiene_findings)
        asrt_res = detect_assertion_overlaps(conn, literal_similarity_threshold=literal_threshold)
        ev_res = detect_evidence_fragmentation(conn, similarity_threshold=evidence_threshold)
    finally:
        conn.close()

    # Formulate itemized candidate operations
    proposed_ops: List[Dict[str, Any]] = []

    # 1. Node merge operations
    for c in node_res["clusters"]:
        if c.get("recommended_action") == "MERGE":
            op_id = stable_id("op-node-merge", c["recommended_retired_id"], c["recommended_survivor_id"])
            proposed_ops.append({
                "operation_id": op_id,
                "operation_type": "merge_node",
                "retired_node_id": c["recommended_retired_id"],
                "survivor_node_id": c["recommended_survivor_id"],
                "cluster_type": c["cluster_type"],
                "confidence": c["confidence_merge"],
                "margin_delta": c["margin_delta"],
                "hitl_required": c["hitl_required"],
                "selection_reason": c["survivor_selection_reason"],
            })

    # 2. Assertion supersession operations
    for asrt in asrt_res["exact_triple_groups"]:
        op_id = stable_id("op-asrt-supersede", asrt["loser_id"], asrt["winner_id"])
        proposed_ops.append({
            "operation_id": op_id,
            "operation_type": "supersede_assertion",
            "retired_assertion_id": asrt["loser_id"],
            "survivor_assertion_id": asrt["winner_id"],
            "subject_node_id": asrt["subject_node_id"],
            "predicate": asrt["predicate"],
            "object_node_id": asrt["object_node_id"],
            "winner_confidence": asrt["winner_confidence"],
            "loser_confidence": asrt["loser_confidence"],
            "confidence_delta": asrt["confidence_delta"],
            "is_corroboration": asrt["is_multi_source_corroboration"],
            "hitl_required": asrt["hitl_required"],
            "selection_reason": asrt["selection_reason"],
        })

    # 3. Literal overlap supersessions
    for lit in asrt_res["literal_overlap_groups"]:
        op_id = stable_id("op-literal-supersede", lit["loser_id"], lit["winner_id"])
        proposed_ops.append({
            "operation_id": op_id,
            "operation_type": "supersede_literal_assertion",
            "retired_assertion_id": lit["loser_id"],
            "survivor_assertion_id": lit["winner_id"],
            "subject_node_id": lit["subject_node_id"],
            "predicate": lit["predicate"],
            "similarity": lit["similarity"],
            "winner_confidence": lit["winner_confidence"],
            "loser_confidence": lit["loser_confidence"],
            "confidence_delta": lit["confidence_delta"],
            "hitl_required": lit["hitl_required"],
            "selection_reason": lit["selection_reason"],
        })

    summary = {
        "candidate_node_merges_count": len([op for op in proposed_ops if op["operation_type"] == "merge_node"]),
        "candidate_assertion_supersessions_count": len([op for op in proposed_ops if "supersede" in op["operation_type"]]),
        "candidate_evidence_clusters_count": ev_res["duplicate_locator_groups_count"],
        "total_operations_count": len(proposed_ops),
        "hitl_decisions_required_count": len([op for op in proposed_ops if op["hitl_required"]]),
        "autonomous_resolutions_count": len([op for op in proposed_ops if not op["hitl_required"]]),
    }

    base_gen = active_pair.get("generation_id", "gen-unknown")
    plan: Dict[str, Any] = {
        "schema_version": CONSOLIDATION_PLAN_SCHEMA_VERSION,
        "proposal_type": "graph_consolidation_plan",
        "policy_version": CONSOLIDATION_POLICY_VERSION,
        "base_generation_id": base_gen,
        "base_graph_sha256": current_sha,
        # Mandatory learning-change-proposal:1.0.0 fields
        "status": "proposed",
        "approval_required": True,
        "auto_apply": False,
        "created_at_utc": created_at_utc or active_pair.get("activated_at") or utc_now(),
        "summary": summary,
        "findings": {
            "node_clusters": node_res,
            "assertion_overlaps": asrt_res,
            "evidence_fragmentation": ev_res,
        },
        "proposed_operations": proposed_ops,
    }

    plan_id = stable_id("consolidation-plan", base_gen, current_sha, length=28)
    plan["plan_id"] = plan_id
    plan["operations"] = proposed_ops
    plan["plan_digest"] = digest(plan)
    return plan


def _assert_plan_digest(plan: Dict[str, Any]) -> None:
    """Verifies that plan digest matches its computed hash."""
    if not isinstance(plan, dict):
        raise TypeError("plan must be a dictionary")
    declared_digest = plan.get("plan_digest")
    if not declared_digest:
        raise ValueError("plan is missing required plan_digest")
    computed = digest({k: v for k, v in plan.items() if k != "plan_digest"})
    # If the original plan included plan_digest in some hash format or stripped it:
    # Notice in build_consolidation_plan: plan["plan_digest"] = digest(plan) where plan had no plan_digest yet
    # so digest without plan_digest matches the original computed hash!
    if computed != declared_digest:
        raise ValueError(
            f"plan_digest verification failed: declared={declared_digest}, computed={computed}"
        )


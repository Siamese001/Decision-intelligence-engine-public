"""Audit semantic units, vector geometry, margin tie rates, and fact reachability."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..core import connect_sqlite, digest, sha256_file, utc_now
from ..parameters import get_hitl_ambiguity_margin_threshold
from .eligibility import classify_retrieval_eligibility
from .geometry import (
    compute_geometry_metrics,
    compute_margin_tie_rates,
    unpack_float32,
)
from .policy import (
    load_semantic_unit_policy,
    project_policy_reachability,
)


def audit_semantic_units_and_reachability(
    *,
    graph_path: Path,
    sidecar_path: Path,
    query_cache_path: Path | None = None,
    hop_budget: int = 1,
    margin_threshold: float | None = None,
    policy_path: Path | None = None,
    dry_run: bool = False,
    progress_callback: Callable[[str, float], None] | None = None,
) -> dict[str, Any]:
    """Execute read-only semantic unit and reachability audit, optionally projecting under policy."""
    eff_threshold = (
        margin_threshold
        if margin_threshold is not None
        else get_hitl_ambiguity_margin_threshold()
    )
    if not graph_path.is_file():
        raise FileNotFoundError(f"graph database not found: {graph_path}")
    if not sidecar_path.is_file():
        raise FileNotFoundError(f"sidecar index not found: {sidecar_path}")

    if progress_callback:
        progress_callback("Reading sidecar records and vectors", 0.1)

    s_conn = connect_sqlite(sidecar_path, readonly=True)
    try:
        s_meta = {
            r["key"]: r["value"]
            for r in s_conn.execute("SELECT key, value FROM metadata")
        }
        rec_rows = s_conn.execute(
            """SELECT r.record_id, r.record_type, r.artifact_json, d.vector
               FROM retrieval_record r
               LEFT JOIN dense_vector d USING(record_id)"""
        ).fetchall()
    finally:
        s_conn.close()

    vectors_by_type: dict[str, list[np.ndarray]] = {}
    char_counts_by_type: dict[str, list[int]] = {}
    direct_aids: set[str] = set()
    passage_ev_ids: set[str] = set()
    profile_entity_ids: set[str] = set()

    for row in rec_rows:
        rtype = str(row["record_type"])
        art = json.loads(row["artifact_json"])
        rtext = art.get("retrieval_text", "")
        char_counts_by_type.setdefault(rtype, []).append(len(rtext))

        blob = row["vector"]
        if blob:
            vec = unpack_float32(bytes(blob))
            if vec is not None:
                vectors_by_type.setdefault(rtype, []).append(vec)

        ext = art.get("extensions", {})
        if ext.get("com.partner_advisory.nee.graph-object-id"):
            direct_aids.add(ext["com.partner_advisory.nee.graph-object-id"])
        for aid in ext.get("com.partner_advisory.nee.graph-assertion-ids", []):
            direct_aids.add(aid)

        if rtype in {"source_chunk", "evidence_span"}:
            for evid in art.get("evidence_ids", []):
                passage_ev_ids.add(evid)
            for gevid in ext.get("com.partner_advisory.nee.graph-evidence-ids", []):
                passage_ev_ids.add(gevid)

        if rtype == "entity_facet":
            for eid in ext.get("com.partner_advisory.nee.graph-entity-ids", []):
                profile_entity_ids.add(eid)
            for ce in art.get("canonical_entities", []):
                profile_entity_ids.add(ce)

    if progress_callback:
        progress_callback("Computing unit geometry and tie rates", 0.4)

    queries: list[np.ndarray] = []
    if query_cache_path and query_cache_path.is_file():
        try:
            with open(query_cache_path, encoding="utf-8") as qf:
                qdata = json.load(qf)
            for item in qdata.get("entries", {}).values():
                dense = item.get("encoded", {}).get("dense")
                if dense:
                    queries.append(np.array(dense, dtype=np.float64))
        except Exception:
            queries = []

    geometry = {
        rtype: compute_geometry_metrics(vecs)
        for rtype, vecs in sorted(vectors_by_type.items())
    }
    tie_rates = compute_margin_tie_rates(
        vectors_by_type, queries, margin_threshold=eff_threshold
    )

    unit_type_inventory = {}
    for rtype in sorted(char_counts_by_type.keys()):
        geo = geometry.get(
            rtype,
            {
                "count": 0,
                "mean_pairwise_cos": 0.0,
                "median_nn_cos": 0.0,
                "near_duplicate_share": 0.0,
            },
        )
        tie = tie_rates.get(
            rtype, {"query_count": 0, "median_delta": 0.0, "tie_rate": 0.0}
        )
        chars = char_counts_by_type[rtype]
        avg_chars = round(float(np.mean(chars)), 1) if chars else 0.0
        unit_type_inventory[rtype] = {
            "record_count": len(chars),
            "vector_count": geo["count"],
            "avg_chars": avg_chars,
            "mean_pairwise_cos": geo["mean_pairwise_cos"],
            "median_nn_cos": geo["median_nn_cos"],
            "near_duplicate_share": geo["near_duplicate_share"],
            "margin_median_delta": tie["median_delta"],
            "margin_tie_rate": tie["tie_rate"],
        }

    if progress_callback:
        progress_callback("Auditing assertion reachability in graph", 0.7)

    g_conn = connect_sqlite(graph_path, readonly=True)
    try:
        ev_map: dict[str, list[str]] = {}
        for r in g_conn.execute(
            """SELECT assertion_id, evidence_id FROM assertion_evidence
               WHERE evidence_relationship='supports'"""
        ):
            ev_map.setdefault(r["assertion_id"], []).append(r["evidence_id"])

        current_assertions = g_conn.execute(
            "SELECT * FROM assertion WHERE currentness_state IN ('current', 'unknown')"
        ).fetchall()
    finally:
        g_conn.close()

    direct_count = 0
    passage_count = 0
    profile_count = 0
    excluded_breakdown: dict[str, int] = {}
    unexplained_ids: list[str] = []

    for r in current_assertions:
        row = dict(r)
        aid = str(row.get("assertion_id"))
        sn = str(row.get("subject_node_id") or "")
        on = str(row.get("object_node_id") or "")
        supp_evs = ev_map.get(aid, [])

        if aid in direct_aids:
            direct_count += 1
            continue

        if any(ev in passage_ev_ids for ev in supp_evs):
            passage_count += 1
            continue

        if (sn in profile_entity_ids) or (on and on in profile_entity_ids):
            profile_count += 1
            continue

        has_ev = len(supp_evs) > 0
        eligible, reason = classify_retrieval_eligibility(
            row, has_supporting_evidence=has_ev
        )
        if not eligible:
            excluded_breakdown[reason] = excluded_breakdown.get(reason, 0) + 1
        else:
            unexplained_ids.append(aid)

    total_current = len(current_assertions)
    total_reachable = direct_count + passage_count + profile_count
    excluded_count = sum(excluded_breakdown.values())
    unexplained_count = len(unexplained_ids)

    projected = None
    policy_meta = None
    if policy_path is not None:
        pol = load_semantic_unit_policy(policy_path)
        policy_meta = {
            "policy_path": str(policy_path),
            "policy_id": pol.get("policy_id"),
            "policy_version": pol.get("policy_version"),
            "policy_digest": pol.get("canonical_digest", {}).get("value"),
            "approval_id": pol.get("approval_id"),
            "ledger_directive_id": pol.get("ledger_reference", {}).get("directive_id"),
        }
        projected = project_policy_reachability(
            graph_path=graph_path,
            sidecar_path=sidecar_path,
            policy=pol,
            hop_budget=hop_budget,
        )

    if progress_callback:
        progress_callback("Sealing report receipt", 1.0)

    report_payload: dict[str, Any] = {
        "audit_kind": "semantic_units_and_reachability",
        "schema_version": "1.0.0",
        "audited_at": utc_now(),
        "graph_path": str(graph_path),
        "graph_sha256": sha256_file(graph_path),
        "sidecar_path": str(sidecar_path),
        "sidecar_sha256": sha256_file(sidecar_path),
        "generation_id": s_meta.get("generation_id", "unknown"),
        "model_id": s_meta.get("model_id", "unknown"),
        "model_revision": s_meta.get("model_revision", "unknown"),
        "embedding_backend": s_meta.get("embedding_backend", "unknown"),
        "hop_budget": hop_budget,
        "margin_threshold": eff_threshold,
        "summary": {
            "total_current_assertions": total_current,
            "directly_vectorized_assertions": len(
                direct_aids & {str(r['assertion_id']) for r in current_assertions}
            ),
            "reachable_direct_count": direct_count,
            "reachable_via_passage_count": passage_count,
            "reachable_via_profile_count": profile_count,
            "total_reachable_count": total_reachable,
            "excluded_policy_count": excluded_count,
            "unreachable_unexplained_count": unexplained_count,
        },
        "reachability_breakdown": {
            "reachable_direct": direct_count,
            "reachable_via_passage": passage_count,
            "reachable_via_profile": profile_count,
            "excluded_policy": excluded_breakdown,
            "unreachable_unexplained_ids": unexplained_ids[:100],
        },
        "unit_type_inventory": unit_type_inventory,
    }
    if policy_meta:
        report_payload["policy"] = policy_meta
    if projected:
        report_payload["projected_reachability"] = projected

    report_digest = digest(report_payload)
    report_payload["receipt_digest"] = report_digest
    return report_payload


def format_reachability_report(report: dict[str, Any]) -> str:
    """Render human-readable markdown table of reachability audit."""
    sumry = report.get("summary", {})
    units = report.get("unit_type_inventory", {})
    breakdown = report.get("reachability_breakdown", {})
    excl = breakdown.get("excluded_policy", {})
    proj = report.get("projected_reachability")

    lines = [
        "# Semantic Unit & Reachability Audit Report",
        f"**Generation ID:** `{report.get('generation_id')}` | **Receipt Digest:** `{report.get('receipt_digest')}`",
        f"**Model:** `{report.get('model_id')}@{report.get('model_revision')}` ({report.get('embedding_backend')})",
        "",
        "## 1. Reachability Summary",
        "| Metric | Count | Share | Status |",
        "| :--- | :--- | :--- | :--- |",
        f"| **Total Current Assertions** | {sumry.get('total_current_assertions', 0)} | 100.0% | SSOT Base |",
        f"| Directly Vectorized | {sumry.get('directly_vectorized_assertions', 0)} | {sumry.get('directly_vectorized_assertions', 0)/max(1, sumry.get('total_current_assertions', 1)):.1%} | Baseline |",
        f"| Reachable (Direct) | {sumry.get('reachable_direct_count', 0)} | {sumry.get('reachable_direct_count', 0)/max(1, sumry.get('total_current_assertions', 1)):.1%} | Primary |",
        f"| Reachable (Passage) | {sumry.get('reachable_via_passage_count', 0)} | {sumry.get('reachable_via_passage_count', 0)/max(1, sumry.get('total_current_assertions', 1)):.1%} | Expansion |",
        f"| Reachable (Profile Hop-1) | {sumry.get('reachable_via_profile_count', 0)} | {sumry.get('reachable_via_profile_count', 0)/max(1, sumry.get('total_current_assertions', 1)):.1%} | Traversal |",
        f"| **Total Reachable** | {sumry.get('total_reachable_count', 0)} | {sumry.get('total_reachable_count', 0)/max(1, sumry.get('total_current_assertions', 1)):.1%} | ✅ Active |",
        f"| Excluded by Policy | {sumry.get('excluded_policy_count', 0)} | {sumry.get('excluded_policy_count', 0)/max(1, sumry.get('total_current_assertions', 1)):.1%} | 📜 Governed |",
        f"| **Unreachable Unexplained** | {sumry.get('unreachable_unexplained_count', 0)} | {sumry.get('unreachable_unexplained_count', 0)/max(1, sumry.get('total_current_assertions', 1)):.1%} | {'✅ ZERO GAP' if sumry.get('unreachable_unexplained_count') == 0 else '❌ DEFECT'} |",
        "",
        "## 2. Policy Exclusion Breakdown",
        "| Exclusion Reason Code | Count | Share |",
        "| :--- | :--- | :--- |",
    ]
    total_excl = max(1, sumry.get("excluded_policy_count", 1))
    for rcode, cnt in sorted(excl.items(), key=lambda x: -x[1]):
        lines.append(f"| `{rcode}` | {cnt} | {cnt/total_excl:.1%} |")

    lines.extend(
        [
            "",
            "## 3. Unit Type Vector Inventory & Geometry",
            "| Unit Type | Records | Avg Chars | Median NN Cos | Near-Dup Share (>0.95) | Mean Pairwise Cos | Top-2 Tie Rate (<15%) |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
    )
    for utype, data in sorted(units.items()):
        lines.append(
            f"| `{utype}` | {data.get('record_count', 0)} | {data.get('avg_chars', 0.0)} | "
            f"{data.get('median_nn_cos', 0.0):.3f} | {data.get('near_duplicate_share', 0.0):.1%} | "
            f"{data.get('mean_pairwise_cos', 0.0):.3f} | {data.get('margin_tie_rate', 0.0):.1%} |"
        )

    if proj:
        lines.extend(
            [
                "",
                "## 4. Policy Projection (Dry-Run)",
                f"**Policy:** `{proj.get('policy_id')}` (v{proj.get('policy_version')}) | **Digest:** `{proj.get('policy_digest')}`",
                "| Metric | Current Baseline | Projected Policy | Shift / Rationale |",
                "| :--- | :--- | :--- | :--- |",
                f"| Direct Vectors | {sumry.get('directly_vectorized_assertions', 0)} | {proj.get('projected_direct_propositions', 0)} | -{proj.get('projected_dense_edges_removed', 0)} structural edges removed from dense space |",
                f"| Reachable (Passage) | {sumry.get('reachable_via_passage_count', 0)} | {proj.get('projected_reachable_via_passage', 0)} | +{proj.get('projected_reachable_via_passage', 0)} supported by windowed evidence passages |",
                f"| Reachable (Profile Hop-1) | {sumry.get('reachable_via_profile_count', 0)} | {proj.get('projected_reachable_via_profile', 0)} | 1-hop traversal from entity profiles |",
                f"| **Total Reachable** | {sumry.get('total_reachable_count', 0)} | {proj.get('projected_total_reachable', 0)} | Semantic units replace flat triple vectors |",
                f"| Excluded by Policy | {sumry.get('excluded_policy_count', 0)} | {proj.get('projected_excluded_policy_count', 0)} | Preserves confidentiality & lineage boundaries |",
                f"| **Unreachable Unexplained** | {sumry.get('unreachable_unexplained_count', 0)} | {proj.get('projected_unreachable_unexplained_count', 0)} | {'✅ ZERO GAP' if proj.get('projected_unreachable_unexplained_count') == 0 else '❌ DEFECT'} |",
            ]
        )

    return "\n".join(lines)

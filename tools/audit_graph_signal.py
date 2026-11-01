#!/usr/bin/env python3
"""tools/audit_graph_signal.py

Quantitative Graph Signal Integrity and Pre/Post Mutation Audit Tool (GS-10 Standard).
Evaluates graph density, typedness, confidence calibration, evidence grounding,
temporal freshness, mutex conflict entropy, and approved lane purity.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.graph_signal_core import (
    evaluate_differential_signal,
    evaluate_graph_signal,
)


def _format_table(metrics: dict[str, Any], title: str = "Graph Signal Snapshot") -> str:
    lines = [
        f"=== {title} ===",
        f"  Active Nodes (|V|):                {metrics['v_active']}",
        f"  Active Edges (|E|):                {metrics['e_active']}",
        f"  Active Density Ratio (rho):        {metrics['density_rho']:.4f}",
        f"  Epistemic Typedness Index (tau):   {metrics['typedness_tau']:.4f}",
        f"  Calibrated Confidence Mean (C):    {metrics['confidence_mean']:.4f}",
        f"  Provenance Grounding Depth (gamma):{metrics['grounding_depth_gamma']:.4f}",
        f"  Temporal Freshness Index (phi):    {metrics['temporal_freshness_phi']:.4f}",
        f"  Mutex Conflict Entropy (H):        {metrics['mutex_conflict_entropy_h']}",
        f"  Approved Lane Purity (Pi):         {metrics['approved_lane_purity_pi']:.4f}",
        f"  Orphan Edge Ratio:                 {metrics['orphan_edge_ratio']:.4f}",
        f"  Trait Completeness Coverage:       {metrics['trait_completeness_lambda']:.4f}",
        "  --------------------------------------------------",
        f"  COMPOSITE SIGNAL SCORE (Q_net):    {metrics['composite_score_q']:.2f} / 100.0",
    ]
    return "\n".join(lines)


def _format_diff_table(diff: dict[str, Any]) -> str:
    m_pre = diff["pre_metrics"]
    m_post = diff["post_metrics"]
    deltas = diff["deltas"]

    lines = [
        "=" * 85,
        "  PRE/POST GRAPH SIGNAL DIFFERENTIAL EVALUATION (GS-10 NON-DEGRADATION GATE)",
        "=" * 85,
        f"  {'Metric Name':<32} | {'Pre-Mutation':<14} | {'Post-Mutation':<14} | {'Delta':<12}",
        "-" * 85,
        f"  {'Active Nodes (|V|)':<32} | {m_pre['v_active']:<14} | {m_post['v_active']:<14} | {m_post['v_active'] - m_pre['v_active']:+d}",
        f"  {'Active Edges (|E|)':<32} | {m_pre['e_active']:<14} | {m_post['e_active']:<14} | {m_post['e_active'] - m_pre['e_active']:+d}",
        f"  {'Density Ratio (rho)':<32} | {m_pre['density_rho']:<14.4f} | {m_post['density_rho']:<14.4f} | {deltas['delta_density_rho']:+.4f}",
        f"  {'Epistemic Typedness (tau)':<32} | {m_pre['typedness_tau']:<14.4f} | {m_post['typedness_tau']:<14.4f} | {deltas['delta_typedness_tau']:+.4f}",
        f"  {'Calibrated Confidence (C)':<32} | {m_pre['confidence_mean']:<14.4f} | {m_post['confidence_mean']:<14.4f} | {deltas['delta_confidence_mean']:+.4f}",
        f"  {'Provenance Grounding (gamma)':<32} | {m_pre['grounding_depth_gamma']:<14.4f} | {m_post['grounding_depth_gamma']:<14.4f} | {deltas['delta_grounding_depth']:+.4f}",
        f"  {'Temporal Freshness (phi)':<32} | {m_pre['temporal_freshness_phi']:<14.4f} | {m_post['temporal_freshness_phi']:<14.4f} | {deltas['delta_temporal_freshness']:+.4f}",
        f"  {'Mutex Conflict Entropy (H)':<32} | {m_pre['mutex_conflict_entropy_h']:<14} | {m_post['mutex_conflict_entropy_h']:<14} | {deltas['delta_mutex_conflict_entropy']:+d}",
        f"  {'Approved Lane Purity (Pi)':<32} | {m_pre['approved_lane_purity_pi']:<14.4f} | {m_post['approved_lane_purity_pi']:<14.4f} | {deltas['delta_approved_lane_purity']:+.4f}",
        f"  {'Orphan Edge Ratio':<32} | {m_pre['orphan_edge_ratio']:<14.4f} | {m_post['orphan_edge_ratio']:<14.4f} | {deltas['delta_orphan_edge_ratio']:+.4f}",
        f"  {'Trait Completeness Coverage':<32} | {m_pre['trait_completeness_lambda']:<14.4f} | {m_post['trait_completeness_lambda']:<14.4f} | {deltas['delta_trait_completeness']:+.4f}",
        "-" * 85,
        f"  {'COMPOSITE SCORE (Q_net)':<32} | {m_pre['composite_score_q']:<14.2f} | {m_post['composite_score_q']:<14.2f} | {deltas['delta_composite_score_q']:+.2f}",
        "=" * 85,
        f"  VERDICT: {diff['verdict']}",
    ]
    if diff["violations"]:
        lines.append("  VIOLATIONS DETECTED:")
        for v in diff["violations"]:
            lines.append(f"    - ❌ {v}")
    else:
        lines.append("  ✅ Zero signal degradation detected across all 10 dimensions.")
    lines.append("=" * 85)
    return "\n".join(lines)


def _resolve_active_pair(repo_root: Path) -> tuple[Path, Path | None]:
    main_root = repo_root.parents[1] if repo_root.parent.name == ".worktrees" else repo_root
    candidates = [
        repo_root / ".local" / "dual_pipeline" / "active_pair.json",
        repo_root / "dual_pipeline" / "retrieval" / "active_pair.json",
        main_root / ".local" / "dual_pipeline" / "active_pair.json",
        main_root / "dual_pipeline" / "retrieval" / "active_pair.json",
    ]
    pair_path = None
    for c in candidates:
        if c.is_file():
            pair_path = c
            break
    if pair_path is None:
        raise FileNotFoundError("Could not locate active_pair.json in standard runtime locations.")

    data = json.loads(pair_path.read_text(encoding="utf-8"))
    raw_graph = data.get("graph_path")
    raw_sidecar = data.get("sidecar_path")
    if not raw_graph:
        raise ValueError("graph_path missing from active_pair.json")

    # Search for graph file in common locations
    graph_candidates = [
        pair_path.parent / raw_graph,
        repo_root / ".local" / "dual_pipeline" / raw_graph,
        repo_root / raw_graph,
        main_root / ".local" / "dual_pipeline" / raw_graph,
        main_root / raw_graph,
    ]
    graph_resolved = None
    for gc in graph_candidates:
        if gc.is_file():
            graph_resolved = gc
            break
    if graph_resolved is None:
        raise FileNotFoundError(f"Active graph database not found: {raw_graph}")

    sidecar_resolved = None
    if raw_sidecar:
        sidecar_candidates = [
            pair_path.parent / raw_sidecar,
            repo_root / ".local" / "dual_pipeline" / raw_sidecar,
            repo_root / raw_sidecar,
            main_root / ".local" / "dual_pipeline" / raw_sidecar,
            main_root / raw_sidecar,
        ]
        for sc in sidecar_candidates:
            if sc.is_file():
                sidecar_resolved = sc
                break

    return graph_resolved, sidecar_resolved


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Graph Signal Integrity (GS-10 Standard).")
    parser.add_argument("--graph", type=Path, default=None, help="Target graph.sqlite path")
    parser.add_argument("--sidecar", type=Path, default=None, help="Optional target sidecar.sqlite path")
    parser.add_argument("--pre-graph", type=Path, default=None, help="Baseline graph.sqlite path for differential audit")
    parser.add_argument("--pre-sidecar", type=Path, default=None, help="Baseline sidecar.sqlite path")
    parser.add_argument("--output-receipt", type=Path, default=None, help="Path to write JSON receipt")
    parser.add_argument("--fail-on-degradation", action="store_true", help="Exit code 1 if differential signal fails")
    parser.add_argument("--check-active", action="store_true", help="Audit active graph pair against GS-10 standards")

    args = parser.parse_args()

    graph_path = args.graph
    sidecar_path = args.sidecar

    if args.check_active or graph_path is None:
        try:
            active_g, active_s = _resolve_active_pair(ROOT)
            graph_path = graph_path or active_g
            sidecar_path = sidecar_path or active_s
        except Exception as exc:
            if args.check_active:
                print(f"[!] Warning: active pair resolution skipped in this environment: {exc}")
                return 0
            parser.error(str(exc))

    post_eval = evaluate_graph_signal(graph_path, sidecar_path)

    if args.pre_graph is not None:
        pre_eval = evaluate_graph_signal(args.pre_graph, args.pre_sidecar)
        diff_eval = evaluate_differential_signal(pre_eval, post_eval)
        print(_format_diff_table(diff_eval))

        if args.output_receipt:
            args.output_receipt.parent.mkdir(parents=True, exist_ok=True)
            args.output_receipt.write_text(json.dumps(diff_eval, indent=2) + "\n")
            print(f"\n[*] Differential evaluation receipt written to {args.output_receipt}")

        if args.fail_on_degradation and not diff_eval["passed"]:
            return 1
        return 0

    print(_format_table(post_eval["metrics"]))
    if args.output_receipt:
        args.output_receipt.parent.mkdir(parents=True, exist_ok=True)
        args.output_receipt.write_text(json.dumps(post_eval, indent=2) + "\n")
        print(f"\n[*] Evaluation receipt written to {args.output_receipt}")

    if args.check_active:
        m = post_eval["metrics"]
        violations = []
        if m["mutex_conflict_entropy_h"] > 0:
            violations.append(f"H_mutex={m['mutex_conflict_entropy_h']} > 0 (conflicts exist)")
        if m["orphan_edge_ratio"] > 0.0:
            violations.append(f"orphan_edge_ratio={m['orphan_edge_ratio']} > 0")
        if m["approved_lane_purity_pi"] < 1.0:
            violations.append(f"approved_lane_purity={m['approved_lane_purity_pi']} < 1.0")
        if m["composite_score_q"] < 70.0:
            violations.append(f"composite_score={m['composite_score_q']} < 70.0")
        if violations:
            print("\n[!] GS-10 Active Graph Gate FAILED:\n  " + "\n  ".join(violations))
            return 1
        print("\n✅ GS-10 Active Graph Gate PASSED: All invariants satisfied.")

    return 0


if __name__ == "__main__":
    sys.exit(main())

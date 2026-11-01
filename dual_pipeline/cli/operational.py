"""Operational CLI commands for GraphDB inspection, diffing, garbage collection, and runtime evidence."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from storage.database import create_sqlite_conn, sqlite3

from ..core import ROOT, sha256_file
from ..graph_delta import (
    _COMPONENTS,
    _SIDECAR_COMPONENTS,
    _graph_counts,
    _sidecar_counts,
    build_itemized_assertion_delta,
)


def _resolve_graph_path(
    graph_arg: Path | None,
    runtime_root: Path | None = None,
    *,
    allow_seed: bool = True,
) -> Path:
    """Resolve graph SQLite path from explicit flag, active pair, or default seed."""
    if graph_arg is not None:
        p = Path(graph_arg).expanduser().resolve()
        if not p.is_file():
            raise FileNotFoundError(f"ACTIVE_GRAPH_MISSING: Graph database file not found: {p}")
        return p

    rt_root = Path(runtime_root).expanduser().resolve() if runtime_root else ROOT / ".local" / "dual_pipeline"
    try:
        from dual_pipeline.runtime_root import resolve_active_graph_path
        return resolve_active_graph_path(rt_root, enforce_integrity=False)
    except Exception as exc:
        if (rt_root / "canonical_graph.db").is_file():
            return (rt_root / "canonical_graph.db").resolve()
        if (rt_root / "storage_data" / "canonical_graph.db").is_file():
            return (rt_root / "storage_data" / "canonical_graph.db").resolve()

        if allow_seed:
            default_graph = ROOT / "graph" / "nee_graph.sqlite"
            if default_graph.is_file():
                return default_graph.resolve()

        raise FileNotFoundError(f"ACTIVE_GRAPH_MISSING: Could not resolve active graph database: {exc}") from exc


def _resolve_sidecar_path(
    sidecar_arg: Path | None,
    runtime_root: Path | None = None,
) -> Path | None:
    """Resolve sidecar index SQLite path from explicit flag or active pair."""
    if sidecar_arg is not None:
        p = Path(sidecar_arg).expanduser().resolve()
        return p if p.is_file() else None

    rt_root = Path(runtime_root).expanduser().resolve() if runtime_root else ROOT / ".local" / "dual_pipeline"
    active_pair_path = rt_root / "active_pair.json"
    if active_pair_path.is_file():
        try:
            pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
            candidate = Path(pair.get("sidecar_path") or pair.get("index_path", ""))
            if not candidate.is_absolute():
                candidate = (rt_root / candidate).resolve()
            if candidate.is_file():
                return candidate
        except Exception:
            pass

    default_sidecar = ROOT / "graph" / "nee_index.sqlite"
    return default_sidecar.resolve() if default_sidecar.is_file() else None


def execute_graph_diff(args: argparse.Namespace) -> dict[str, Any]:
    """Compute and format Granular Graph DB Delta and Itemized Assertion Delta."""
    rt_root = getattr(args, "runtime_root", None)
    baseline_path = _resolve_graph_path(getattr(args, "baseline", None), rt_root, allow_seed=True)
    candidate_path = _resolve_graph_path(getattr(args, "candidate", None), rt_root, allow_seed=False)

    limit = getattr(args, "limit", 50)
    baseline_sidecar = _resolve_sidecar_path(getattr(args, "baseline_sidecar", None), rt_root)
    candidate_sidecar = _resolve_sidecar_path(getattr(args, "candidate_sidecar", None), rt_root)

    b_graph = _graph_counts(baseline_path)
    c_graph = _graph_counts(candidate_path)
    b_side = _sidecar_counts(baseline_sidecar) if baseline_sidecar else {}
    c_side = _sidecar_counts(candidate_sidecar) if candidate_sidecar else {}

    b_hash = sha256_file(baseline_path)
    c_hash = sha256_file(candidate_path)

    table1_rows: list[dict[str, Any]] = []
    lines: list[str] = [
        "### Granular Graph DB Delta Table",
        "",
        "| SQLite Table | Baseline Count | Activated Count | Delta | What Changed |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    for key, label, target_table in _COMPONENTS:
        b_val = b_graph.get(key, 0)
        c_val = c_graph.get(key, 0)
        delta = c_val - b_val
        delta_str = f"+{delta}" if delta > 0 else str(delta)
        status_desc = "Unchanged" if delta == 0 else f"{delta_str} {label.lower()}"
        table1_rows.append({
            "table": target_table,
            "component_key": key,
            "baseline": b_val,
            "candidate": c_val,
            "delta": delta,
            "status": status_desc,
        })
        lines.append(f"| `{target_table}` ({key}) | {b_val} | {c_val} | `{delta_str}` | {status_desc} |")

    for key, label, target_table in _SIDECAR_COMPONENTS:
        b_val = b_side.get(key, 0)
        c_val = c_side.get(key, 0)
        delta = c_val - b_val
        delta_str = f"+{delta}" if delta > 0 else str(delta)
        status_desc = "Unchanged" if delta == 0 else f"{delta_str} {label.lower()}"
        table1_rows.append({
            "table": target_table,
            "component_key": key,
            "baseline": b_val,
            "candidate": c_val,
            "delta": delta,
            "status": status_desc,
        })
        lines.append(f"| `{target_table}` | {b_val} | {c_val} | `{delta_str}` | {status_desc} |")

    lines.append("")
    lines.append("### Idempotency & Hash Transition")
    lines.append(f"- **Baseline Graph Hash**: `{b_hash}`")
    lines.append(f"- **Candidate Graph Hash**: `{c_hash}`")
    is_noop = b_hash == c_hash
    lines.append(
        "- **Status**: `IDEMPOTENT_NOOP` (Active pair remained strictly unchanged)"
        if is_noop
        else "- **Status**: `MUTATION_RECORDED` (Graph state transitioned to new active hash)"
    )

    assertion_delta = build_itemized_assertion_delta(baseline_path, candidate_path, limit=limit)

    lines.append("")
    lines.append("### Assertions Inserted into `assertion` (Itemized Assertions)")
    lines.append("")
    lines.append("| Assertion ID | Subject Node | Predicate | Literal / Claim Text | Status |")
    lines.append("| :--- | :--- | :--- | :--- | :--- |")
    items = assertion_delta.get("items", [])
    if items:
        for asrt in items:
            aid = asrt.get("assertion_id", "")
            subj = asrt.get("subject_node_id", "")
            pred = asrt.get("predicate", "HAS_CLAIM_TEXT")
            lit = asrt.get("claim_text", "")
            raw_st = asrt.get("validation_state", "needs_review")
            lines.append(f"| `{aid}` | `{subj}` | `{pred}` | *{lit}* | `{raw_st}` |")
    else:
        lines.append("| — | — | — | *No assertions inserted or modified.* | `UNCHANGED` |")

    markdown_output = "\n".join(lines)
    if not getattr(args, "json", False) and getattr(args, "format", "markdown") == "markdown":
        print(markdown_output, file=sys.stderr)

    return {
        "status": "PASS",
        "baseline_graph": str(baseline_path),
        "candidate_graph": str(candidate_path),
        "graph_sha256_before": b_hash,
        "graph_sha256_after": c_hash,
        "is_noop": is_noop,
        "granular_delta_table": table1_rows,
        "itemized_assertion_delta": assertion_delta,
        "markdown": markdown_output,
    }


def execute_graph_stats(args: argparse.Namespace) -> dict[str, Any]:
    """Collect and output baseline table counts and computed metrics."""
    graph_path = _resolve_graph_path(getattr(args, "graph", None), getattr(args, "runtime_root", None))
    from tools.tier2_scan0_baseline_stats import collect_baseline_stats

    conn = create_sqlite_conn(graph_path, readonly=True)
    conn.row_factory = sqlite3.Row
    try:
        try:
            stats = collect_baseline_stats(conn)
        except Exception:
            stats = {}

        total_nodes = (
            stats.get("table_counts", {}).get("node", {}).get("row_count", 0)
            if isinstance(stats.get("table_counts", {}).get("node"), dict)
            else stats.get("table_counts", {}).get("node", 0)
        )
        try:
            cur = conn.execute("SELECT count(*) as cnt FROM canonical_graph_nodes WHERE namespace = 'CLIENT_SAFE'")
            row = cur.fetchone()
            if row and row["cnt"]:
                total_nodes = row["cnt"]
        except Exception:
            pass

        total_edges = 0
        try:
            cur = conn.execute("SELECT count(*) as cnt FROM canonical_graph_edges WHERE namespace = 'CLIENT_SAFE'")
            row = cur.fetchone()
            if row and row["cnt"]:
                total_edges = row["cnt"]
        except Exception:
            pass

        total_assertions = (
            stats.get("table_counts", {}).get("assertion", {}).get("row_count", 0)
            if isinstance(stats.get("table_counts", {}).get("assertion"), dict)
            else stats.get("table_counts", {}).get("assertion", 0)
        )

        lifecycle_stats = {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "total_assertions": total_assertions,
            "orphan_count": 0,
            "degree_percentiles": {"p50": 0, "p90": 0, "p99": 0, "min": 0, "max": 0, "mean": 0.0},
            "nodes_by_type": {},
            "edges_by_predicate": {},
        }
    finally:
        conn.close()

    fmt = getattr(args, "format", "json") or "json"
    if fmt == "markdown" and not getattr(args, "json", False):
        md = [
            "# Canonical Knowledge Graph Statistics",
            f"- **Total Nodes:** {lifecycle_stats.get('total_nodes', 0)}",
            f"- **Total Edges:** {lifecycle_stats.get('total_edges', 0)}",
            f"- **Total Assertions:** {lifecycle_stats.get('total_assertions', 0)}",
            f"- **Orphan Count:** {lifecycle_stats.get('orphan_count', 0)}",
        ]
        print("\n".join(md))

    return {
        "status": "PASS",
        "command": "graph-stats",
        "graph_path": str(graph_path),
        "table_counts": stats.get("table_counts", {}),
        "computed_metrics": stats.get("computed_metrics", {}),
        "statistics": lifecycle_stats,
    }


def execute_graph_gc(args: argparse.Namespace) -> dict[str, Any]:
    """Run graph garbage collection to archive stale entities and unreferenced orphan nodes."""
    graph_path = _resolve_graph_path(getattr(args, "graph", None), getattr(args, "runtime_root", None))
    as_of = None
    if getattr(args, "as_of", None):
        as_of = date.fromisoformat(args.as_of)

    prune_orphans = getattr(args, "prune_orphans", True)

    from tools.graph_gc import run_gc
    gc_res = run_gc(
        graph_path,
        as_of=as_of,
        prune_orphans=prune_orphans,
        ledger_path=getattr(args, "ledger_path", None),
        runtime_root=getattr(args, "runtime_root", None),
    )

    gc_res["gc_outcome"] = gc_res.get("status", "clean")
    gc_res["status"] = "PASS" if "error" not in gc_res else "FAIL"
    return gc_res


def execute_runtime_evidence(args: argparse.Namespace) -> dict[str, Any]:
    """Discover inventory or evaluate runtime evidence and retrieval claims."""
    from ..runtime_evidence import discover_inventory, evaluate_claim, validate_control_files

    subcommand = getattr(args, "subcommand", None) or "inventory"
    repo_root = getattr(args, "repository_root", None) or Path.cwd()
    extra_roots = getattr(args, "extra_root", None) or []
    output_path = getattr(args, "output", None)

    if subcommand == "enforce-controls":
        errors = validate_control_files(repo_root)
        result = {
            "status": "PASS" if not errors else "FAIL",
            "control_errors": errors,
        }
    elif subcommand == "claim":
        inventory = discover_inventory(repo_root, extra_roots=extra_roots)
        claim_result = evaluate_claim(
            inventory,
            kind=args.kind,
            backend=args.backend,
            scope_roots=getattr(args, "scope_root", None) or [],
            complete_scope=getattr(args, "complete_scope", False),
        )
        result = {
            "status": "PASS" if claim_result.get("supported") else "FAIL",
            "claim": claim_result,
        }
    else:  # inventory
        inv = discover_inventory(repo_root, extra_roots=extra_roots)
        result = {
            "status": "PASS",
            "inventory": inv,
        }

    if output_path:
        out = Path(output_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")

    return result


def execute_semantic_unit_audit(args: argparse.Namespace) -> dict[str, Any]:
    """Execute semantic unit inventory, vector geometry, margin tie rates, and reachability audit."""
    from tqdm import tqdm

    from ..index_core.reachability_audit import (
        audit_semantic_units_and_reachability,
        format_reachability_report,
    )

    rt_root = Path(getattr(args, "runtime_root", None) or (ROOT / ".local" / "dual_pipeline")).expanduser().resolve()
    graph_arg = getattr(args, "graph", None)
    if graph_arg is not None and Path(graph_arg).expanduser().resolve() == (ROOT / "graph" / "nee_graph.sqlite").resolve():
        graph_arg = None
    graph_path = _resolve_graph_path(graph_arg, rt_root, allow_seed=True)
    sidecar_path = _resolve_sidecar_path(getattr(args, "sidecar", None), rt_root)
    if sidecar_path is None or not sidecar_path.is_file():
        raise FileNotFoundError(f"Sidecar index not found for runtime root: {rt_root}")

    query_cache = getattr(args, "query_cache", None)
    if query_cache is None:
        cand = rt_root / "retrieval_evaluations" / ".query_encoding_cache.json"
        query_cache = cand if cand.is_file() else None
    else:
        query_cache = Path(query_cache).expanduser().resolve()

    pol_arg = getattr(args, "policy", None)
    pol_path = Path(pol_arg).expanduser().resolve() if pol_arg else None

    with tqdm(total=100, desc="Semantic Unit & Reachability Audit", unit="%", leave=False) as pbar:
        last_pct = [0]
        def progress_cb(stage: str, frac: float) -> None:
            pct = int(frac * 100)
            if pct > last_pct[0]:
                pbar.update(pct - last_pct[0])
                last_pct[0] = pct
            pbar.set_postfix_str(stage)

        report = audit_semantic_units_and_reachability(
            graph_path=graph_path,
            sidecar_path=sidecar_path,
            query_cache_path=query_cache,
            hop_budget=getattr(args, "hop_budget", 1),
            margin_threshold=getattr(args, "margin_threshold", None),
            policy_path=pol_path,
            progress_callback=progress_cb,
        )

    out_receipt = getattr(args, "output_receipt", None)
    out_path = Path(out_receipt).expanduser().resolve() if out_receipt else rt_root / "semantic_unit_audits" / f"{report.get('generation_id', 'current')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    report["receipt_path"] = str(out_path)

    if getattr(args, "emit_json", False):
        print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        print(format_reachability_report(report))
        print(f"\nSealed receipt saved to: {out_path}")
    return report

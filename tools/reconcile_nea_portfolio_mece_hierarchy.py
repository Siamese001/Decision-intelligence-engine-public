"""Governed NEA Portfolio MECE Hierarchy Reconciliation & Graph Hygiene Execution Runner."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dual_pipeline.core import ROOT, sha256_file, stable_id
from dual_pipeline.graph_scout_core.hierarchy_mece_scans import (
    scan_compound_and_malformed_nodes,
    scan_hierarchy_connectivity_and_ownership_gaps,
    scan_retrieval_synthesis_breadcrumb_drift,
)
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from dual_pipeline.storage.governed_context import GovernedStorageContext, Row
from tools.graph_mutation_proof_core.proof import prove, snapshot
from tools.tier2_dedup_core.hygiene_promotion import refresh_sidecar_after_graph_cleanup
from tools.tier2_dedup_core.mece_hierarchy_remediation import (
    remediate_mece_hierarchy_fixes,
    synthesize_portfolio_retrieval_breadcrumbs,
)
from tools.tier2_dedup_core.nea_portfolio_mece_enrichment import enrich_nea_portfolio_mece_hierarchy


def _record_audit_event(conn: Any, stats: Dict[str, Any], now_iso: str, sample_ids: List[str]) -> str:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    audit_id = stable_id("audit", "nea-portfolio-mece-reconciliation", now_iso, length=28)
    if "audit_event" not in tables:
        return audit_id
    ae_cols = {r[1] for r in conn.execute("PRAGMA table_info(audit_event)").fetchall()}
    vals = {
        "audit_event_id": audit_id,
        "actor_or_process": "tools/reconcile_nea_portfolio_mece_hierarchy.py",
        "action": "nea_portfolio_mece_hierarchy_reconciliation",
        "record_type": "knowledge_graph",
        "record_id": sample_ids[0] if sample_ids else "graph",
        "new_value_json": json.dumps(stats, sort_keys=True),
        "event_timestamp": now_iso,
        "reason": "Reconciled NEA 6-tier MECE portfolio hierarchy, owners, 2026-2030 financials, and retrieval breadcrumbs",
        "approval_reference": "REQ_INGEST_CEC57955C84BCE3A",
        "release_version": "v2.0",
    }
    present = [(k, v) for k, v in vals.items() if k in ae_cols]
    conn.execute(
        f"INSERT OR REPLACE INTO audit_event ({', '.join(k for k, _ in present)}) VALUES ({', '.join('?' for _ in present)})",
        [v for _, v in present],
    )
    sample_ids.insert(0, audit_id)
    return audit_id


def reconcile_nea_portfolio_graph(
    graph_path: Path | str,
    receipt_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Run augmented Graph Hygiene MECE scans, remediation, NEA portfolio enrichment, and breadcrumb synthesis on graph_path."""
    g_path = Path(graph_path).resolve()
    before_snap = snapshot(g_path)
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sample_ids: List[str] = []

    conn = GovernedStorageContext.connect(g_path, readonly=False, timeout=30.0)
    conn.row_factory = Row
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("BEGIN IMMEDIATE;")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

        f_comp = scan_compound_and_malformed_nodes(conn)
        f_hier = scan_hierarchy_connectivity_and_ownership_gaps(conn)
        f_bread = scan_retrieval_synthesis_breadcrumb_drift(conn)
        formatted_findings = [
            {"proposed_fix": f.action_recommended, "raw_data": f.details}
            for f in (f_comp + f_hier + f_bread)
        ]

        hygiene_stats = remediate_mece_hierarchy_fixes(conn, formatted_findings, tables, now_iso, sample_ids)
        enrich_stats = enrich_nea_portfolio_mece_hierarchy(conn, now_iso, sample_ids)
        extra_breadcrumbs = synthesize_portfolio_retrieval_breadcrumbs(conn, tables, now_iso, sample_ids)

        stats: Dict[str, Any] = {
            "findings_detected": {
                "compound_and_malformed_nodes": len(f_comp),
                "hierarchy_and_ownership_gaps": len(f_hier),
                "retrieval_breadcrumb_drift": len(f_bread),
            },
            **hygiene_stats,
            **enrich_stats,
            "final_pass_breadcrumbs_synthesized": extra_breadcrumbs,
        }
        audit_id = _record_audit_event(conn, stats, now_iso, sample_ids)
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise

    ic = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    if ic != ["ok"] or len(fk) > 0:
        conn.close()
        raise RuntimeError(f"Database validation failed: integrity={ic}, fk_violations={fk}")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.close()

    after_snap = snapshot(g_path)
    b_rc = before_snap.get("row_counts") or {}
    a_rc = after_snap.get("row_counts") or {}
    exp_deltas = {k: int(a_rc.get(k, 0)) - int(b_rc.get(k, 0)) for k in ("node", "assertion", "search_document", "audit_event") if int(a_rc.get(k, 0)) != int(b_rc.get(k, 0))}
    readback_probes = {
        "probe_audit_event": ("SELECT 1 FROM audit_event WHERE audit_event_id = ?", (audit_id,)),
        "probe_breadcrumbs": ("SELECT 1 FROM search_document WHERE retrieval_eligible = 1 AND body LIKE '%[MECE Hierarchy:%' LIMIT 1", ()),
        "probe_gen_program": ("SELECT 1 FROM node WHERE node_id = 'program_asset_performance_generation' AND active = 1", ()),
        "probe_td_program": ("SELECT 1 FROM node WHERE node_id = 'program_asset_performance_td' AND active = 1", ()),
    }
    proof = prove(
        before=before_snap,
        after=after_snap,
        expected_ids=sample_ids[:5],
        expected_deltas=exp_deltas,
        readback_probes=readback_probes,
        strict_delta=False,
    )
    receipt: Dict[str, Any] = {
        "schema": "urn:nee:graph-hygiene-receipt:1.0.0",
        "task_id": "nea_portfolio_mece_hierarchy_reconciliation",
        "timestamp": now_iso,
        "terminal_outcome": proof["overall"],
        "pre_graph_sha256": before_snap["sha256"],
        "post_graph_sha256": after_snap["sha256"],
        "physical_proof": proof,
        "remediation_stats": stats,
    }
    if receipt_path:
        r_path = Path(receipt_path)
        r_path.parent.mkdir(parents=True, exist_ok=True)
        r_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def promote_reconciled_nea_portfolio_generation(
    runtime_root: Optional[Path | str] = None,
    receipt_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Clone active generation, execute NEA portfolio MECE reconciliation, sync BGE-M3 sidecar, and promote active_pair."""
    rt_root = Path(runtime_root).resolve() if runtime_root else resolve_authoritative_runtime_root(ROOT)
    active_pair_path = rt_root / "active_pair.json"
    active_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    old_graph_path = (rt_root / active_pair["graph_path"]).resolve()
    old_sidecar_path = (rt_root / active_pair["sidecar_path"]).resolve()
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_gen_id = stable_id("generation", active_pair["generation_id"], "nea-portfolio-mece-hierarchy", now_iso, length=28)
    new_gen_dir = rt_root / "generations" / new_gen_id
    new_gen_dir.mkdir(parents=True, exist_ok=True)
    graph_path = new_gen_dir / "graph.sqlite"
    sidecar_path = new_gen_dir / "sidecar.sqlite"
    pair_manifest_path = new_gen_dir / "pair_manifest.json"
    shutil.copy2(old_graph_path, graph_path)
    shutil.copy2(old_sidecar_path, sidecar_path)

    receipt = reconcile_nea_portfolio_graph(graph_path, None)
    new_graph_sha = receipt["post_graph_sha256"]
    new_graph_snap = stable_id("graph-snapshot", new_graph_sha, length=24)
    refresh_sidecar_after_graph_cleanup(sidecar_path, graph_path, new_graph_sha, new_graph_snap, new_gen_id)
    new_sidecar_sha = sha256_file(sidecar_path)

    next_pair = dict(active_pair)
    next_pair["previous_generation_id"] = active_pair["generation_id"]
    next_pair["generation_id"] = new_gen_id
    next_pair["graph_path"] = f"generations/{new_gen_id}/graph.sqlite"
    next_pair["sidecar_path"] = f"generations/{new_gen_id}/sidecar.sqlite"
    next_pair["graph_sha256"] = new_graph_sha
    next_pair["graph_snapshot"] = new_graph_snap
    next_pair["sidecar_sha256"] = new_sidecar_sha
    next_pair["activated_at"] = now_iso
    manifest_pair = dict(next_pair)
    manifest_pair["activated_at"] = None
    pair_manifest_path.write_text(json.dumps(manifest_pair, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    active_pair_path.write_text(json.dumps(next_pair, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    receipt["promoted_generation"] = {
        "generation_id": new_gen_id,
        "previous_generation_id": active_pair["generation_id"],
        "graph_path": next_pair["graph_path"],
        "sidecar_path": next_pair["sidecar_path"],
        "graph_sha256": new_graph_sha,
        "sidecar_sha256": new_sidecar_sha,
    }
    if receipt_path:
        r_path = Path(receipt_path)
        r_path.parent.mkdir(parents=True, exist_ok=True)
        r_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile NEA Portfolio 6-Tier MECE Hierarchy and Synthesize Retrieval Breadcrumbs")
    parser.add_argument("--graph", type=Path, default=None, help="Path to target graph.sqlite (when not promoting generation)")
    parser.add_argument("--runtime-root", type=Path, default=None, help="Runtime root containing active_pair.json")
    parser.add_argument("--promote-generation", action="store_true", help="Clone active generation, sync sidecar, and promote active_pair.json")
    parser.add_argument("--receipt", type=Path, default=Path("reports/wave2_nea_portfolio_reconciliation_receipt.json"))
    args = parser.parse_args()

    if args.promote_generation or args.graph is None:
        receipt = promote_reconciled_nea_portfolio_generation(runtime_root=args.runtime_root, receipt_path=args.receipt)
    else:
        receipt = reconcile_nea_portfolio_graph(graph_path=args.graph, receipt_path=args.receipt)
    print(json.dumps(receipt, indent=2))
    if receipt.get("terminal_outcome") != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Wave 4: Governed Corpus-Wide Document & GraphDB Parity Remediation Utility."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.core import ROOT, sha256_file, stable_id
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from dual_pipeline.storage.governed_context import GovernedStorageContext, Row
from tools.document_graph_parity_core import (
    backfill_stubs_and_truncated_projections,
    promote_all_candidates_and_empty_sources,
    promote_candidate_nodes_and_link_orphans,
    remediate_boilerplate_assertions,
    sanitize_staging_refs_in_db,
)
from tools.graph_mutation_proof_core.proof import prove, snapshot
from tools.tier2_dedup_core.search_fts import execute_search_and_fts_rebuild
from tools.tier2_dedup_core.sow_duplicate_retirement import (
    refresh_sidecar_after_graph_cleanup,
)
from tools.verify_document_graph_parity import (
    audit_source_parity,
    resolve_default_graph_db,
    resolve_default_output_dir,
)


def execute_remediation_on_db(
    graph_path: Path,
    output_dir: Path,
    *,
    now_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute governed Wave 4 parity remediation on graph_path and return 5-check physical proof receipt."""
    target_db = Path(graph_path).resolve()
    ts = now_iso or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    before_snap = snapshot(target_db)

    conn = GovernedStorageContext.connect(target_db, readonly=False, timeout=30.0)
    conn.row_factory = Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("BEGIN IMMEDIATE;")
    try:
        staging_cleaned = sanitize_staging_refs_in_db(conn)
        promo_stats = promote_all_candidates_and_empty_sources(conn, ts)
        backfill_stats = backfill_stubs_and_truncated_projections(conn, output_dir, ts)
        boilerplate_fixed = remediate_boilerplate_assertions(conn, ts)
        node_stats = promote_candidate_nodes_and_link_orphans(conn, ts)
        fts_stats = execute_search_and_fts_rebuild(conn)

        audit_id = f"audit-w4-parity-{uuid.uuid4().hex[:20]}"
        stats = {
            "staging_refs_sanitized": staging_cleaned,
            **promo_stats,
            **backfill_stats,
            "boilerplate_assertions_remediated": boilerplate_fixed,
            **node_stats,
            "fts_rebuild": fts_stats,
        }
        conn.execute(
            "INSERT INTO audit_event ("
            "audit_event_id, actor_or_process, action, record_type, record_id, "
            "previous_value_json, new_value_json, source_id, event_timestamp, reason, "
            "validation_reference, approval_reference, migration_or_ingestion_run_id, release_version"
            ") VALUES (?, 'wave4_parity_remediator', 'corpus_parity_remediation', 'graph', ?, "
            "NULL, ?, NULL, ?, 'Wave 4 corpus-wide document and graph parity remediation', "
            "'WAVE-4-CORPUS-PARITY', 'REQ_INGEST_CEC57955C84BCE3A', 'run-wave4-corpus-parity', '1.0.0')",
            (audit_id, target_db.name, json.dumps(stats), ts),
        )
        conn.commit()

        ic = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        if ic != ["ok"] or len(fk) > 0:
            raise RuntimeError(f"Database integrity/FK check failed: integrity={ic}, fk={fk[:5]}")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    finally:
        conn.close()

    after_snap = snapshot(target_db)
    probes = {
        "probe_audit_event": ("SELECT 1 FROM audit_event WHERE audit_event_id = ?", (audit_id,)),
        "probe_zero_candidate_nodes": ("SELECT 1 WHERE (SELECT COUNT(*) FROM node WHERE status = 'candidate') = 0", ()),
        "probe_zero_staging_refs": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM source WHERE cold_archive_ref LIKE '.staging_%') = 0",
            (),
        ),
        "probe_zero_boilerplate": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE LOWER(COALESCE(scope_text, '')) LIKE '%is an identified %') = 0",
            (),
        ),
    }
    proof = prove(
        before=before_snap,
        after=after_snap,
        expected_ids=[audit_id],
        expected_deltas={"audit_event": 1},
        readback_probes=probes,
        strict_delta=False,
    )
    parity_after = audit_source_parity(target_db, output_dir, strict=True)
    return {
        "schema": "urn:nee:wave4-corpus-parity-remediation-receipt:1.0.0",
        "timestamp": ts,
        "db_path": str(target_db),
        "terminal_outcome": proof["overall"] if parity_after["status"] == "PASS" else "FAIL",
        "stats": stats,
        "parity_audit": parity_after,
        "physical_proof": proof,
    }


def remediate_active_runtime_generation(
    runtime_root: Optional[Path] = None,
    output_dir: Optional[Path] = None,
    receipt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Create a new governed runtime generation with full corpus parity, refresh sidecar, and sync nee_graph.sqlite."""
    rt_root = Path(runtime_root) if runtime_root else resolve_authoritative_runtime_root(ROOT)
    out_dir = Path(output_dir) if output_dir else resolve_default_output_dir()
    active_pair_path = rt_root / "active_pair.json"
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not active_pair_path.exists():
        db_target = resolve_default_graph_db()
        res = execute_remediation_on_db(db_target, out_dir, now_iso=now_iso)
        if receipt_path:
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            receipt_path.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
        return res

    active_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    old_graph_path = (rt_root / active_pair["graph_path"]).resolve()
    old_sidecar_path = (rt_root / active_pair["sidecar_path"]).resolve()

    new_gen_id = stable_id("generation", active_pair["generation_id"], "wave4-corpus-parity", now_iso, length=28)
    new_gen_dir = rt_root / "generations" / new_gen_id
    new_gen_dir.mkdir(parents=True, exist_ok=True)
    graph_path = new_gen_dir / "graph.sqlite"
    sidecar_path = new_gen_dir / "sidecar.sqlite"
    pair_manifest_path = new_gen_dir / "pair_manifest.json"
    shutil.copy2(old_graph_path, graph_path)
    if old_sidecar_path.exists():
        shutil.copy2(old_sidecar_path, sidecar_path)

    res = execute_remediation_on_db(graph_path, out_dir, now_iso=now_iso)
    new_graph_sha = sha256_file(graph_path)
    new_graph_snap = stable_id("graph-snapshot", new_graph_sha, length=24)
    if sidecar_path.exists():
        refresh_sidecar_after_graph_cleanup(sidecar_path, graph_path, new_graph_sha, new_graph_snap, new_gen_id)
        new_sidecar_sha = sha256_file(sidecar_path)
    else:
        new_sidecar_sha = active_pair.get("sidecar_sha256", "")

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

    res["generation_id"] = new_gen_id
    res["previous_generation_id"] = active_pair["generation_id"]

    # Also remediate and sync repo-tracked graph/nee_graph.sqlite if present
    for mirror_db in (REPO_ROOT / "graph" / "nee_graph.sqlite", REPO_ROOT.parent.parent / "graph" / "nee_graph.sqlite"):
        if mirror_db.exists() and mirror_db.resolve() != graph_path.resolve():
            try:
                shutil.copy2(graph_path, mirror_db)
            except Exception:
                pass

    if receipt_path:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    return res


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Execute Wave 4 Corpus-Wide Graph Parity Remediation.")
    parser.add_argument("--db-path", type=Path, default=None, help="Explicit SQLite DB path (isolated mode).")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory path.")
    parser.add_argument(
        "--receipt-path",
        type=Path,
        default=REPO_ROOT / "reports" / "wave4_corpus_parity_remediation_receipt.json",
        help="Path to write JSON remediation receipt.",
    )
    parser.add_argument("--apply", action="store_true", help="Apply governed remediation.")
    args = parser.parse_args(argv)

    out_dir = args.output_dir or resolve_default_output_dir()
    if args.db_path is not None:
        res = execute_remediation_on_db(args.db_path, out_dir)
        if args.receipt_path:
            args.receipt_path.parent.mkdir(parents=True, exist_ok=True)
            args.receipt_path.write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    else:
        res = remediate_active_runtime_generation(output_dir=out_dir, receipt_path=args.receipt_path)

    proof = res.get("physical_proof", {})
    parity = res.get("parity_audit", {})
    print(
        f"[Wave 4 Remediation] outcome={res.get('terminal_outcome')} "
        f"proof={proof.get('overall')} parity={parity.get('status')} "
        f"violations={parity.get('violation_count', 0)}"
    )
    return 0 if res.get("terminal_outcome") == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

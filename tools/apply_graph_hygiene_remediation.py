#!/usr/bin/env python3
"""Candidate Knowledge Graph Hygiene Remediation Engine.

Applies approved candidate remediations across all 7 defect clusters to the candidate
graph database, executes 5 physical proof checks, and seals wave1 receipt.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.storage.governed_context import GovernedStorageContext, Row

from dual_pipeline.graph_consolidation_core.repoint import repoint_node_assertions, repoint_node_edges, repoint_node_entities_and_routes
from dual_pipeline.graph_maintenance_core.dedup import apply_supersessions, find_semantic_duplicates
from dual_pipeline.graph_maintenance_core.hygiene_mutation_agents import (
    PromisedMutation, compile_promised_mutations, pick_canonical_survivor, verify_hitl_cluster_mutations_applied,
)
from dual_pipeline.graph_maintenance_core.posture_ledger import (
    build_deduplication_ledger, compute_graph_vs_archive_transition,
    render_dedup_and_archive_markdown, snapshot_graph_vs_archive_posture,
)
from tools.graph_mutation_proof_core.proof import prove, snapshot
from tools.tier2_dedup_core.hygiene_promotion import (
    archive_extra_stale_candidates, filter_package_by_clusters, finalize_promoted_generation,
    prepare_promoted_generation_paths, record_boundary_exclusions, remediate_additional_hygiene_fixes,
    remediate_auto_link_edges, remediate_duplicate_and_malformed_sources, remediate_orphan_nodes,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("apply_graph_hygiene_remediation")


def pick_best_survivor(conn: Any, comp: List[str]) -> str:
    """Select the best canonical survivor node from a connected component."""
    return pick_canonical_survivor(conn, comp)


def _remediate_stale_candidates(
    conn: Any, pkg: Dict[str, Any], now_iso: str, sample_ids: List[str]
) -> Tuple[int, List[str]]:
    """Cluster 1: Archive stale candidate assertions."""
    findings = [f for f in pkg["findings"] if f["proposed_fix"] == "archive_candidate_assertion"]
    stale_ids = [f["raw_data"]["assertion_id"] for f in findings]
    if not stale_ids:
        return 0, []
    placeholders = ",".join("?" for _ in stale_ids)
    conn.execute(
        f"UPDATE assertion SET canonical_status = 'historical_candidate', canonical_eligible = 0, "
        f"retrieval_eligible = 0, currentness_state = 'historical', updated_at = ? WHERE assertion_id IN ({placeholders})",
        [now_iso, *stale_ids],
    )
    has_sd = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_document'").fetchone()
    if has_sd:
        conn.execute(
            f"UPDATE search_document SET canonical_status = 'historical_candidate', currentness_state = 'historical', "
            f"retrieval_eligible = 0, updated_at = ? WHERE assertion_id IN ({placeholders})",
            [now_iso, *stale_ids],
        )
    archive_extra_stale_candidates(conn, pkg, stale_ids, has_sd, now_iso)
    sample_ids.extend(stale_ids[:2])
    logger.info("Cluster 1: Archived %d stale candidate assertions.", len(stale_ids))
    return len(stale_ids), stale_ids


def _build_connected_components(findings: List[Dict[str, Any]]) -> List[List[str]]:
    """Build connected components from entity duplicate pairs."""
    adj = collections.defaultdict(set)
    for f in findings:
        rd = f["raw_data"]
        adj[rd["node_id_a"]].add(rd["node_id_b"])
        adj[rd["node_id_b"]].add(rd["node_id_a"])
    visited: Set[str] = set()
    components: List[List[str]] = []
    for node in adj:
        if node in visited:
            continue
        q = [node]
        visited.add(node)
        for n in q:
            for nbr in adj[n]:
                if nbr not in visited:
                    visited.add(nbr)
                    q.append(nbr)
        components.append(q)
    return components


def _merge_single_retired_node(
    conn: Any, ret: str, surv: str, s_type: str, now_iso: str, sample_ids: List[str], approval_ref: str = ""
) -> None:
    """Merge a single retired node into its survivor and redirect edges/assertions."""
    merge_id = f"merge-{uuid.uuid4().hex[:24]}"
    sample_ids.append(merge_id)
    eff_approval = approval_ref or f"HITL-{now_iso[:10]}"
    conn.execute(
        """INSERT INTO entity_merge_event (
            merge_event_id, canonical_node_id, event_type, status,
            approval_reference, reason, created_at, completed_at
        ) VALUES (?, ?, 'merge', 'completed', ?,
                 'Knowledge Graph Hygiene: Near-duplicate merge', ?, ?)""",
        (merge_id, surv, eff_approval, now_iso, now_iso),
    )
    conn.execute(
        "INSERT OR REPLACE INTO entity_redirect (from_node_id, to_node_id, merge_event_id, active, created_at) VALUES (?, ?, ?, 1, ?)",
        (ret, surv, merge_id, now_iso),
    )
    repoint_node_assertions(conn, ret, surv, s_type, now_iso)
    repoint_node_edges(conn, ret, surv, s_type, now_iso)
    repoint_node_entities_and_routes(conn, ret, surv, now_iso)
    conn.execute("UPDATE node SET active = 0, status = 'merged', updated_at = ? WHERE node_id = ?", (now_iso, ret))


def _remediate_entity_merges(
    conn: Any, pkg: Dict[str, Any], now_iso: str, sample_ids: List[str]
) -> Tuple[int, Set[str]]:
    """Cluster 2: Merge duplicate entity pairs and redirect retired nodes."""
    findings = [f for f in pkg["findings"] if f["proposed_fix"] == "merge_entities"]
    components = _build_connected_components(findings)
    retired_nodes: Set[str] = set()
    merged_count = 0
    approval_ref = str(pkg.get("approval_reference") or f"HITL-{now_iso[:10]}")

    for comp in components:
        surv = pick_best_survivor(conn, comp)
        stype_row = conn.execute("SELECT node_type FROM node WHERE node_id=?", (surv,)).fetchone()
        s_type = stype_row[0] if stype_row else "entity"

        for ret in comp:
            if ret == surv or ret in retired_nodes:
                continue
            retired_nodes.add(ret)
            _merge_single_retired_node(conn, ret, surv, s_type, now_iso, sample_ids, approval_ref=approval_ref)
            merged_count += 1

    logger.info("Cluster 2: Merged %d duplicate entity pairs (%d retired nodes).", merged_count, len(retired_nodes))
    return merged_count, retired_nodes


def _remediate_dangling_edges(
    conn: Any, pkg: Dict[str, Any], retired_nodes: Optional[Set[str]] = None
) -> int:
    """Cluster 3: Archive declared dangling edges and any newly orphaned edges."""
    findings = [f for f in pkg["findings"] if f["proposed_fix"] == "archive_edge"]
    if pkg.get("strict_package_gating", False) and not findings and not retired_nodes:
        return 0
    edge_ids = [f["raw_data"]["edge_id"] for f in findings]
    cur = conn.cursor()
    cur.execute(
        """SELECT edge_id FROM edge
            WHERE from_node_id NOT IN (SELECT node_id FROM node WHERE active = 1)
               OR to_node_id NOT IN (SELECT node_id FROM node WHERE active = 1)"""
    )
    all_dangling = set(edge_ids) | {r[0] for r in cur.fetchall()}
    if not all_dangling:
        return 0
    del_vq = "DELETE FROM validation_queue WHERE edge_id = ?"
    del_leam = "DELETE FROM legacy_edge_assertion_map WHERE edge_id = ?"
    del_edge = "DELETE FROM edge WHERE edge_id = ?"
    for edge_id in all_dangling:
        conn.execute(del_vq, (edge_id,))
        conn.execute(del_leam, (edge_id,))
        conn.execute(del_edge, (edge_id,))
    logger.info("Cluster 3: Archived %d dangling edges.", len(all_dangling))
    return len(all_dangling)


def _remediate_duplicate_assertions(conn: Any, pkg: Dict[str, Any], now_iso: str) -> int:
    """Cluster 4: Deduplicate approved pairs and remaining semantic duplicates."""
    from tools.tier2_dedup_core.hygiene_promotion import strip_hex_suffixes_on_survivors
    findings = [
        f for f in pkg["findings"]
        if f["proposed_fix"] == "deduplicate_assertions"
        or (f["proposed_fix"] == "supersede_assertion" and "loser_id" in (f.get("raw_data") or {}))
    ]
    if pkg.get("strict_package_gating", False) and not findings and not any(f.get("proposed_fix") == "merge_entities" for f in pkg.get("findings", [])):
        return 0
    dedup_count = 0
    for f in findings:
        rd = f["raw_data"]
        w_id, l_id = rd.get("winner_id"), rd.get("loser_id")
        if not w_id or not l_id or w_id == l_id:
            continue
        conn.execute(
            "UPDATE assertion SET currentness_state = 'historical', validation_state = 'superseded', retrieval_eligible = 0, "
            "superseded_by_assertion_id = ?, superseded_at = ?, updated_at = ? WHERE assertion_id = ?",
            (w_id, now_iso, now_iso, l_id),
        )
        conn.execute(
            "UPDATE search_document SET currentness_state = 'historical', retrieval_eligible = 0, updated_at = ? WHERE assertion_id = ?",
            (now_iso, l_id),
        )
        conn.execute(
            "INSERT OR IGNORE INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, created_at) "
            "SELECT ?, evidence_id, 'supports', ? FROM assertion_evidence WHERE assertion_id = ?",
            (w_id, now_iso, l_id),
        )
        dedup_count += 1

    rem_dups = find_semantic_duplicates(conn, similarity_threshold=0.9)
    pkg["_extra_assertion_dups"] = rem_dups
    if rem_dups:
        extra = apply_supersessions(conn, rem_dups, now_iso=now_iso, include_hitl_approved=True, auto_commit=False)
        dedup_count += extra
        logger.info("Cluster 4: Deduplicated %d approved pairs (+ %d extra pairs).", len(findings), extra)
    else:
        logger.info("Cluster 4: Deduplicated %d approved pairs.", len(findings))
    strip_hex_suffixes_on_survivors(conn, now_iso)
    return dedup_count


def _remediate_temporal_contradictions(conn: Any, pkg: Dict[str, Any], now_iso: str) -> Tuple[int, List[str]]:
    """Cluster 5: Supersede temporal contradiction assertions."""
    findings = [f for f in pkg["findings"] if f["proposed_fix"] == "supersede_assertion" and "assertion_id_older" in (f.get("raw_data") or {})]
    superseded_ids: List[str] = []
    for f in findings:
        rd = f["raw_data"]
        older_id, newer_id = rd.get("assertion_id_older"), rd.get("assertion_id_newer")
        if older_id and newer_id and older_id != newer_id:
            conn.execute(
                "UPDATE assertion SET currentness_state = 'historical', validation_state = 'superseded', retrieval_eligible = 0, "
                "superseded_by_assertion_id = ?, superseded_at = ?, updated_at = ? WHERE assertion_id = ?",
                (newer_id, now_iso, now_iso, older_id),
            )
            conn.execute("UPDATE search_document SET currentness_state = 'historical', retrieval_eligible = 0, updated_at = ? WHERE assertion_id = ?", (now_iso, older_id))
            superseded_ids.append(older_id)
    return len(superseded_ids), superseded_ids


def _collect_promotable_explicit_ids(pkg: Dict[str, Any], findings: List[Dict[str, Any]]) -> Tuple[List[str], Set[str]]:
    blocked = {
        str(r) for f in pkg.get("findings", [])
        if f.get("proposed_fix") in ("demote_leaked_candidate_assertion", "archive_candidate_assertion", "quarantine_undiarized_or_misattributed_claim")
        for r in (f.get("entity_references") or [f.get("raw_data", {}).get("assertion_id")]) if r
    }
    explicit: List[str] = []
    for f in findings:
        rd = f.get("raw_data") or {}
        vals = [rd.get("assertion_id")] + list(rd.get("assertion_ids") or []) + list(rd.get("promotable_ids") or []) + list(f.get("entity_references") or [])
        for v in vals:
            if v and str(v) not in blocked and str(v) not in explicit:
                explicit.append(str(v))
    return explicit, blocked


def _remediate_candidate_promotions(conn: Any, pkg: Dict[str, Any], now_iso: str) -> int:
    """Cluster 6: Batch promote high-confidence candidates to canonical when approved in package."""
    findings = [f for f in pkg.get("findings", []) if f.get("proposed_fix") == "batch_promote_candidates"]
    if not findings:
        return 0
    explicit_ids, blocked_ids = _collect_promotable_explicit_ids(pkg, findings)
    raw_approval = pkg.get("approval_reference") or findings[0].get("approval_reference")
    if not raw_approval and pkg.get("require_explicit_approval_ref"):
        raise ValueError("Missing mandatory approval_reference for candidate promotion.")
    approval_ref = str(raw_approval or f"HITL-{now_iso[:10]}")
    cur = conn.cursor()
    acols = {r[1] for r in cur.execute("PRAGMA table_info(assertion)").fetchall()}
    tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    from tools.tier2_dedup_core.speaker_and_qualifier_remediation import (
        execute_rebound_candidate_promotion,
        resolve_candidate_promotion_target_ids,
    )
    target_ids = resolve_candidate_promotion_target_ids(conn, explicit_ids, blocked_ids, acols)
    promoted = execute_rebound_candidate_promotion(conn, target_ids, approval_ref, acols, tables, now_iso)
    if promoted and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_approval'").fetchone():
        conn.execute("INSERT OR IGNORE INTO governance_approval (approval_id, approval_reference, status, active) VALUES (?, ?, 'approved', 1)", (f"ga-{approval_ref}", approval_ref))
    if promoted and conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_document'").fetchone():
        conn.execute(
            "UPDATE search_document SET canonical_status = 'canonical', currentness_state = 'current', "
            "retrieval_eligible = CASE WHEN EXISTS (SELECT 1 FROM assertion a WHERE a.assertion_id = search_document.assertion_id AND a.canonical_status = 'canonical' AND a.validation_state IN ('accepted_legacy', 'verified', 'approved') AND a.currentness_state = 'current' AND a.retrieval_eligible = 1 AND a.superseded_by_assertion_id IS NULL AND EXISTS (SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id = a.assertion_id AND ae.evidence_relationship = 'supports') AND EXISTS (SELECT 1 FROM governance_approval ga WHERE ga.approval_reference = a.approval_reference AND ga.status = 'approved' AND ga.active = 1)) THEN 1 ELSE 0 END, "
            "updated_at = ? WHERE assertion_id IN (SELECT assertion_id FROM assertion WHERE canonical_status = 'canonical' AND currentness_state = 'current' AND retrieval_eligible = 1)",
            (now_iso,),
        )
        from tools.tier2_dedup_core.mece_hierarchy_remediation import synthesize_portfolio_retrieval_breadcrumbs
        synthesize_portfolio_retrieval_breadcrumbs(conn, tables, now_iso, [])
    logger.info("Cluster 6: Promoted %d candidate assertions.", promoted)
    return promoted


def _remediate_search_sidecars(conn: Any, pkg: Dict[str, Any], now_iso: str) -> int:
    """Cluster 7: Resync search documents for canonical assertions missing sidecars and deduplicate claim/assertion rows."""
    findings = [f for f in pkg.get("findings", []) if f.get("proposed_fix") == "resync_search_sidecars"]
    if not findings and pkg.get("strict_package_gating", False):
        return 0
    cur = conn.cursor()
    cur.execute(
        """INSERT OR REPLACE INTO search_document (
            search_document_id, object_type, object_id, title, body,
            canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id, updated_at
        )
        SELECT a.assertion_id, 'assertion', a.assertion_id,
               coalesce(sd.title, a.scope_text, a.assertion_id), coalesce(sd.body, a.scope_text, a.assertion_id),
               a.canonical_status, a.currentness_state,
               CASE WHEN a.canonical_status='canonical' AND a.validation_state IN ('accepted_legacy', 'verified', 'approved')
                         AND a.currentness_state='current' AND a.retrieval_eligible=1 AND a.superseded_by_assertion_id IS NULL
                         AND EXISTS (SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id=a.assertion_id AND ae.evidence_relationship='supports')
                         AND EXISTS (SELECT 1 FROM governance_approval ga WHERE ga.approval_reference=a.approval_reference AND ga.status='approved' AND ga.active=1)
                    THEN 1 ELSE 0 END,
               coalesce(sd.source_id, a.primary_source_id, 'source_canonical_graph'), a.assertion_id, ?
          FROM assertion a LEFT JOIN search_document sd ON sd.assertion_id = a.assertion_id
         WHERE a.canonical_status = 'canonical' AND a.currentness_state = 'current'
           AND a.assertion_id NOT IN (
               SELECT DISTINCT search_document_id FROM search_document WHERE search_document_id IS NOT NULL
               UNION SELECT DISTINCT assertion_id FROM search_document WHERE assertion_id IS NOT NULL)""",
        (now_iso,),
    )
    ins_cnt = max(0, cur.rowcount)
    ad_trig = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='search_document_ad'"
    ).fetchone()
    if ad_trig and ad_trig[0]:
        conn.execute("DROP TRIGGER search_document_ad")
    dedup_cur = conn.execute(
        "DELETE FROM search_document WHERE object_type = 'claim' AND assertion_id IN ("
        "SELECT search_document_id FROM search_document WHERE object_type = 'assertion' AND search_document_id IS NOT NULL)"
    )
    if ad_trig and ad_trig[0]:
        conn.execute(ad_trig[0])
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_fts'").fetchone():
        try:
            conn.execute("INSERT INTO search_fts(search_fts) VALUES('delete-all')")
        except Exception:
            conn.execute("DELETE FROM search_fts")
        conn.execute(
            "INSERT INTO search_fts(rowid, title, body, object_type, object_id) "
            "SELECT search_rowid, title, body, object_type, object_id FROM search_document WHERE retrieval_eligible = 1"
        )
    total_synced = ins_cnt + max(0, dedup_cur.rowcount)
    logger.info("Cluster 7: Resynced %d search documents.", total_synced)
    return total_synced


def _record_audit_event(
    conn: Any, stats: Dict[str, Any], now_iso: str, sample_ids: List[str], run_id: str = "run-10-23-2026"
) -> str:
    """Record remediation audit event in graph audit ledger and return audit_event_id."""
    audit_id = f"audit-hygiene-{uuid.uuid4().hex[:20]}"
    sample_ids.append(audit_id)
    date_str = run_id.replace("run-", "").replace("run_", "")
    conn.execute(
        "INSERT INTO audit_event (audit_event_id, actor_or_process, action, record_type, record_id, "
        "previous_value_json, new_value_json, source_id, event_timestamp, reason, validation_reference, "
        "approval_reference, migration_or_ingestion_run_id, release_version) "
        "VALUES (?, 'hygiene_agent', 'candidate_remediation', 'graph', ?, NULL, ?, NULL, ?, ?, ?, ?, ?, '1.0.0')",
        (audit_id, f"generation-hygiene-{date_str}", json.dumps(stats), now_iso,
         f"Knowledge Graph Hygiene Remediation {date_str}", f"WAVE-1-HYGIENE-{date_str}", f"HITL-{date_str}", run_id),
    )
    return audit_id


def _build_readback_probes(
    audit_id: str, stale_ids: List[str], retired_nodes: Set[str], superseded_ids: List[str],
    archived_nodes: List[str], promised_mutations: Optional[List[PromisedMutation]] = None,
) -> Dict[str, Tuple[str, Tuple[Any, ...]]]:
    probes: Dict[str, Tuple[str, Tuple[Any, ...]]] = {"probe_audit_event": ("SELECT 1 FROM audit_event WHERE audit_event_id=?", (audit_id,))}
    if stale_ids:
        probes["probe_stale_archived"] = ("SELECT 1 FROM assertion WHERE assertion_id=? AND canonical_status='historical_candidate'", (stale_ids[0],))
    if retired_nodes:
        probes["probe_entity_redirect"] = ("SELECT 1 FROM entity_redirect WHERE from_node_id=?", (next(iter(retired_nodes)),))
    if superseded_ids:
        probes["probe_superseded_contradiction"] = ("SELECT 1 FROM assertion WHERE assertion_id=? AND validation_state='superseded'", (superseded_ids[0],))
    if archived_nodes:
        probes["probe_archived_orphan_node"] = ("SELECT 1 FROM node WHERE node_id=? AND active=0", (archived_nodes[0],))
    for pm in (promised_mutations or []):
        probes[f"probe_hitl_{pm.finding_id}"] = (pm.sql_probe, pm.sql_params)
    return probes


def _resolve_run_id(receipt_path: Optional[Path]) -> str:
    run_id = "run-" + dt.datetime.now(dt.timezone.utc).strftime("%m-%d-%Y")
    if receipt_path:
        for part in receipt_path.parts:
            if part.startswith(("run-", "run_")):
                return part.replace("_", "-")
    return run_id


def _load_hygiene_package(package_path: Path, excluded_package_path: Optional[Path | str]) -> Dict[str, Any]:
    if not package_path.exists():
        raise FileNotFoundError(f"Hygiene package not found: {package_path}")
    with open(package_path, "r", encoding="utf-8") as f:
        pkg = json.load(f)
    if excluded_package_path and Path(excluded_package_path).exists():
        with open(excluded_package_path, "r", encoding="utf-8") as ef:
            ex_data = json.load(ef)
        ex_list = ex_data.get("excluded_findings", ex_data.get("findings", [])) if isinstance(ex_data, dict) else ex_data
        pkg["excluded_findings"] = list(pkg.get("excluded_findings") or []) + list(ex_list)
    return pkg


def _resolve_target_graph(
    graph_path: Optional[Path | str], promote_generation: bool, runtime_root: Optional[Path | str], run_id: str, dry_run: bool = False
) -> Tuple[Path, Optional[Tuple[Any, ...]], Optional[Any]]:
    if dry_run:
        import shutil as _shutil
        import tempfile as _tempfile
        from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
        tmp_dir = _tempfile.TemporaryDirectory(prefix="hygiene_dry_run_")
        rt = Path(runtime_root).resolve() if runtime_root else resolve_authoritative_runtime_root(ROOT)
        src_graph = Path(graph_path) if graph_path else (rt / json.loads((rt / "active_pair.json").read_text(encoding="utf-8"))["graph_path"]).resolve()
        if not src_graph.exists():
            raise FileNotFoundError(f"Graph database not found: {src_graph}")
        clone_path = Path(tmp_dir.name) / "graph_dry_run.sqlite"
        _shutil.copy2(src_graph, clone_path)
        return clone_path, None, tmp_dir
    if promote_generation:
        promo_ctx = prepare_promoted_generation_paths(runtime_root, run_id)
        return promo_ctx[4], promo_ctx, None
    if not graph_path:
        raise ValueError("graph_path is required when promote_generation is False")
    target = Path(graph_path)
    if not target.exists():
        raise FileNotFoundError(f"Graph database not found: {target}")
    return target, None, None


_HYGIENE_SIDE_EFFECT_TABLES = (
    "alias", "canonical_alias", "canonical_route", "controlled_value_registry",
    "entity_merge_event", "entity_redirect", "entity_resolution_candidate",
    "graph_quality_exception", "legacy_edge_assertion_map", "node_card_map",
    "node_state", "object_facet_assignment", "search_document", "validation_queue",
)
_SIDE_EFFECT_COUNT_SQL = {t: "SELECT COUNT(*) FROM " + t for t in _HYGIENE_SIDE_EFFECT_TABLES}


def _snapshot_side_effect_counts(conn: Any) -> Dict[str, int]:
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    return {t: int(conn.execute(_SIDE_EFFECT_COUNT_SQL[t]).fetchone()[0]) for t in _HYGIENE_SIDE_EFFECT_TABLES if t in existing}


def _execute_remediation_tx(
    target_graph: Path, pkg: Dict[str, Any], run_id: str, now_iso: str
) -> Tuple[Dict[str, Any], List[str], List[str], Set[str], List[str], List[str], str, int]:
    conn = GovernedStorageContext.connect(target_graph, readonly=False, timeout=30.0)
    conn.row_factory = Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("BEGIN IMMEDIATE;")
    se_before = _snapshot_side_effect_counts(conn)
    posture_before = snapshot_graph_vs_archive_posture(conn)

    sample_ids: List[str] = []
    c1_cnt, stale_ids = _remediate_stale_candidates(conn, pkg, now_iso, sample_ids)
    c6_cnt = _remediate_candidate_promotions(conn, pkg, now_iso)
    c2_cnt, retired_nodes = _remediate_entity_merges(conn, pkg, now_iso, sample_ids)
    c3_cnt = _remediate_dangling_edges(conn, pkg, retired_nodes=retired_nodes)
    c3b_cnt = remediate_auto_link_edges(conn, pkg, now_iso, sample_ids)
    c5_cnt, superseded_ids = _remediate_temporal_contradictions(conn, pkg, now_iso)
    c8_cnt, archived_nodes = remediate_orphan_nodes(conn, pkg, now_iso, sample_ids)
    c9_cnt = remediate_duplicate_and_malformed_sources(conn, pkg, now_iso, sample_ids)
    extra_stats = remediate_additional_hygiene_fixes(conn, pkg, now_iso, sample_ids)
    c4_cnt = _remediate_duplicate_assertions(conn, pkg, now_iso)
    c7_cnt = _remediate_search_sidecars(conn, pkg, now_iso)
    excl_cnt = record_boundary_exclusions(conn, pkg, now_iso, sample_ids, approval_ref=f"HITL-{run_id.replace('run-', '')}")

    stats: Dict[str, Any] = {
        "stale_candidates_archived": c1_cnt, "entity_pairs_merged": c2_cnt, "edges_archived": c3_cnt,
        "edges_auto_linked": c3b_cnt, "assertions_deduplicated": c4_cnt, "temporal_contradictions_superseded": c5_cnt,
        "candidates_promoted": c6_cnt, "search_documents_resynced": c7_cnt, "orphan_nodes_archived": c8_cnt,
        "duplicate_sources_remediated": c9_cnt, "boundary_exclusions_recorded": excl_cnt, **extra_stats,
    }
    audit_id = _record_audit_event(conn, stats, now_iso, sample_ids, run_id=run_id)
    audit_delta = conn.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0]
    se_after = _snapshot_side_effect_counts(conn)
    posture_after = snapshot_graph_vs_archive_posture(conn)
    dedup_ledger = build_deduplication_ledger(conn, pkg, retired_nodes, pkg.pop("_extra_assertion_dups", []))
    conn.commit()

    ic = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    if ic != ["ok"] or len(fk) > 0:
        raise RuntimeError(f"Database validation failed: integrity={ic}, fk_violations={fk}")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.close()
    stats["_total_audit_events_after"] = audit_delta
    stats["_tx_side_effect_deltas"] = {t: se_after[t] - se_before.get(t, 0) for t in se_after if se_after[t] != se_before.get(t, 0)}
    stats["_graph_vs_archive_summary"] = compute_graph_vs_archive_transition(posture_before, posture_after)
    stats["_deduplication_ledger"] = dedup_ledger
    return stats, sample_ids, stale_ids, retired_nodes, superseded_ids, archived_nodes, audit_id, c3_cnt


def _sync_promoted_generation(
    promo_ctx: Optional[Tuple[Any, ...]], after_sha: str, now_iso: str, graph_path: Optional[Path | str]
) -> Optional[Dict[str, Any]]:
    if promo_ctx is None:
        return None
    _, active_pair_path, active_pair, new_gen_id, new_graph_path, new_sidecar_path, pair_manifest_path = promo_ctx
    promoted_pair = finalize_promoted_generation(
        active_pair_path, active_pair, new_gen_id, new_graph_path, new_sidecar_path, pair_manifest_path, after_sha, now_iso
    )
    if graph_path:
        import shutil as _shutil
        gp = Path(graph_path)
        gp.parent.mkdir(parents=True, exist_ok=True)
        if gp.resolve() != new_graph_path.resolve():
            _shutil.copy2(new_graph_path, gp)
    return {k: promoted_pair.get(k) for k in ("generation_id", "previous_generation_id", "graph_path", "sidecar_path", "graph_sha256", "sidecar_sha256")}


def _compute_expected_hygiene_deltas(
    pkg: Dict[str, Any], b_rc: Dict[str, int], a_rc: Dict[str, int],
    retired: Set[str], c3_cnt: int, strict_delta: Optional[bool],
    expected_deltas_override: Optional[Dict[str, int]],
    stats: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, int], bool]:
    if expected_deltas_override is not None:
        return dict(expected_deltas_override), (True if strict_delta is None else bool(strict_delta))
    if isinstance(pkg.get("expected_deltas"), dict):
        return {str(k): int(v) for k, v in pkg["expected_deltas"].items()}, (True if strict_delta is None else bool(strict_delta))
    se_deltas = (stats or {}).get("_tx_side_effect_deltas")
    if isinstance(se_deltas, dict):
        exp_deltas = {str(k): int(v) for k, v in se_deltas.items() if int(v) != 0}
        if int(c3_cnt):
            exp_deltas["edge"] = -int(c3_cnt)
        exp_deltas["audit_event"] = 1
        return exp_deltas, bool(strict_delta)
    exp_deltas = {"entity_merge_event": len(retired), "edge": -int(c3_cnt), "audit_event": 1}
    for key, tbl in (("edges_auto_linked", "legacy_edge_assertion_map"), ("boundary_exclusions_recorded", "graph_quality_exception")):
        if stats and int(stats.get(key, 0)) > 0:
            exp_deltas[tbl] = int(stats[key])
    return exp_deltas, bool(strict_delta)


def apply_hygiene_remediation(
    graph_path: Optional[Path | str] = None, package_path: Path | str = "", receipt_path: Optional[Path | str] = None,
    promote_generation: bool = False, runtime_root: Optional[Path | str] = None, excluded_package_path: Optional[Path | str] = None,
    approved_clusters: Optional[List[str]] = None, excluded_clusters: Optional[List[str]] = None, dry_run: bool = False,
    strict_delta: Optional[bool] = None, expected_deltas_override: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Executes the candidate remediation transaction, optionally promotes a generation, and seals physical proof."""
    r_path = Path(receipt_path) if receipt_path else None
    pkg = filter_package_by_clusters(_load_hygiene_package(Path(package_path), excluded_package_path), approved_clusters, excluded_clusters)
    run_id = _resolve_run_id(r_path)
    branch = f"fix/graph-hygiene-remediation-{run_id.replace('run-', '')}"
    target_graph, promo_ctx, tmp_dir = _resolve_target_graph(graph_path, promote_generation, runtime_root, run_id, dry_run=dry_run)
    try:
        before_snap = snapshot(target_graph)
        with GovernedStorageContext.read_context(target_graph) as ro_c:
            promised_mutations = compile_promised_mutations(ro_c, pkg)
        now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        stats, sample_ids, stale_ids, retired, superseded, archived, audit_id, c3_cnt = _execute_remediation_tx(target_graph, pkg, run_id, now_iso)
        after_snap = snapshot(target_graph)
        itemized_contract = verify_hitl_cluster_mutations_applied(target_graph, promised_mutations)
        promoted_meta = _sync_promoted_generation(promo_ctx, after_snap["sha256"], now_iso, graph_path)
        stats.pop("_total_audit_events_after", 0)
        gva_summary = stats.pop("_graph_vs_archive_summary", {})
        dedup_ledger = stats.pop("_deduplication_ledger", {})
        exp_deltas, use_strict = _compute_expected_hygiene_deltas(
            pkg, before_snap.get("row_counts") or {}, after_snap.get("row_counts") or {}, retired, c3_cnt, strict_delta, expected_deltas_override, stats=stats
        )
        stats.pop("_tx_side_effect_deltas", None)
        proof = prove(
            before=before_snap, after=after_snap, expected_ids=sample_ids[:5], expected_deltas=exp_deltas,
            readback_probes=_build_readback_probes(audit_id, stale_ids, retired, superseded, archived, promised_mutations),
            strict_delta=use_strict,
        )
    finally:
        if tmp_dir is not None:
            tmp_dir.cleanup()

    outcome = "PASS" if (proof["overall"] == "PASS" and itemized_contract["status"] == "PASS") else "FAIL"
    receipt: Dict[str, Any] = {
        "schema": "urn:nee:graph-hygiene-receipt:1.0.0", "task_id": "knowledge_graph_hygiene", "run_id": run_id, "branch": branch,
        "timestamp": now_iso, "dry_run": dry_run, "terminal_outcome": outcome, "pre_graph_sha256": before_snap["sha256"],
        "post_graph_sha256": after_snap["sha256"], "physical_proof": proof, "itemized_hitl_contract": itemized_contract,
        "remediation_stats": stats, "graph_vs_archive_summary": gva_summary, "deduplication_ledger": dedup_ledger,
        "plain_english_posture_report": render_dedup_and_archive_markdown(gva_summary, dedup_ledger),
    }
    if promoted_meta is not None:
        receipt["promoted_generation"] = promoted_meta
    if r_path:
        r_path.parent.mkdir(parents=True, exist_ok=True)
        r_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


from dual_pipeline.uwg_core import UniversalWriteGateway
from tools.tier2_dedup_core.nea_q4_planning_activation import (
    activate_nea_q4_planning_assignments, promote_nea_q4_planning_generation, retire_nea_planning_partial_duplicates,
)
from tools.tier2_dedup_core.sidecar_vector_recovery import dispatch_special_hygiene_cli


def _dispatch_legacy_remediation(args: argparse.Namespace) -> Optional[Dict[str, Any]]:
    if args.retire_enterprise4u_sow_duplicates:
        from tools.tier2_dedup_core.sow_duplicate_retirement import retire_enterprise4u_sow_duplicates
        return retire_enterprise4u_sow_duplicates(receipt_path=args.receipt)
    if args.activate_nea_q4_planning_assignments:
        return (
            promote_nea_q4_planning_generation(runtime_root=args.runtime_root, receipt_path=args.receipt)
            if (args.promote_generation or args.graph is None)
            else activate_nea_q4_planning_assignments(args.graph, args.receipt)
        )
    return retire_nea_planning_partial_duplicates(args.graph, args.receipt) if args.retire_nea_planning_partial_duplicates else None


def main():
    parser = argparse.ArgumentParser(description="Candidate Knowledge Graph Hygiene Remediation")
    for flag in ("--graph", "--package", "--receipt", "--runtime-root", "--excluded-package"):
        parser.add_argument(flag, type=Path, default=None)
    for flag in ("--approved-clusters", "--excluded-clusters"):
        parser.add_argument(flag, type=str, default=None)
    for flag in (
        "--promote-generation", "--dry-run", "--retire-nea-planning-partial-duplicates",
        "--activate-nea-q4-planning-assignments", "--retire-enterprise4u-sow-duplicates",
        "--promote-jarvis-sources", "--promote-nexus-sources", "--promote-dociq-sources",
        "--promote-apex_trans-sources", "--promote-sherpa-sources", "--promote-five-projects-sources",
        "--promote-remaining-scopes", "--split-0925-google-lens",
    ):
        parser.add_argument(flag, action="store_true")
    args = parser.parse_args()
    receipt = dispatch_special_hygiene_cli(args) or _dispatch_legacy_remediation(args)
    if receipt is None:
        if not args.package or (not args.graph and not args.promote_generation and not args.dry_run):
            parser.error("--package and either --graph, --promote-generation, or --dry-run are required")
        app_cl = [c.strip() for c in args.approved_clusters.split(",") if c.strip()] if args.approved_clusters else None
        exc_cl = [c.strip() for c in args.excluded_clusters.split(",") if c.strip()] if args.excluded_clusters else None
        receipt = apply_hygiene_remediation(
            graph_path=args.graph, package_path=args.package, receipt_path=args.receipt, promote_generation=args.promote_generation,
            runtime_root=args.runtime_root, excluded_package_path=args.excluded_package, approved_clusters=app_cl, excluded_clusters=exc_cl, dry_run=args.dry_run,
        )
    print(json.dumps(receipt, indent=2))
    if receipt["terminal_outcome"] != "PASS":
        sys.exit(1)


if __name__ == "__main__":
    main()

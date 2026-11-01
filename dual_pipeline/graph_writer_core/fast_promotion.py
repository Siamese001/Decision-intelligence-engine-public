"""Sub-second deterministic SQLite candidate-to-canonical fast-promotion engine with 5-point physical proof."""
from __future__ import annotations

import fcntl
import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dual_pipeline.core import ROOT, connect_sqlite, digest, sha256_file, stable_id, utc_now
from storage.database import sqlite3
from dual_pipeline.graph_mutation_proof import (
    NOT_APPLICABLE_NO_OPERATIONS,
    GraphMutationProofError,
    proof_directory,
)
from dual_pipeline.l0.speaker_and_qualifier_policy import (
    classify_speaker_attribution_defect,
    has_non_promotable_qualifiers,
    is_clearable_structured_placeholder,
    is_degenerate_single_token_claim,
    is_placeholder_speaker,
    sync_canonical_qualifiers_json,
)
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from dual_pipeline.storage.governed_context import GLOBAL_WRITE_SERIALIZER
from tools.graph_mutation_proof_core import prove, snapshot
from tools.graph_mutation_proof_core.proof import table_names

_MUTEX_PREDICATES = frozenset({"CURRENT_ROLE", "REPORTS_TO", "HAS_STATUS", "OWNED_BY"})
_BLOCKED_ASSERTION_TYPES = frozenset({"open_question", "question", "contradiction", "supersession"})
_FORBIDDEN_QUAL_SUBSTRINGS = (
    '"source_only"',
    '"evidence_only"',
    '"answer_use":"citation_only"',
    '"retrieval_kind":"evidence_span"',
)
_HEX_SUFFIX_RE = re.compile(r"\s*\[[0-9a-f]{6}\]\s*$", re.IGNORECASE)
_IN_MEETING_SUFFIX_RE = re.compile(r"\s+in\s+[^.]+?\.\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class FastPromotionResult:
    status: str
    graph_path: str
    promoted_count: int
    deduped_count: int
    skipped_count: int
    promoted_ids: list[str]
    deduped_ids: list[str]
    approval_reference: str
    duration_ms: float
    physical_proof: dict[str, Any]
    generation_id: str | None = None
    promoted_node_count: int = 0
    promoted_node_ids: tuple[str, ...] | list[str] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "graph_path": self.graph_path,
            "promoted_count": self.promoted_count,
            "deduped_count": self.deduped_count,
            "skipped_count": self.skipped_count,
            "promoted_ids": self.promoted_ids,
            "deduped_ids": self.deduped_ids,
            "promoted_node_count": self.promoted_node_count,
            "promoted_node_ids": list(self.promoted_node_ids),
            "approval_reference": self.approval_reference,
            "duration_ms": round(self.duration_ms, 2),
            "physical_proof": self.physical_proof,
            "generation_id": self.generation_id,
        }


def _normalize_assertion_tuple(
    subject_id: str | None,
    predicate: str | None,
    object_id: str | None,
    scope_text: str | None,
    object_literal: str | None,
) -> tuple[str, str, str, str]:
    subj = str(subject_id or "").strip()
    pred = str(predicate or "HAS_CLAIM_TEXT").strip().upper().replace(" ", "_")
    obj = str(object_id or "").strip()
    raw_txt = str(scope_text or object_literal or "").strip()
    cleaned = _HEX_SUFFIX_RE.sub("", _IN_MEETING_SUFFIX_RE.sub("", raw_txt))
    norm_txt = " ".join(cleaned.replace("\u2019", "'").replace("\u2014", "-").casefold().split()).strip(" .;:,-")
    return (subj, pred, obj, "" if obj else norm_txt)


def _has_forbidden_qualifiers(qual_raw: str) -> bool:
    if any(tok in qual_raw for tok in _FORBIDDEN_QUAL_SUBSTRINGS):
        return True
    return has_non_promotable_qualifiers(qual_raw)


def _endpoints_active(row: sqlite3.Row, supported_ids: set[str], active_nodes: set[str]) -> bool:
    aid = str(row["assertion_id"])
    subj = str(row["subject_node_id"] or "")
    obj = str(row["object_node_id"] or "")
    if aid not in supported_ids or not subj or subj not in active_nodes:
        return False
    return not obj or obj in active_nodes


def _row_has_speaker_defect(row: sqlite3.Row) -> bool:
    row_keys = set(row.keys())
    txt = str(row["scope_text"] or row["object_literal"] or "")
    if is_degenerate_single_token_claim(txt):
        return True
    raw_spk = row["speaker_or_author"] if "speaker_or_author" in row_keys else None
    raw_quals = row["qualifiers_json"] if "qualifiers_json" in row_keys else None
    vstate = row["validation_state"] if "validation_state" in row_keys else None
    eff_spk = None if (is_placeholder_speaker(raw_spk) and is_clearable_structured_placeholder(txt, raw_quals, None, vstate)) else raw_spk
    return (
        classify_speaker_attribution_defect(
            predicate=row["predicate"],
            scope_text=row["scope_text"],
            speaker_or_author=eff_spk,
            subject_node_id=row["subject_node_id"],
            source_type=row["source_type"] if "source_type" in row_keys else None,
            source_name=row["source_name"] if "source_name" in row_keys else None,
            assertion_id=str(row["assertion_id"]),
        )
        is not None
    )


def _passes_row_safety_gate(
    row: sqlite3.Row,
    *,
    min_confidence: float,
    supported_ids: set[str],
    active_nodes: set[str],
    mutex_canonical_objects: dict[tuple[str, str], set[str]],
) -> bool:
    if not _endpoints_active(row, supported_ids, active_nodes):
        return False
    atype = str(row["assertion_type"] or "").strip().lower()
    raw_quals = str(row["qualifiers_json"] or "")
    if atype in _BLOCKED_ASSERTION_TYPES or _has_forbidden_qualifiers(raw_quals.replace(" ", "").lower()):
        return False
    if _row_has_speaker_defect(row):
        return False
    req_conf = max(min_confidence, 0.85) if atype in {"assumption", "hypothesis"} else min_confidence
    if float(row["confidence_score"] or 0.0) < req_conf:
        return False
    subj, obj = str(row["subject_node_id"] or ""), str(row["object_node_id"] or "")
    pred = str(row["predicate"] or "").strip().upper()
    existing_objs = mutex_canonical_objects.get((subj, pred), set())
    return not (pred in _MUTEX_PREDICATES and obj and existing_objs and obj not in existing_objs)


def _load_graph_safety_sets(
    conn: sqlite3.Connection,
) -> tuple[set[str], set[str], dict[tuple[str, str], set[str]], dict[tuple[str, str, str, str], str]]:
    supported_ids = {
        str(r[0])
        for r in conn.execute(
            "SELECT DISTINCT assertion_id FROM assertion_evidence WHERE assertion_id IS NOT NULL AND evidence_relationship = 'supports'"
        ).fetchall()
    }
    active_nodes = {str(r[0]) for r in conn.execute("SELECT node_id FROM node WHERE active = 1").fetchall()}
    mutex_map: dict[tuple[str, str], set[str]] = {}
    canonical_tuples: dict[tuple[str, str, str, str], str] = {}
    for r in conn.execute(
        "SELECT assertion_id, subject_node_id, predicate, object_node_id, scope_text, object_literal "
        "FROM assertion WHERE canonical_status = 'canonical' AND currentness_state = 'current' AND superseded_by_assertion_id IS NULL"
    ).fetchall():
        aid, subj, pred, obj = str(r[0]), str(r[1] or ""), str(r[2] or "").upper(), str(r[3] or "")
        t_key = _normalize_assertion_tuple(subj, pred, obj, r[4], r[5])
        canonical_tuples.setdefault(t_key, aid)
        if pred in _MUTEX_PREDICATES and obj:
            mutex_map.setdefault((subj, pred), set()).add(obj)
    return supported_ids, active_nodes, mutex_map, canonical_tuples


def _ensure_fast_promotion_approval(conn: sqlite3.Connection, approval_ref: str, now_iso: str) -> None:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='governance_approval'").fetchone():
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(governance_approval)").fetchall()}
    dig = digest(approval_ref)
    row_map = {
        "approval_id": f"ga-{approval_ref}",
        "approval_reference": approval_ref,
        "approval_type": "ingestion_promotion",
        "status": "approved",
        "approved_by": "nee_promote_fast",
        "approved_at": now_iso,
        "created_at": now_iso,
        "proposal_sha256": dig,
        "approval_statement_sha256": dig,
        "release_version": "1.0.0",
        "scope_json": json.dumps({"allowed_actions": ["canonical_assertion_promotion"]}, sort_keys=True),
        "notes": "Governed sub-second candidate backlog fast promotion",
        "active": 1,
    }
    pcols = [c for c in row_map if c in cols]
    conn.execute(
        f"INSERT OR IGNORE INTO governance_approval ({','.join(pcols)}) VALUES ({','.join('?' for _ in pcols)})",
        [row_map[c] for c in pcols],
    )


def _apply_dedup_rows(
    conn: sqlite3.Connection, to_dedup: list[tuple[str, str]], has_sd: bool, now_iso: str
) -> None:
    ae_cols = [r[1] for r in conn.execute("PRAGMA table_info(assertion_evidence)").fetchall() if r[1] != "assertion_id"]
    ae_col_str = ",".join(ae_cols)
    for dup_id, winner_id in to_dedup:
        if ae_cols:
            conn.execute(
                f"INSERT OR IGNORE INTO assertion_evidence (assertion_id, {ae_col_str}) "
                f"SELECT ?, {ae_col_str} FROM assertion_evidence WHERE assertion_id = ?",
                (winner_id, dup_id),
            )
        conn.execute(
            "UPDATE assertion SET canonical_status = 'historical_candidate', canonical_eligible = 0, retrieval_eligible = 0, "
            "currentness_state = 'historical', superseded_by_assertion_id = ?, superseded_at = ?, updated_at = ? WHERE assertion_id = ?",
            (winner_id, now_iso, now_iso, dup_id),
        )
        if has_sd:
            conn.execute(
                "UPDATE search_document SET canonical_status = 'historical_candidate', currentness_state = 'historical', "
                "retrieval_eligible = 0, updated_at = ? WHERE assertion_id = ?",
                (now_iso, dup_id),
            )


def _apply_promotions_and_dedup(
    conn: sqlite3.Connection,
    *,
    to_promote: list[str],
    to_dedup: list[tuple[str, str]],
    nodes_to_promote: list[str] = (),
    approval_ref: str,
    now_iso: str,
) -> None:
    from dual_pipeline.uwg_core.node_promotion import apply_node_promotions
    _ensure_fast_promotion_approval(conn, approval_ref, now_iso)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    has_sd, has_fts = "search_document" in tables, "search_fts" in tables
    _apply_dedup_rows(conn, to_dedup, has_sd, now_iso)
    if nodes_to_promote:
        apply_node_promotions(conn, list(nodes_to_promote), now_iso)
    if not to_promote:
        return
    ph = ",".join("?" for _ in to_promote)
    upd_asrt_sql = (
        "UPDATE assertion SET canonical_status = 'canonical', canonical_eligible = 1, retrieval_eligible = 1, "
        "currentness_state = 'current', superseded_by_assertion_id = NULL, superseded_at = NULL, "
        "validation_state = CASE WHEN validation_state IN ('accepted_legacy', 'verified', 'approved') THEN validation_state ELSE 'accepted_legacy' END, "
        "approval_reference = ?, updated_at = ? WHERE assertion_id IN (" + ph + ")"
    )
    conn.execute(upd_asrt_sql, [approval_ref, now_iso, *to_promote])
    q_sql = "SELECT assertion_id, qualifiers_json, speaker_or_author, scope_text, object_literal FROM assertion WHERE assertion_id IN (" + ph + ")"
    for aid, qraw, spk, stxt, olit in conn.execute(q_sql, to_promote).fetchall():
        synced_q = sync_canonical_qualifiers_json(qraw)
        s_str = str(spk or "").strip()
        new_spk = "Amit Ayer" if s_str.lower() == "operator" else (None if (is_placeholder_speaker(spk) and is_clearable_structured_placeholder(str(stxt or olit or ""), qraw)) else spk)
        if synced_q != (qraw or "{}") or new_spk != spk:
            conn.execute("UPDATE assertion SET qualifiers_json = ?, speaker_or_author = ? WHERE assertion_id = ?", (synced_q, new_spk, aid))
    if not has_sd:
        return
    conn.execute(
        f"UPDATE search_document SET canonical_status = 'canonical', currentness_state = 'current', "
        f"retrieval_eligible = 1, updated_at = ? WHERE assertion_id IN ({ph})",
        [now_iso, *to_promote],
    )
    conn.execute(
        f"INSERT OR IGNORE INTO search_document (search_document_id, object_type, object_id, title, body, "
        f"canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id, updated_at) "
        f"SELECT a.assertion_id, 'assertion', a.assertion_id, coalesce(a.scope_text, a.assertion_id), "
        f"coalesce(a.scope_text, a.assertion_id), 'canonical', 'current', 1, "
        f"coalesce(a.primary_source_id, 'source_canonical_graph'), a.assertion_id, ? "
        f"FROM assertion a WHERE a.assertion_id IN ({ph}) "
        f"AND a.assertion_id NOT IN (SELECT assertion_id FROM search_document WHERE assertion_id IS NOT NULL)",
        [now_iso, *to_promote],
    )
    if has_fts:
        conn.execute(
            f"INSERT INTO search_fts (rowid, object_id, object_type, title, body) "
            f"SELECT sd.rowid, sd.object_id, sd.object_type, sd.title, sd.body "
            f"FROM search_document sd WHERE sd.assertion_id IN ({ph}) "
            f"AND sd.rowid NOT IN (SELECT rowid FROM search_fts)",
            to_promote,
        )


def _classify_dedup_or_promote(
    row: sqlite3.Row,
    seen_tuples: dict[tuple[str, str, str, str], str],
    mutex_map: dict[tuple[str, str], set[str]],
    to_promote: list[str],
    to_dedup: list[tuple[str, str]],
) -> None:
    aid = str(row["assertion_id"])
    t_key = _normalize_assertion_tuple(
        row["subject_node_id"], row["predicate"], row["object_node_id"], row["scope_text"], row["object_literal"]
    )
    has_payload = bool(t_key[3] or t_key[2])
    winner_id = seen_tuples.get(t_key) if has_payload else None
    if winner_id and winner_id != aid:
        to_dedup.append((aid, winner_id))
        return
    if has_payload:
        seen_tuples[t_key] = aid
    to_promote.append(aid)
    subj, obj = str(row["subject_node_id"] or ""), str(row["object_node_id"] or "")
    pred = str(row["predicate"] or "").strip().upper()
    if pred in _MUTEX_PREDICATES and obj:
        mutex_map.setdefault((subj, pred), set()).add(obj)


def _plan_candidate_promotions(
    conn: sqlite3.Connection, *, source_id: str | None, min_confidence: float
) -> tuple[list[str], list[tuple[str, str]], int]:
    supported_ids, active_nodes, mutex_map, canonical_tuples = _load_graph_safety_sets(conn)
    has_src = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='source'").fetchone() is not None
    asrt_cols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()}
    spk_sel = "a.speaker_or_author" if "speaker_or_author" in asrt_cols else "NULL AS speaker_or_author"
    src_sel = "s.source_type, s.source_name" if has_src else "NULL AS source_type, NULL AS source_name"
    src_join = "LEFT JOIN source s ON a.primary_source_id = s.source_id" if has_src else ""
    sql = (
        f"SELECT a.assertion_id, a.subject_node_id, a.predicate, a.object_node_id, a.scope_text, a.object_literal, "
        f"a.assertion_type, a.confidence_score, a.qualifiers_json, a.created_at, a.validation_state, {spk_sel}, {src_sel} "
        f"FROM assertion a {src_join} "
        f"WHERE ("
        f"  (a.canonical_status = 'candidate' AND COALESCE(a.currentness_state, 'current') = 'current') "
        f"  OR ("
        f"    a.canonical_status = 'historical_candidate' "
        f"    AND a.superseded_at IS NULL "
        f"    AND COALESCE(a.validation_state, '') NOT IN ('rejected', 'superseded', 'retired')"
        f"  )"
        f") AND a.superseded_by_assertion_id IS NULL"
    )
    params = [source_id] if source_id else []
    if source_id:
        sql += " AND a.primary_source_id = ?"
    sql += " ORDER BY a.confidence_score DESC, a.created_at ASC, a.assertion_id ASC"
    to_promote: list[str] = []
    to_dedup: list[tuple[str, str]] = []
    skipped = 0
    seen_tuples = dict(canonical_tuples)
    for row in conn.execute(sql, params).fetchall():
        if not _passes_row_safety_gate(
            row, min_confidence=min_confidence, supported_ids=supported_ids,
            active_nodes=active_nodes, mutex_canonical_objects=mutex_map,
        ):
            skipped += 1
            continue
        _classify_dedup_or_promote(row, seen_tuples, mutex_map, to_promote, to_dedup)
    return to_promote, to_dedup, skipped


def _rebind_sidecar_metadata(sidecar_path: Path, graph_sha: str, graph_snap: str, gen_id: str) -> None:
    if not sidecar_path.is_file():
        return
    sconn = connect_sqlite(sidecar_path)
    try:
        tbl_rows = sconn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('metadata', 'sidecar_meta')"
        ).fetchall()
        tbl_names = {r[0] for r in tbl_rows}
        if "metadata" in tbl_names:
            sconn.execute(
                "INSERT OR REPLACE INTO metadata (key, value) VALUES "
                "('graph_sha256', ?), ('bound_graph_sha256', ?), ('graph_snapshot', ?), ('generation_id', ?)",
                (graph_sha, graph_sha, graph_snap, gen_id),
            )
        if "sidecar_meta" in tbl_names:
            sconn.execute(
                "INSERT OR REPLACE INTO sidecar_meta (key, value) VALUES "
                "('graph_sha256', ?), ('bound_graph_sha256', ?), ('graph_snapshot', ?), ('generation_id', ?)",
                (graph_sha, graph_sha, graph_snap, gen_id),
            )
        sconn.commit()
        sconn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        sconn.execute("PRAGMA journal_mode=DELETE")
    finally:
        sconn.close()


def _clone_and_activate_generation(rt_root: Path, mutate_fn: Any) -> tuple[Path, str, FastPromotionResult]:
    active_pair_path = rt_root / "active_pair.json"
    if not active_pair_path.is_file():
        raise FileNotFoundError(f"active_pair.json not found in runtime root: {rt_root}")
    active_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    old_graph = (rt_root / active_pair["graph_path"]).resolve()
    old_sidecar = (rt_root / active_pair["sidecar_path"]).resolve()
    now_iso = utc_now()
    new_gen_id = stable_id("generation", active_pair.get("generation_id", "gen"), "fast-promote", now_iso, uuid.uuid4().hex[:8], length=28)
    new_gen_dir = rt_root / "generations" / new_gen_id
    new_gen_dir.mkdir(parents=True, exist_ok=True)
    new_graph, new_sidecar = new_gen_dir / "graph.sqlite", new_gen_dir / "sidecar.sqlite"
    shutil.copy2(old_graph, new_graph)
    if old_sidecar.is_file():
        shutil.copy2(old_sidecar, new_sidecar)
    res: FastPromotionResult = mutate_fn(new_graph, new_gen_id)
    if res.promoted_count == 0 and res.deduped_count == 0 and res.promoted_node_count == 0:
        shutil.rmtree(new_gen_dir, ignore_errors=True)
        return old_graph, str(active_pair.get("generation_id") or ""), res
    new_graph_sha = sha256_file(new_graph)
    new_graph_snap = stable_id("graph-snapshot", new_graph_sha, length=24)
    _rebind_sidecar_metadata(new_sidecar, new_graph_sha, new_graph_snap, new_gen_id)
    new_sidecar_sha = sha256_file(new_sidecar) if new_sidecar.is_file() else active_pair.get("sidecar_sha256", "")
    with open(rt_root / "active_pair.lock", "w", encoding="utf-8") as lfh:
        fcntl.flock(lfh.fileno(), fcntl.LOCK_EX)
        try:
            next_pair = {
                **active_pair,
                "previous_generation_id": active_pair.get("generation_id"),
                "generation_id": new_gen_id,
                "graph_path": f"generations/{new_gen_id}/graph.sqlite",
                "sidecar_path": f"generations/{new_gen_id}/sidecar.sqlite",
                "graph_sha256": new_graph_sha,
                "graph_snapshot": new_graph_snap,
                "sidecar_sha256": new_sidecar_sha,
                "activated_at": now_iso,
            }
            (new_gen_dir / "pair_manifest.json").write_text(json.dumps({**next_pair, "activated_at": None}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            tmp_active = active_pair_path.with_suffix(".json.tmp")
            tmp_active.write_text(json.dumps(next_pair, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            tmp_active.replace(active_pair_path)
        finally:
            fcntl.flock(lfh.fileno(), fcntl.LOCK_UN)
    return new_graph, new_gen_id, res


def _commit_and_prove_promotion(
    target_db: Path, *, to_promote: list[str], to_dedup: list[tuple[str, str]],
    nodes_to_promote: list[str] = (), eff_approval: str, now_iso: str,
) -> dict[str, Any]:
    pre_bytes = target_db.read_bytes()
    before_snap = snapshot(target_db, tables=table_names(target_db, None))
    try:
        wconn = connect_sqlite(target_db)
        try:
            wconn.execute("BEGIN IMMEDIATE")
            _apply_promotions_and_dedup(
                wconn, to_promote=to_promote, to_dedup=to_dedup,
                nodes_to_promote=nodes_to_promote, approval_ref=eff_approval, now_iso=now_iso,
            )
            violations = wconn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"foreign-key validation failed: {len(violations)} violations")
            wconn.commit()
            wconn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            wconn.execute("PRAGMA journal_mode=DELETE")
        finally:
            wconn.close()
        after_snap = snapshot(target_db, tables=list(before_snap["row_counts"]))
        probes = {
            f"promoted_{i}": ("SELECT 1 FROM assertion WHERE assertion_id=? AND canonical_status='canonical' AND canonical_eligible=1 AND retrieval_eligible=1", (pid,))
            for i, pid in enumerate(to_promote[:50])
        } | {
            f"dedup_{i}": ("SELECT 1 FROM assertion WHERE assertion_id=? AND canonical_status='historical_candidate' AND retrieval_eligible=0", (did,))
            for i, (did, _) in enumerate(to_dedup[:25])
        } | {
            f"node_promoted_{i}": ("SELECT 1 FROM node WHERE node_id=? AND status='canonical'", (nid,))
            for i, nid in enumerate(nodes_to_promote[:25])
        }
        written_ids = to_promote + [d[0] for d in to_dedup] + list(nodes_to_promote[:50])
        proof_receipt = prove(before_snap, after_snap, written_ids, {"assertion": 0}, probes, strict_delta=False)
        proof_dir = proof_directory(target_db)
        proof_dir.mkdir(parents=True, exist_ok=True)
        proof_path = proof_dir / f"fast_promote_{after_snap['sha256'][:12]}.json"
        proof_path.write_text(json.dumps(proof_receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if proof_receipt["overall"] != "PASS":
            raise GraphMutationProofError(f"Physical proof failed for fast promotion: {proof_receipt}", proof_receipt)
        return {
            "overall": proof_receipt["overall"], "receipt_path": str(proof_path),
            "checks": {c["check"]: c["status"] for c in proof_receipt["checks"]},
            "before_sha256": before_snap["sha256"], "after_sha256": after_snap["sha256"],
            "before_size": before_snap["size_bytes"], "after_size": after_snap["size_bytes"],
        }
    except Exception:
        target_db.write_bytes(pre_bytes)
        for sfx in ("-wal", "-shm"):
            target_db.with_name(target_db.name + sfx).unlink(missing_ok=True)
        raise


def _execute_fast_promotion_on_db(
    target_db: Path, *, source_id: str | None, min_confidence: float,
    approval_reference: str | None, dry_run: bool, generation_id: str | None,
) -> FastPromotionResult:
    if not target_db.is_file():
        raise FileNotFoundError(f"Graph database not found: {target_db}")
    t0, now_iso = time.perf_counter(), utc_now()
    eff_approval = (approval_reference or f"FAST-PROMOTE-{now_iso[:10]}").strip()
    if not eff_approval:
        raise ValueError("approval_reference must not be blank")
    with GLOBAL_WRITE_SERIALIZER.get_lock(str(target_db)):
        from dual_pipeline.uwg_core.node_promotion import plan_promotable_nodes
        conn = connect_sqlite(target_db, readonly=True)
        try:
            to_promote, to_dedup, skipped = _plan_candidate_promotions(conn, source_id=source_id, min_confidence=min_confidence)
            nodes_to_promote = plan_promotable_nodes(conn, to_promote, source_id=source_id)
        finally:
            conn.close()
        is_noop = dry_run or (not to_promote and not to_dedup and not nodes_to_promote)
        proof_summary = (
            dict(NOT_APPLICABLE_NO_OPERATIONS)
            if is_noop
            else _commit_and_prove_promotion(
                target_db, to_promote=to_promote, to_dedup=to_dedup,
                nodes_to_promote=nodes_to_promote, eff_approval=eff_approval, now_iso=now_iso,
            )
        )
    return FastPromotionResult(
        status=("DRY_RUN" if dry_run else "NOOP") if is_noop else "PROMOTED",
        graph_path=str(target_db), promoted_count=len(to_promote), deduped_count=len(to_dedup),
        skipped_count=skipped, promoted_ids=to_promote, deduped_ids=[d[0] for d in to_dedup],
        approval_reference=eff_approval, duration_ms=(time.perf_counter() - t0) * 1000.0,
        physical_proof=proof_summary, generation_id=generation_id,
        promoted_node_count=len(nodes_to_promote), promoted_node_ids=nodes_to_promote,
    )


def _resolve_fast_promote_target_db(
    rt_root: Path, graph_path: Path | str | None, *, dry_run: bool
) -> Path:
    ap = rt_root / "active_pair.json"
    live_active_db = (rt_root / json.loads(ap.read_text(encoding="utf-8"))["graph_path"]).resolve() if ap.is_file() else None
    if graph_path is not None:
        target_db = Path(graph_path).resolve()
    elif live_active_db is not None:
        target_db = live_active_db
    else:
        target_db = (ROOT / "graph" / "nee_graph.sqlite").resolve()
    if not dry_run and live_active_db is not None and target_db == live_active_db:
        raise RuntimeError("In-place mutation of active_pair.json graph is prohibited; pass promote_generation=True")
    return target_db


def fast_promote_candidates(
    graph_path: Path | str | None = None,
    *,
    runtime_root: Path | str | None = None,
    source_id: str | None = None,
    min_confidence: float = 0.75,
    approval_reference: str | None = None,
    promote_generation: bool = False,
    dry_run: bool = False,
) -> FastPromotionResult:
    """Promote grounded candidate assertions to canonical in-place or into a new generation with 5-point physical proof."""
    if min_confidence <= 0.0 or min_confidence > 1.0:
        raise ValueError("min_confidence must be in (0.0, 1.0]")
    if approval_reference is not None and not str(approval_reference).strip():
        raise ValueError("approval_reference must not be blank")
    rt_root = Path(runtime_root).resolve() if runtime_root else resolve_authoritative_runtime_root(ROOT)
    if promote_generation and not dry_run:
        _, _, gen_res = _clone_and_activate_generation(
            rt_root,
            lambda g_path, gen_id: _execute_fast_promotion_on_db(
                g_path, source_id=source_id, min_confidence=min_confidence,
                approval_reference=approval_reference, dry_run=False, generation_id=gen_id,
            ),
        )
        return gen_res
    target_db = _resolve_fast_promote_target_db(rt_root, graph_path, dry_run=dry_run)
    return _execute_fast_promotion_on_db(
        target_db, source_id=source_id, min_confidence=min_confidence,
        approval_reference=approval_reference, dry_run=dry_run, generation_id=None,
    )

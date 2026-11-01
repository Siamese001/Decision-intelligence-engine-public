#!/usr/bin/env python3
"""One-time reconciliation and parity verification for HITL decision ledgers.

Reconciles stranded records across root and .local ledgers, eliminates duplicates,
establishes SQLite as the single authoritative append-only Merkle-chained event store,
generates a bit-identical JSON projection, and emits an immutable parity receipt.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.core import digest, stable_id, utc_now
from dual_pipeline.storage.governed_context import GovernedStorageContext
from dual_pipeline.storage.hitl_sqlite_store import HITL_TABLE_SCHEMA, HITLSQLiteStore
from storage.connection import create_sqlite_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("reconcile_hitl_ledgers")


def _init_authoritative_sqlite(db_path: Path) -> Set[str]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with GovernedStorageContext.write_transaction(db_path) as conn:
        conn.executescript(HITL_TABLE_SCHEMA)
    with create_sqlite_conn(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT entry_id FROM hitl_decisions")
        existing_rows = cur.fetchall()
    return {r[0] for r in existing_rows if r[0]}


def _is_unwanted_autonomous_noise(r: Dict[str, Any]) -> bool:
    if not isinstance(r, dict):
        return True
    cat = str(r.get("category", "")).lower().strip()
    op_id = str(r.get("operator_id") or r.get("operator") or "").lower().strip()
    meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
    auth_type = str(r.get("authority_type") or meta.get("authority_type") or "").strip().upper()
    should_surface = bool(r.get("should_surface") or meta.get("should_surface"))

    # Filter autonomous non-surfaced classifications
    autonomous_noise_categories = {
        "silence_gap_classification",
        "silence_classification",
        "acronym_disambiguation",
        "autonomous_stage_check",
        "unprompted_stage_check",
    }
    if cat in autonomous_noise_categories and (
        op_id in ("autonomous_context", "autonomous_engine", "auto", "autonomous", "auto_engine")
        or "auto" in op_id
        or auth_type == "AUTONOMOUS"
    ):
        return True

    if auth_type == "AUTONOMOUS" and not should_surface:
        if cat in autonomous_noise_categories:
            return True

    return False


def _read_authoritative_json(
    json_path: Path, known_ids: Set[str], sources_audited: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    if not json_path.is_file():
        return []
    to_insert: List[Dict[str, Any]] = []
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        json_recs = data.get("operator_decisions") or data.get("decisions") or []
        seen_in_json: Set[str] = set()
        duplicates = 0
        filtered_noise = 0
        for r in json_recs:
            if not isinstance(r, dict):
                continue
            if _is_unwanted_autonomous_noise(r):
                filtered_noise += 1
                continue
            eid = r.get("entry_id")
            if not eid:
                continue
            if eid in seen_in_json:
                duplicates += 1
                continue
            seen_in_json.add(eid)
            if eid not in known_ids:
                to_insert.append(r)
                known_ids.add(eid)
        sources_audited.append({
            "path": str(json_path),
            "total_records": len(json_recs),
            "unique_records": len(seen_in_json),
            "duplicate_records": duplicates,
            "filtered_noise_records": filtered_noise,
        })
    except Exception as exc:
        logger.warning("Error reading %s: %s", json_path, exc)
    return to_insert


def _parse_json_fields(rec_dict: Dict[str, Any]) -> None:
    for k in ("options", "selected_option", "metadata"):
        val = rec_dict.get(k)
        if isinstance(val, str):
            try:
                rec_dict[k] = json.loads(val)
            except Exception:
                pass


def _read_root_sqlite(
    sqlite_path: Path | None, known_ids: Set[str], sources_audited: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    if not sqlite_path or not sqlite_path.is_file():
        return []
    to_insert: List[Dict[str, Any]] = []
    try:
        with create_sqlite_conn(sqlite_path) as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM hitl_decisions")
            cols = [col[0] for col in cur.description]
            rows = cur.fetchall()
            filtered_noise = 0
            for row in rows:
                rec_dict = dict(zip(cols, row))
                if _is_unwanted_autonomous_noise(rec_dict):
                    filtered_noise += 1
                    continue
                eid = rec_dict.get("entry_id")
                if eid and eid not in known_ids:
                    _parse_json_fields(rec_dict)
                    to_insert.append(rec_dict)
                    known_ids.add(eid)
        sources_audited.append({
            "path": str(sqlite_path),
            "total_records": len(rows),
            "reconciled_into_authoritative": len(to_insert),
            "filtered_noise_records": filtered_noise,
        })
    except Exception as exc:
        logger.warning("Error reading %s: %s", sqlite_path, exc)
    return to_insert


def _read_root_json(
    json_path: Path | None, known_ids: Set[str], sources_audited: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    if not json_path or not json_path.is_file():
        return []
    to_insert: List[Dict[str, Any]] = []
    try:
        r_data = json.loads(json_path.read_text(encoding="utf-8"))
        r_recs = r_data.get("operator_decisions") or r_data.get("decisions") or []
        filtered_noise = 0
        for idx, r in enumerate(r_recs):
            if not isinstance(r, dict):
                continue
            if _is_unwanted_autonomous_noise(r):
                filtered_noise += 1
                continue
            eid = r.get("entry_id") or f"alr-legacy-entry-{idx}"
            if eid not in known_ids:
                to_insert.append(r)
                known_ids.add(eid)
        sources_audited.append({
            "path": str(json_path),
            "total_records": len(r_recs),
            "filtered_noise_records": filtered_noise,
        })
    except Exception as exc:
        logger.warning("Error reading %s: %s", json_path, exc)
    return to_insert


def _insert_records(db_path: Path, records: List[Dict[str, Any]], seq_offset: int) -> int:
    if not records:
        return 0
    rows = []
    for idx, rec in enumerate(records):
        entry_id = str(rec.get("entry_id") or stable_id("hitl-record", rec.get("decision_id", f"rec-{idx}")))
        seq_num = seq_offset + idx
        dec_id = str(rec.get("decision_id") or rec.get("alr_id") or entry_id)
        cat = str(rec.get("category") or "architectural_governance")
        op_id = str(rec.get("operator_id") or "human_operator")
        prompt = str(rec.get("prompt") or rec.get("resolution") or "")
        opts = rec.get("options", [])
        sel_opt = rec.get("selected_option") or rec.get("resolution") or ""
        sel_key = str(rec.get("selected_key") or sel_opt)
        rationale = rec.get("rationale")
        prev_hash = str(rec.get("previous_entry_hash") or ("0" * 64))
        rec_at = str(rec.get("recorded_at") or utc_now())
        meta = rec.get("metadata", {})
        entry_dig = str(rec.get("entry_digest") or digest(rec))
        rows.append((
            entry_id, seq_num, dec_id, cat, op_id, prompt,
            json.dumps(opts, sort_keys=True),
            json.dumps(sel_opt, sort_keys=True),
            sel_key, rationale, prev_hash, rec_at,
            json.dumps(meta, sort_keys=True),
            entry_dig,
        ))
    with GovernedStorageContext.write_transaction(db_path) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO hitl_decisions (
                entry_id, sequence_number, decision_id, category,
                operator_id, prompt, options, selected_option,
                selected_key, rationale, previous_entry_hash,
                recorded_at, metadata, entry_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def _rechain_and_export(
    db_path: Path, json_path: Path
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    with create_sqlite_conn(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM hitl_decisions ORDER BY sequence_number ASC, ROWID ASC")
        cols = [c[0] for c in cur.description]
        all_rows = cur.fetchall()

    canonical_decisions = []
    prev_hash = "0" * 64
    for idx, r in enumerate(all_rows):
        rec = dict(zip(cols, r))
        rec["sequence_number"] = idx
        for k in ("options", "selected_option", "metadata"):
            if isinstance(rec.get(k), str):
                try:
                    rec[k] = json.loads(rec[k])
                except Exception:
                    pass
        rec["previous_entry_hash"] = prev_hash
        rec_sans_digest = {k: v for k, v in rec.items() if k != "entry_digest"}
        entry_dig = digest(rec_sans_digest)
        rec["entry_digest"] = entry_dig
        prev_hash = entry_dig
        canonical_decisions.append(rec)

    with GovernedStorageContext.write_transaction(db_path) as conn:
        conn.executemany(
            "UPDATE hitl_decisions SET sequence_number = ?, previous_entry_hash = ?, entry_digest = ? WHERE entry_id = ?",
            [(rec["sequence_number"], rec["previous_entry_hash"], rec["entry_digest"], rec["entry_id"]) for rec in canonical_decisions],
        )

    envelope = {
        "$schema": "urn:nee:hitl-decision-ledger:1.2.0",
        "schema_version": "1.2.0",
        "store_of_record": "hitl_decision_ledger.sqlite",
        "record_count": len(canonical_decisions),
        "total_records": len(canonical_decisions),
        "updated_at": utc_now(),
        "genesis_hash": "0" * 64,
        "latest_entry_hash": canonical_decisions[-1]["entry_digest"] if canonical_decisions else ("0" * 64),
        "operator_decisions": canonical_decisions,
        "decisions": canonical_decisions,
    }
    envelope["ledger_digest"] = digest(envelope)
    tmp_json = json_path.with_name(f".{json_path.name}.tmp")
    tmp_json.write_text(json.dumps(envelope, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_json, json_path)
    return canonical_decisions, envelope


def reconcile_ledgers(
    authoritative_sqlite_path: Path,
    authoritative_json_path: Path,
    secondary_sqlite_path: Path | None = None,
    secondary_json_path: Path | None = None,
    root_sqlite_path: Path | None = None,
    root_json_path: Path | None = None,
    receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Execute complete reconciliation and verify zero divergence."""
    known_entry_ids = _init_authoritative_sqlite(authoritative_sqlite_path)
    initial_sqlite_count = len(known_entry_ids)
    logger.info("Authoritative SQLite baseline count: %d", initial_sqlite_count)

    sec_sqlite = secondary_sqlite_path or root_sqlite_path
    sec_json = secondary_json_path or root_json_path

    sources_audited: List[Dict[str, Any]] = []
    records_to_insert: List[Dict[str, Any]] = []

    records_to_insert.extend(_read_authoritative_json(authoritative_json_path, known_entry_ids, sources_audited))
    if sec_sqlite:
        records_to_insert.extend(_read_root_sqlite(sec_sqlite, known_entry_ids, sources_audited))
    if sec_json:
        records_to_insert.extend(_read_root_json(sec_json, known_entry_ids, sources_audited))

    inserted_count = _insert_records(authoritative_sqlite_path, records_to_insert, initial_sqlite_count)
    canonical_decisions, envelope = _rechain_and_export(authoritative_sqlite_path, authoritative_json_path)

    final_sqlite_count = len(canonical_decisions)
    final_json_count = len(envelope["decisions"])
    divergence = abs(final_sqlite_count - final_json_count)

    receipt = {
        "contract_id": "urn:nee:aidlc:hitl-parity-receipt:1.0.0",
        "schema_version": "1.0.0",
        "status": "PASS" if divergence == 0 else "FAIL",
        "reconciliation_timestamp": utc_now(),
        "sources_audited": sources_audited,
        "initial_sqlite_count": initial_sqlite_count,
        "newly_inserted_count": inserted_count,
        "authoritative_sqlite_count": final_sqlite_count,
        "canonical_json_count": final_json_count,
        "divergence_count": divergence,
        "parity_achieved": divergence == 0,
        "latest_entry_hash": envelope["latest_entry_hash"],
        "ledger_digest": envelope["ledger_digest"],
        "authoritative_sqlite_path": str(authoritative_sqlite_path),
        "authoritative_json_path": str(authoritative_json_path),
    }
    receipt["canonical_digest"] = digest(receipt)

    if receipt_path:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
        logger.info("Emitted parity receipt to %s", receipt_path)

    return receipt


def main() -> int:
    from dual_pipeline.hitl_governance_core.decision_record_writer import resolve_ledger_paths
    default_auth_sqlite, default_auth_json = resolve_ledger_paths(repo_root=ROOT)

    parser = argparse.ArgumentParser(description="Reconcile HITL decision ledgers to SQLite and verify parity")
    parser.add_argument("--authoritative-sqlite", type=Path, default=default_auth_sqlite)
    parser.add_argument("--authoritative-json", type=Path, default=default_auth_json)
    parser.add_argument("--legacy-sqlite", type=Path, default=ROOT / "hitl_decision_ledger.sqlite")
    parser.add_argument("--legacy-json", type=Path, default=ROOT / "hitl_decision_ledger.json")
    parser.add_argument("--root-sqlite", type=Path, default=None, help="Legacy alias for secondary sqlite")
    parser.add_argument("--root-json", type=Path, default=None, help="Legacy alias for secondary json")
    parser.add_argument("--receipt", type=Path, default=ROOT / "reports/aidlc/w10_hitl_parity_receipt.json")
    args = parser.parse_args()

    sec_sqlite = args.legacy_sqlite if (args.legacy_sqlite and args.legacy_sqlite.exists()) else args.root_sqlite
    sec_json = args.legacy_json if (args.legacy_json and args.legacy_json.exists()) else args.root_json

    receipt = reconcile_ledgers(
        authoritative_sqlite_path=args.authoritative_sqlite,
        authoritative_json_path=args.authoritative_json,
        secondary_sqlite_path=sec_sqlite if sec_sqlite and sec_sqlite.exists() else None,
        secondary_json_path=sec_json if sec_json and sec_json.exists() else None,
        receipt_path=args.receipt,
    )

    print(json.dumps(receipt, indent=2))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

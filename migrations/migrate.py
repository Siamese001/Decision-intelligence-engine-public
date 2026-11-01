#!/usr/bin/env python3
"""Apply, validate, or roll back the digest-bound NEE V48 integrated migration.

The migration is intentionally one integrated release transaction with internally
ordered phases. It preserves every V47.2 legacy record, applies only the approved
rowsets and eight due-date updates, and is safe to run repeatedly.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

BASELINE_DB_SHA256 = "2a55957f0164140eef2bbb519c94b12a5f5bbc3a206faf13d4cc9d69d4274f09"
G1_SHA256 = "13f99411128954538589c8be241f341df9e97d3578dde486432e3a76dd213c7a"
C1_SHA256 = "bc643ccabd9e769d2bc2cf60b01a489dfb8f38553e6d793051ce6cf7d4c058f9"
O1_SHA256 = "b61f9fef1cf36ede9ee211036389de4a08b49a349f87d26d94d17b6648028486"
MIGRATION_ID = "0001_integrated_hardening"
ROWSET_ORDER = [
    "write_control", "node_type_registry", "edge_type_registry",
    "edge_type_domain_range", "property_registry", "controlled_value_registry",
    "node_state", "source_version", "extraction_run", "assertion",
    "evidence_item", "assertion_evidence", "legacy_claim_assertion_map",
    "legacy_edge_assertion_map", "canonical_alias", "legacy_alias_canonical_map",
    "entity_resolution_candidate", "entity_resolution_candidate_member",
    "canonical_route", "graph_quality_exception", "query_definition",
    "search_document",
]
LEGACY_TABLES = ["node", "source", "card", "edge", "claim", "alias", "node_card_map", "validation_queue"]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def canonical_proposal_digest(path: Path) -> str:
    value = json.loads(path.read_text(encoding="utf-8"))
    value.pop("canonical_serialization_sha256", None)
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def table_rows(con: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    columns = [row[1] for row in con.execute(f'PRAGMA table_info("{table}")')]
    if not columns:
        raise RuntimeError(f"Missing table: {table}")
    rows = [dict(zip(columns, row)) for row in con.execute(
        "SELECT " + ",".join(f'"{c}"' for c in columns) + f' FROM "{table}"'
    )]
    rows.sort(key=canonical_bytes)
    return rows


def table_digest(con: sqlite3.Connection, table: str) -> dict[str, Any]:
    rows = table_rows(con, table)
    return {
        "row_count": len(rows),
        "canonical_rows_sha256": hashlib.sha256(canonical_bytes(rows)).hexdigest(),
    }


def insert_jsonl(con: sqlite3.Connection, path: Path, table: str) -> int:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        return 0
    columns = list(rows[0])
    if any(list(row) != columns for row in rows):
        raise RuntimeError(f"Inconsistent JSONL column order for {table}")
    sql = f'INSERT INTO "{table}" ({",".join(columns)}) VALUES ({",".join("?" for _ in columns)})'
    con.executemany(sql, [[row[col] for col in columns] for row in rows])
    return len(rows)


def paths(package_root: Path) -> dict[str, Path]:
    migration = package_root / "migrations" / MIGRATION_ID
    approvals_dir = package_root / "approvals" / "v48"
    if not approvals_dir.exists():
        approvals_dir = package_root / "archive" / "approvals" / "v48"
    return {
        "root": package_root,
        "migration": migration,
        "data": migration / "data",
        "approvals": approvals_dir,
        "cards": package_root / "cards",
        "rollback_db": package_root / "rollback" / "baseline" / "nee_graph_v47.2.sqlite",
    }


def verify_governance(p: dict[str, Path]) -> dict[str, Any]:
    approvals = p["approvals"]
    checks = {
        "g1": canonical_proposal_digest(approvals / "G1_INTEGRATED_PROPOSED_GRAPH_DELTA.json"),
        "c1": canonical_proposal_digest(approvals / "C1_ZERO_CARD_DELTA.json"),
        "o1": sha256_file(approvals / "GATE_O1_INLINE_TABLES.md"),  # noqa: SSOT_EXEMPT
    }
    expected = {"g1": G1_SHA256, "c1": C1_SHA256, "o1": O1_SHA256}  # noqa: SSOT_EXEMPT
    if checks != expected:
        raise RuntimeError(f"Governance digest mismatch: actual={checks}, expected={expected}")
    return {**checks, "result": "PASS"}


def verify_cards(p: dict[str, Path]) -> dict[str, Any]:
    manifest = json.loads((p["data"] / "baseline_card_manifest.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    for item in manifest["files"]:
        card = p["cards"] / item["path"]
        if not card.is_file() or card.stat().st_size != item["size_bytes"] or sha256_file(card) != item["sha256"]:
            failures.append(item["path"])
    if failures:
        raise RuntimeError(f"Card zero-delta failure: {failures}")
    return {"files_checked": len(manifest["files"]), "failures": [], "result": "PASS"}


def validate_database(database: Path, package_root: Path) -> dict[str, Any]:
    p = paths(package_root)
    governance = verify_governance(p)
    cards = verify_cards(p)
    baseline = json.loads((p["data"] / "baseline_table_digests.json").read_text(encoding="utf-8"))
    due_updates = json.loads((p["data"] / "validation_due_date_updates.json").read_text(encoding="utf-8"))["updates"]
    con = sqlite3.connect(f"file:{database.resolve().as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA foreign_keys=ON")

    integrity = [r[0] for r in con.execute("PRAGMA integrity_check")]
    quick = [r[0] for r in con.execute("PRAGMA quick_check")]
    fk = [list(r) for r in con.execute("PRAGMA foreign_key_check")]

    legacy: dict[str, Any] = {}
    for table in LEGACY_TABLES:
        if table == "validation_queue":
            continue
        actual = table_digest(con, table)
        expected = baseline[table]
        legacy[table] = {"expected": expected, "actual": actual, "result": "PASS" if actual == expected else "FAIL"}

    validation_count = con.execute("SELECT COUNT(*) FROM validation_queue").fetchone()[0]
    due_results: list[dict[str, Any]] = []
    for update in due_updates:
        row = con.execute(
            "SELECT due_date,owner,priority,status FROM validation_queue WHERE validation_id=?",
            (update["validation_id"],),
        ).fetchone()
        ok = row is not None and row["due_date"] == update["proposed_due_date"]
        due_results.append({
            "validation_id": update["validation_id"],
            "expected_due_date": update["proposed_due_date"],
            "actual_due_date": None if row is None else row["due_date"],
            "result": "PASS" if ok else "FAIL",
        })

    rowsets: dict[str, Any] = {}
    for table in ROWSET_ORDER:
        expected_rows = [json.loads(line) for line in (p["data"] / "proposed_rowsets" / f"{table}.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        expected_rows.sort(key=canonical_bytes)
        actual_rows = table_rows(con, table)
        expected_digest = hashlib.sha256(canonical_bytes(expected_rows)).hexdigest()
        actual_digest = hashlib.sha256(canonical_bytes(actual_rows)).hexdigest()
        ok = len(expected_rows) == len(actual_rows) and expected_digest == actual_digest
        rowsets[table] = {
            "expected_count": len(expected_rows), "actual_count": len(actual_rows),
            "expected_sha256": expected_digest, "actual_sha256": actual_digest,
            "result": "PASS" if ok else "FAIL",
        }

    fts = {
        "search_document": con.execute("SELECT COUNT(*) FROM search_document").fetchone()[0],
        "search_fts": con.execute("SELECT COUNT(*) FROM search_fts").fetchone()[0],
    }
    fts["result"] = "PASS" if fts["search_document"] == fts["search_fts"] else "FAIL"
    trigger_count = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'search_document_%'").fetchone()[0]
    stakeholder_sql = con.execute("SELECT sql FROM sqlite_master WHERE type='view' AND name='v_stakeholder_map'").fetchone()[0]
    currentness_leakage = {
        "v_stakeholder_map_missing_current_filter": 0 if "e.superseded_by IS NULL" in stakeholder_sql else 1,
        "v_hitl_items_superseded": con.execute("""
            SELECT COUNT(*)
            FROM v_hitl_items v
            LEFT JOIN claim c ON v.item_type='claim' AND c.claim_id=v.item_id
            LEFT JOIN edge e ON v.item_type='edge' AND e.edge_id=v.item_id
            WHERE coalesce(c.superseded_by,e.superseded_by) IS NOT NULL
        """).fetchone()[0],
    }
    con.close()

    failures = []
    if integrity != ["ok"] or quick != ["ok"] or fk:
        failures.append("integrity")
    failures += [f"legacy:{k}" for k, v in legacy.items() if v["result"] != "PASS"]
    if validation_count != baseline["validation_queue"]["row_count"] or any(x["result"] != "PASS" for x in due_results):
        failures.append("validation_queue")
    failures += [f"rowset:{k}" for k, v in rowsets.items() if v["result"] != "PASS"]
    if fts["result"] != "PASS" or trigger_count != 3:
        failures.append("fts")
    if any(currentness_leakage.values()):
        failures.append("currentness")

    return {
        "migration_id": MIGRATION_ID,
        "database": str(database),
        "database_sha256": sha256_file(database),
        "validated_at": utc_now(),
        "governance": governance,
        "cards": cards,
        "integrity_check": integrity,
        "quick_check": quick,
        "foreign_key_violations": fk,
        "legacy_tables": legacy,
        "validation_queue": {"row_count": validation_count, "approved_due_date_updates": due_results},
        "approved_rowsets": rowsets,
        "fts": fts,
        "fts_trigger_count": trigger_count,
        "currentness_leakage": currentness_leakage,
        "failures": failures,
        "result": "PASS" if not failures else "FAIL",
    }


def apply_migration(database: Path, package_root: Path, backup: Path) -> dict[str, Any]:
    p = paths(package_root)
    verify_governance(p)
    verify_cards(p)
    if not database.is_file():
        raise FileNotFoundError(database)

    con = sqlite3.connect(database)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA recursive_triggers=ON")
    already = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='assertion'").fetchone() is not None
    con.close()
    if already:
        validation = validate_database(database, package_root)
        if validation["result"] != "PASS":
            raise RuntimeError("Existing V48 database failed conformance")
        return {"status": "idempotent_noop", "validation": validation}

    actual_hash = sha256_file(database)
    if actual_hash != BASELINE_DB_SHA256:
        raise RuntimeError(f"Input is not the approved V47.2 baseline: {actual_hash}")
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(database, backup)
    if sha256_file(backup) != BASELINE_DB_SHA256:
        raise RuntimeError("Pre-migration backup verification failed")

    con = sqlite3.connect(database)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA recursive_triggers=ON")
    views = [(r[0], r[1]) for r in con.execute("SELECT name,sql FROM sqlite_master WHERE type='view' ORDER BY name")]
    counts: dict[str, int] = {}
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("PRAGMA legacy_alter_table=ON")
        con.execute("BEGIN IMMEDIATE")
        for name, _ in views:
            con.execute(f'DROP VIEW IF EXISTS "{name}"')
        con.executescript((p["data"] / "legacy_table_rebuild.sql").read_text(encoding="utf-8"))
        for _, sql in views:
            con.execute(sql)
        con.commit()

        con.execute("PRAGMA legacy_alter_table=OFF")
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA recursive_triggers=ON")
        con.executescript((p["data"] / "v48_schema.sql").read_text(encoding="utf-8"))

        con.execute("BEGIN IMMEDIATE")
        for table in ROWSET_ORDER:
            counts[table] = insert_jsonl(con, p["data"] / "proposed_rowsets" / f"{table}.jsonl", table)
        updates = json.loads((p["data"] / "validation_due_date_updates.json").read_text(encoding="utf-8"))["updates"]
        for update in updates:
            cur = con.execute(
                "UPDATE validation_queue SET due_date=? WHERE validation_id=? AND due_date IS NULL",
                (update["proposed_due_date"], update["validation_id"]),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"Expected one approved due-date update for {update['validation_id']}; got {cur.rowcount}")
        con.executescript((p["data"] / "currentness_safe_views.sql").read_text(encoding="utf-8"))
        con.commit()
    except Exception:
        try:
            con.rollback()
        finally:
            con.close()
        shutil.copy2(backup, database)
        raise
    con.close()

    validation = validate_database(database, package_root)
    if validation["result"] != "PASS":
        shutil.copy2(backup, database)
        raise RuntimeError(f"Post-migration validation failed: {validation['failures']}")
    return {
        "status": "applied",
        "migration_id": MIGRATION_ID,
        "applied_at": utc_now(),
        "row_counts": counts,
        "approved_due_date_updates": 8,
        "backup": {"path": str(backup), "sha256": sha256_file(backup)},
        "validation": validation,
    }


def rollback_migration(database: Path, package_root: Path) -> dict[str, Any]:
    p = paths(package_root)
    baseline = p["rollback_db"]
    if sha256_file(baseline) != BASELINE_DB_SHA256:
        raise RuntimeError("Bundled rollback baseline hash mismatch")
    tmp = database.with_suffix(database.suffix + ".rollback.tmp")
    shutil.copy2(baseline, tmp)
    if sha256_file(tmp) != BASELINE_DB_SHA256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Rollback copy verification failed")
    tmp.replace(database)
    return {
        "status": "rolled_back",
        "database": str(database),
        "database_sha256": sha256_file(database),
        "expected_sha256": BASELINE_DB_SHA256,
        "completed_at": utc_now(),
        "result": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["apply", "validate", "rollback"])
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--package-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.package_root.resolve()
    db = args.database.resolve()
    if args.action == "apply":
        backup = (args.backup or db.with_suffix(db.suffix + ".pre_v48.bak")).resolve()
        result = apply_migration(db, root, backup)
    elif args.action == "validate":
        result = validate_database(db, root)
    else:
        result = rollback_migration(db, root)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result.get("result", result.get("validation", {}).get("result", "PASS")) == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

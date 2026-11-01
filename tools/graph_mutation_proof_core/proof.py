"""Physical proof that a graph mutation reached the database file on disk.

Five checks, all required:
1. File changed (size, mtime, sha256 differ).
2. Written IDs found by scanning raw file bytes (no SQLite driver).
3. PRAGMA integrity_check returns ok.
4. Fresh read-only connection returns the written rows.
5. Per-table row counts changed by exactly the expected amounts.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from storage.connection import create_sqlite_conn

SCHEMA_VERSION = "1.0.0"
_CHUNK = 8 * 1024 * 1024
_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_]+$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def table_names(path: Path, tables: Optional[Iterable[str]]) -> List[str]:
    if tables is not None:
        return list(tables)
    conn = create_sqlite_conn(path, readonly=True)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%\\_fts%' ESCAPE '\\'"
        ).fetchall()
    finally:
        conn.close()
    return [str(r[0]) for r in rows]


def _row_counts(path: Path, tables: List[str]) -> Dict[str, int]:
    conn = create_sqlite_conn(path, readonly=True)
    try:
        counts: Dict[str, int] = {}
        for t in tables:
            if not _IDENTIFIER_RE.match(t):
                raise ValueError(f"Invalid table identifier: {t!r}")
            row = conn.execute('SELECT count(*) FROM "%s"' % t).fetchone()
            counts[t] = int(row[0]) if row else 0
        return counts
    finally:
        conn.close()


_CANONICAL_METRIC_QUERIES: tuple[tuple[str, str, str], ...] = (
    ("canonical_nodes", "node", "SELECT count(*) FROM node WHERE status='canonical'"),
    ("approved_node_states", "node_state", "SELECT count(*) FROM node_state WHERE approval_state='approved'"),
    (
        "canonical_assertions",
        "assertion",
        "SELECT count(*) FROM assertion WHERE canonical_status='canonical' AND canonical_eligible=1",
    ),
    (
        "canonical_relationships",
        "assertion",
        "SELECT count(*) FROM assertion WHERE canonical_status='canonical' AND canonical_eligible=1 AND object_node_id IS NOT NULL",
    ),
    (
        "canonical_search_documents",
        "search_document",
        "SELECT count(*) FROM search_document WHERE canonical_status='canonical' AND retrieval_eligible=1",
    ),
)


def _canonical_counts(path: Path, tables: List[str]) -> Dict[str, int]:
    table_set = set(tables)
    conn = create_sqlite_conn(path, readonly=True)
    try:
        counts: Dict[str, int] = {}
        for metric, table_name, sql in _CANONICAL_METRIC_QUERIES:
            if table_name not in table_set:
                continue
            try:
                row = conn.execute(sql).fetchone()
                counts[metric] = int(row[0]) if row else 0
            except Exception:
                continue
        return counts
    finally:
        conn.close()


def snapshot(db_path: Path | str, tables: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Capture file state, row counts, and canonical promotion counts before or after a write."""
    path = Path(db_path)
    stat = path.stat()
    names = table_names(path, tables)
    return {
        "path": str(path),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": _sha256(path),
        "row_counts": _row_counts(path, names),
        "canonical_counts": _canonical_counts(path, names),
    }


def raw_byte_scan(db_path: Path | str, ids: Iterable[str]) -> Dict[str, Optional[int]]:
    """Return the file byte offset of each ID, or None. Reads the file, not SQLite.

    The sibling -wal file is deliberately not scanned: IDs only in the WAL are not
    yet in the main database file and must not count as physically committed.
    """
    wanted = {i: i.encode("utf-8") for i in ids}
    found: Dict[str, Optional[int]] = {i: None for i in wanted}
    if not wanted:
        return found
    overlap = max((len(v) for v in wanted.values()), default=1)
    offset = 0
    tail = b""
    with open(db_path, "rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            buf = tail + block
            base = offset - len(tail)
            for key, needle in wanted.items():
                if found[key] is None:
                    pos = buf.find(needle)
                    if pos != -1:
                        found[key] = base + pos
            tail = buf[-overlap:]
            offset += len(block)
    return found


def _integrity(db_path: Path) -> str:
    conn = create_sqlite_conn(db_path, readonly=True)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "unknown"
    finally:
        conn.close()


def _readback(db_path: Path, probes: Mapping[str, tuple]) -> Dict[str, bool]:
    """probes: name -> (sql, params); passes when at least one row returns."""
    conn = create_sqlite_conn(db_path, readonly=True)
    try:
        return {n: conn.execute(sql, params).fetchone() is not None for n, (sql, params) in probes.items()}
    finally:
        conn.close()


def _check(name: str, passed: bool, detail: Any) -> Dict[str, Any]:
    return {"check": name, "status": "PASS" if passed else "FAIL", "detail": detail}


def _delta_check(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    expected: Mapping[str, int],
    strict: bool = True,
) -> Dict[str, Any]:
    """strict=True: every table must match (unlisted tables must be unchanged).

    strict=False: only listed tables are compared. Writers use this with the
    audit_event table because each governed operation writes exactly one audit
    row while touching a variable set of other tables.
    """
    actual = {
        t: after["row_counts"].get(t, 0) - before["row_counts"].get(t, 0)
        for t in set(before["row_counts"]) | set(after["row_counts"])
    }
    if strict:
        observed = {t: d for t, d in actual.items() if d != 0}
        wanted = {t: d for t, d in expected.items() if d != 0}
    else:
        observed = {t: actual.get(t, 0) for t in expected}
        wanted = dict(expected)
    return _check("row_delta", observed == wanted, {"expected": wanted, "actual": observed, "strict": strict})


def prove(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    expected_ids: Iterable[str],
    expected_deltas: Mapping[str, int],
    readback_probes: Optional[Mapping[str, tuple]] = None,
    strict_delta: bool = True,
) -> Dict[str, Any]:
    """Compare snapshots and return a receipt. Overall PASS only if all five pass."""
    ids = list(expected_ids)
    db_path = Path(after["path"])
    changed = before["sha256"] != after["sha256"]
    offsets = raw_byte_scan(db_path, ids)
    missing = [i for i, o in offsets.items() if o is None]
    probes = _readback(db_path, readback_probes or {})
    integrity = _integrity(db_path)
    checks = [
        _check("file_changed", changed, {"before_sha256": before["sha256"], "after_sha256": after["sha256"],
                                         "before_size": before["size_bytes"], "after_size": after["size_bytes"],
                                         "before_mtime_ns": before["mtime_ns"], "after_mtime_ns": after["mtime_ns"]}),
        _check("raw_bytes", bool(ids) and not missing, {"offsets": offsets, "missing": missing}),
        _check("integrity", integrity == "ok", integrity),
        _check("readback", bool(probes) and all(probes.values()), probes),
        _delta_check(before, after, expected_deltas, strict_delta),
    ]
    overall = "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL"
    before_canonical = before.get("canonical_counts") or {}
    after_canonical = after.get("canonical_counts") or {}
    canonical_deltas = {
        k: after_canonical.get(k, 0) - before_canonical.get(k, 0)
        for k in sorted(set(before_canonical) | set(after_canonical))
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "database": after["path"],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "overall": overall,
        "checks": checks,
        "canonical_deltas": canonical_deltas,
    }

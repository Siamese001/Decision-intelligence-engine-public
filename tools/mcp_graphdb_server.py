"""Governed GraphDB & Sidecar State MCP Server (Rank 1).

Exposes live GraphDB introspection, snapshot state capture, 14-table mutation
breakdown generation, sidecar vector synchronization checks, and SQLite lock
diagnostics as native Model Context Protocol (MCP) tools.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE_ROOT))

from dual_pipeline.core import sha256_file  # noqa: E402
from dual_pipeline.graph_delta import (  # noqa: E402
    _COMPONENTS,
    _SIDECAR_COMPONENTS,
    _graph_counts,
    _sidecar_counts,
    build_itemized_assertion_delta,
    inspect_vector_health,
)
from storage.connection import (  # noqa: E402
    GraphUnavailableError,
    create_sqlite_conn,
    sqlite3,
    validate_canonical_graph_db,
)

server = MCPServer(
    name="graphdb-inspector",
    instructions=(
        "Governed GraphDB & Sidecar State Server — provides live SQLite inspection, "
        "snapshot state capture, automated 14-table mutation breakdown generation, "
        "sidecar vector synchronization verification, and concurrency lock diagnostics."
    ),
)

# In-memory snapshot cache keyed by snapshot ID
_SNAPSHOT_CACHE: dict[str, dict[str, Any]] = {}


def _is_safe_db_path(p: Path | str) -> bool:
    """Validate path is strictly within workspace, main repository, or system temp directory."""
    resolved = Path(p).resolve()
    temp_dir = Path(tempfile.gettempdir()).resolve()
    ws_dir = WORKSPACE_ROOT.resolve()
    try:
        from dual_pipeline.runtime_root import resolve_main_repository_root
        main_repo = resolve_main_repository_root(ws_dir)
        if resolved.is_relative_to(main_repo):
            return True
    except Exception:
        pass
    return resolved.is_relative_to(ws_dir) or resolved.is_relative_to(temp_dir)


def _scan_candidate_graphs(workspace_root: Path) -> Path | None:
    try:
        from dual_pipeline.runtime_root import resolve_active_graph_path
        return validate_canonical_graph_db(resolve_active_graph_path(enforce_integrity=False))
    except Exception:
        pass

    active_pair_file = workspace_root / ".local" / "dual_pipeline" / "active_pair.json"
    if active_pair_file.is_file():
        try:
            data = json.loads(active_pair_file.read_text(encoding="utf-8"))
            candidate = Path(data.get("graph_path", ""))
            if not candidate.is_absolute():
                candidate = active_pair_file.parent / candidate
            if candidate.is_file() and _is_safe_db_path(candidate):
                return validate_canonical_graph_db(candidate)
        except Exception:
            pass

    for cand in (workspace_root / "graph" / "nee_graph.sqlite", workspace_root / "storage_data" / "canonical_graph.db"):
        if cand.is_file():
            try:
                return validate_canonical_graph_db(cand)
            except Exception:
                pass
    return None


def _resolve_default_graph_path(db_path: str = "") -> Path:
    """Resolve graph database path, defaulting to active pair or standard graph."""
    if db_path:
        p = Path(db_path)
        if not p.is_absolute():
            p = WORKSPACE_ROOT / p
        if not _is_safe_db_path(p):
            raise PermissionError(f"Access denied: path '{p}' outside workspace root '{WORKSPACE_ROOT}'")
        return validate_canonical_graph_db(p)

    cand = _scan_candidate_graphs(WORKSPACE_ROOT)
    if cand:
        return cand
    raise GraphUnavailableError(
        "GRAPH_UNAVAILABLE: No valid canonical graph database found (must be >= 1MB with assertion table)"
    )


def _candidate_sidecar_from_pair(data: dict, base_dir: Path) -> Path | None:
    """Resolve candidate sidecar path from active pair dictionary."""
    raw_path = data.get("sidecar_path") or data.get("index_path", "")
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    if candidate.is_file() and _is_safe_db_path(candidate):
        return candidate
    return None


def _resolve_default_sidecar_path(sidecar_path: str = "") -> Path:
    """Resolve sidecar index database path."""
    if sidecar_path:
        p = Path(sidecar_path)
        if not p.is_absolute():
            p = WORKSPACE_ROOT / p
        if not _is_safe_db_path(p):
            raise PermissionError(f"Access denied: path '{p}' outside workspace root '{WORKSPACE_ROOT}'")
        return p

    try:
        from dual_pipeline.runtime_root import resolve_active_pair
        data, actual_pair_path = resolve_active_pair(enforce_integrity=False)
        cand = _candidate_sidecar_from_pair(data, actual_pair_path.parent)
        if cand:
            return cand
    except Exception:
        pass

    active_pair_file = WORKSPACE_ROOT / ".local" / "dual_pipeline" / "active_pair.json"
    if active_pair_file.is_file():
        try:
            data = json.loads(active_pair_file.read_text(encoding="utf-8"))
            cand = _candidate_sidecar_from_pair(data, active_pair_file.parent)
            if cand:
                return cand
        except Exception:
            pass

    default_sidecar = WORKSPACE_ROOT / "graph" / "nee_index.sqlite"
    if default_sidecar.is_file():
        return default_sidecar
    return WORKSPACE_ROOT / ".local" / "dual_pipeline" / "index.db"


@server.tool(name="graphdb_snapshot_state")
def graphdb_snapshot_state(snapshot_id: str, db_path: str = "", sidecar_path: str = "") -> str:
    """Capture row counts and SHA-256 digests for snapshot comparison."""
    try:
        g_path = _resolve_default_graph_path(db_path)
        s_path = _resolve_default_sidecar_path(sidecar_path)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    if not g_path.is_file():
        return json.dumps({"error": f"Graph database file not found at {g_path}"})

    snapshot = {
        "snapshot_id": snapshot_id,
        "graph_path": str(g_path),
        "sidecar_path": str(s_path),
        "graph_sha256": sha256_file(g_path) if g_path.is_file() else "FILE_NOT_FOUND",
        "sidecar_sha256": sha256_file(s_path) if s_path.is_file() else "FILE_NOT_FOUND",
        "graph_counts": _graph_counts(g_path),
        "sidecar_counts": _sidecar_counts(s_path),
    }
    _SNAPSHOT_CACHE[snapshot_id] = snapshot
    return json.dumps(snapshot, indent=2)


@server.tool(name="graphdb_diff_breakdown")
def graphdb_diff_breakdown(baseline_snapshot_id: str, activated_snapshot_id: str) -> str:
    """Generate 14-table delta breakdown between two snapshots."""
    if baseline_snapshot_id not in _SNAPSHOT_CACHE:
        return f"Error: Baseline snapshot '{baseline_snapshot_id}' not found in cache. Available: {list(_SNAPSHOT_CACHE.keys())}"
    if activated_snapshot_id not in _SNAPSHOT_CACHE:
        return f"Error: Activated snapshot '{activated_snapshot_id}' not found in cache. Available: {list(_SNAPSHOT_CACHE.keys())}"

    base = _SNAPSHOT_CACHE[baseline_snapshot_id]
    act = _SNAPSHOT_CACHE[activated_snapshot_id]

    b_graph = base["graph_counts"]
    a_graph = act["graph_counts"]
    b_side = base["sidecar_counts"]
    a_side = act["sidecar_counts"]

    lines = [
        "### Granular Graph DB Delta Table",
        "",
        "| SQLite Table | Baseline Count | Activated Count | Delta | What Changed |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    # Graph tables
    for key, label, target_table in _COMPONENTS:
        b_val = b_graph.get(key, 0)
        a_val = a_graph.get(key, 0)
        delta = a_val - b_val
        delta_str = f"+{delta}" if delta > 0 else str(delta)
        status = "Unchanged" if delta == 0 else f"{delta_str} {label.lower()}"
        lines.append(f"| `{target_table}` ({key}) | {b_val} | {a_val} | `{delta_str}` | {status} |")

    # Sidecar tables
    for key, label, target_table in _SIDECAR_COMPONENTS:
        b_val = b_side.get(key, 0)
        a_val = a_side.get(key, 0)
        delta = a_val - b_val
        delta_str = f"+{delta}" if delta > 0 else str(delta)
        status = "Unchanged" if delta == 0 else f"{delta_str} {label.lower()}"
        lines.append(f"| `{target_table}` | {b_val} | {a_val} | `{delta_str}` | {status} |")

    lines.append("")
    lines.append("### Idempotency & Hash Transition")
    lines.append(f"- **Baseline Graph Hash**: `{base['graph_sha256']}`")
    lines.append(f"- **Activated Graph Hash**: `{act['graph_sha256']}`")
    if base["graph_sha256"] == act["graph_sha256"]:
        lines.append("- **Status**: `IDEMPOTENT_NOOP` (Active pair remained strictly unchanged)")
    else:
        lines.append(
            "- **Status**: `MUTATION_RECORDED` (Graph state transitioned to new active hash)"
        )

    return "\n".join(lines)


def _is_sensitive_row(r: dict[str, Any], has_c19_marker: Any) -> bool:
    if r.get("namespace") == "INTERNAL_ADVISORY" or r.get("is_quarantined") in (1, True, "1"):
        return True
    return any(has_c19_marker(v) for v in r.values())


def _is_candidate_row(r: dict[str, Any]) -> bool:
    c_status = r.get("canonical_status")
    if c_status is not None and str(c_status).lower() not in ("canonical", "confirmed", "active"):
        return True
    if r.get("canonical_eligible") in (0, False, "0") or r.get("retrieval_eligible") in (0, False, "0"):
        return True
    c_state = r.get("currentness_state")
    if c_state is not None and str(c_state).lower() not in ("current",):
        return True
    return bool(r.get("superseded_by"))


def _is_garbled_or_polluted_row(r: dict[str, Any]) -> bool:
    from dual_pipeline.l0.speaker_and_qualifier_policy import (
        GARBLED_ASR_PROPER_NOUN_MARKERS,
        has_synthetic_or_cross_project_pollution,
    )

    text_blob = " ".join(
        str(r.get(k) or "")
        for k in ("scope_text", "object_literal", "body", "title", "claim_text", "excerpt_text", "raw_text")
    ).lower()
    if any(m in text_blob for m in GARBLED_ASR_PROPER_NOUN_MARKERS):
        return True
    return (
        has_synthetic_or_cross_project_pollution(
            record_id=str(r.get("assertion_id") or r.get("node_id") or "") or None,
            subject_node_id=str(r.get("subject_node_id") or "") or None,
            scope_text=text_blob or None,
            speaker_or_author=str(r.get("speaker_or_author") or "") or None,
            source_name=str(r.get("source_name") or "") or None,
        )
        is not None
    )


def _filter_sanitized_rows(
    results: list[dict[str, Any]], *, include_candidates: bool = False
) -> list[dict[str, Any]]:
    from dual_pipeline.live_meeting_recorder_core.c19_firewall import has_c19_marker
    sanitized = []
    for r in results:
        if not isinstance(r, dict) or _is_sensitive_row(r, has_c19_marker):
            continue
        if not include_candidates and (_is_candidate_row(r) or _is_garbled_or_polluted_row(r)):
            continue
        sanitized.append(r)
    return sanitized


def _filter_projected_assertion_rows(
    cur: Any, query: str, results: list[dict[str, Any]], *, include_candidates: bool
) -> list[dict[str, Any]]:
    if include_candidates or not results or not re.search(r"\bassertion\b", query, re.IGNORECASE):
        return results
    asrt_cols = {r[1] for r in cur.execute("PRAGMA table_info(assertion)").fetchall()}
    if not ({"assertion_id", "canonical_status", "canonical_eligible"} <= asrt_cols):
        return results
    aids = [
        str(r["assertion_id"])
        for r in results
        if r.get("assertion_id") is not None and ("canonical_status" not in r or "canonical_eligible" not in r)
    ]
    if not aids:
        return results
    ph = ",".join("?" for _ in aids)
    valid_aids = {
        str(r[0])
        for r in cur.execute(
            f"SELECT assertion_id FROM assertion WHERE assertion_id IN ({ph}) "
            "AND canonical_status = 'canonical' AND canonical_eligible = 1 "
            "AND COALESCE(retrieval_eligible, 1) = 1 AND COALESCE(currentness_state, 'current') = 'current'",
            aids,
        ).fetchall()
    }
    return [
        r
        for r in results
        if r.get("assertion_id") is None
        or ("canonical_status" in r and "canonical_eligible" in r)
        or str(r["assertion_id"]) in valid_aids
    ]


_CLAIM_DEPRECATION_WARNING = (
    "[DEPRECATION WARNING] Table 'claim' was retired in July 2026. "
    "Use 'assertion' or 'search_document' for active knowledge graph queries."
)


def _validate_read_query(clean: str, query: str, include_candidates: bool) -> str | None:
    if not clean.startswith(("SELECT", "EXPLAIN", "PRAGMA")):
        return "Error: Only read-only queries (SELECT, EXPLAIN, PRAGMA) are permitted."
    if any(m in clean for m in ("SHADOW_GRAPH", "INTERNAL_ADVISORY", "STEALTH_SCORE")):
        return "Error: Permission denied. Access to shadow graph tables and internal advisory data is prohibited."
    if not include_candidates and re.search(r"\b(?:FROM|JOIN)\s+claim\b", query, re.IGNORECASE):
        return (
            "Error: LEGACY_CLAIM_TABLE_BLOCKED: Direct queries against legacy table 'claim' are blocked "
            "when include_candidates=False because 'claim' contains unpromoted/superseded candidate records. "
            f"{_CLAIM_DEPRECATION_WARNING}"
        )
    return None


@server.tool(name="graphdb_read_query")
def graphdb_read_query(
    query: str, db_path: str = "", max_rows: int = 50, include_candidates: bool = False
) -> str:
    """Run read-only SQL query against graph DB."""
    clean = query.strip().upper()
    val_err = _validate_read_query(clean, query, include_candidates)
    if val_err:
        return val_err

    has_claim_ref = bool(re.search(r"\b(?:FROM|JOIN)\s+claim\b", query, re.IGNORECASE))
    try:
        g_path = _resolve_default_graph_path(db_path)
    except Exception as exc:
        msg = f"GRAPH_UNAVAILABLE: {exc}" if "GRAPH_UNAVAILABLE" in str(exc) else f"Error: {exc}"
        return f"{msg}\n{_CLAIM_DEPRECATION_WARNING}" if has_claim_ref else msg

    if not g_path.is_file():
        return f"Error: Database file not found at {g_path}"

    if not include_candidates:
        try:
            from dual_pipeline.fast_promotion import ensure_staged_candidates_promoted
            g_path, _ = ensure_staged_candidates_promoted(g_path)
        except Exception:
            pass

    try:
        conn = create_sqlite_conn(g_path, readonly=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(query)
        results = [dict(row) for row in cur.fetchmany(max_rows)]
        results = _filter_projected_assertion_rows(cur, query, results, include_candidates=include_candidates)
        conn.close()

        sanitized_results = _filter_sanitized_rows(results, include_candidates=include_candidates)
        output: dict[str, Any] = {"count": len(sanitized_results), "rows": sanitized_results}
        if has_claim_ref:
            output["deprecation_warning"] = _CLAIM_DEPRECATION_WARNING
        return json.dumps(output, indent=2)
    except Exception as exc:
        err_msg = f"Query execution error: {exc}"
        return f"{err_msg}\n{_CLAIM_DEPRECATION_WARNING}" if has_claim_ref else err_msg


@server.tool(name="graphdb_verify_sidecar_sync")
def graphdb_verify_sidecar_sync(db_path: str = "", sidecar_path: str = "") -> str:
    """Verify alignment between search documents and sidecar vectors."""
    try:
        g_path = _resolve_default_graph_path(db_path)
        s_path = _resolve_default_sidecar_path(sidecar_path)
    except Exception as exc:
        return json.dumps({"status": "ERROR", "reason": str(exc)})

    if not g_path.is_file():
        return json.dumps({"status": "ERROR", "reason": f"Graph database missing: {g_path}"})
    if not s_path.is_file():
        return json.dumps({"status": "ERROR", "reason": f"Sidecar database missing: {s_path}"})

    g_counts = _graph_counts(g_path)
    s_counts = _sidecar_counts(s_path)

    total_search_docs = 0
    candidate_search_docs = g_counts.get("candidate_search_document", 0)
    try:
        conn = create_sqlite_conn(g_path, readonly=True)
        cur = conn.execute("SELECT count(*) FROM search_document")
        total_search_docs = cur.fetchone()[0]
        conn.close()
    except Exception:
        total_search_docs = candidate_search_docs

    retrieval_records = s_counts.get("retrieval_record", 0)
    dense_vecs = s_counts.get("dense_vector", 0)
    sparse_weights = s_counts.get("sparse_weight", 0)
    colbert_tokens = s_counts.get("colbert_token", 0)

    # Both candidate and canonical search documents must be covered by sidecar records
    target_docs = max(total_search_docs, candidate_search_docs)
    is_synced = (retrieval_records == 0 and target_docs == 0) or (
        (dense_vecs >= target_docs or (retrieval_records > 0 and dense_vecs >= retrieval_records))
        and (
            sparse_weights >= target_docs
            or (retrieval_records > 0 and sparse_weights >= retrieval_records)
        )
    )

    return json.dumps(
        {
            "status": "IN_SYNC" if is_synced else "OUT_OF_SYNC",
            "search_documents": target_docs,
            "candidate_search_documents": candidate_search_docs,
            "total_search_documents": total_search_docs,
            "retrieval_records": retrieval_records,
            "dense_vectors": dense_vecs,
            "sparse_weights": sparse_weights,
            "colbert_tokens": colbert_tokens,
            "aligned": is_synced,
        },
        indent=2,
    )


@server.tool(name="graphdb_diagnose_locks")
def graphdb_diagnose_locks(db_path: str = "") -> str:
    """Diagnose SQLite journal mode, WAL state, and locks."""
    try:
        g_path = _resolve_default_graph_path(db_path)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
    if not g_path.is_file():
        return json.dumps({"error": f"Database file not found at {g_path}"})

    wal_file = g_path.with_name(f"{g_path.name}-wal")
    shm_file = g_path.with_name(f"{g_path.name}-shm")

    diagnostics: dict[str, Any] = {
        "database": str(g_path),
        "exists": g_path.is_file(),
        "size_bytes": g_path.stat().st_size if g_path.is_file() else 0,
        "wal_file_exists": wal_file.is_file(),
        "wal_size_bytes": wal_file.stat().st_size if wal_file.is_file() else 0,
        "shm_file_exists": shm_file.is_file(),
    }

    try:
        conn = create_sqlite_conn(g_path, readonly=True)
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        synchronous = conn.execute("PRAGMA synchronous;").fetchone()[0]
        conn.close()
        diagnostics.update(
            {
                "journal_mode": journal_mode,
                "busy_timeout_ms": busy_timeout,
                "synchronous": synchronous,
                "lock_status": "ACCESSIBLE_NO_LOCK_CONTENTION",
            }
        )
    except sqlite3.OperationalError as exc:
        diagnostics.update(
            {
                "lock_status": "LOCKED_OR_BUSY",
                "error": str(exc),
            }
        )

    return json.dumps(diagnostics, indent=2)


@server.tool(name="graphdb_itemized_assertion_delta")
def graphdb_itemized_assertion_delta(
    before_db_path: str = "",
    after_db_path: str = "",
    limit: int = 50,
) -> str:
    """Return itemized assertion delta between two graph DBs."""
    try:
        g_before = _resolve_default_graph_path(before_db_path) if before_db_path else None
        g_after = _resolve_default_graph_path(after_db_path)
    except Exception as exc:
        return json.dumps({"error": str(exc)})

    if not g_after.is_file():
        return json.dumps({"error": f"Target after graph database not found at {g_after}"})

    delta = build_itemized_assertion_delta(g_before, g_after, limit=limit)
    return json.dumps(delta, indent=2)


@server.tool(name="graphdb_inspect_vector_health")
def graphdb_inspect_vector_health(
    sidecar_path: str = "",
    sample_limit: int = 2000,
    expected_dense_dim: int = 1024,
    min_dense_norm: float = 0.8,
    max_dense_norm: float = 1.2,
) -> str:
    """Inspect sidecar vectors for NaNs, zeroes, and norm bounds."""
    s_path = _resolve_default_sidecar_path(sidecar_path)
    if not s_path.is_file():
        return json.dumps(
            {
                "status": "ERROR",
                "reason": f"Sidecar database file not found at {s_path}",
                "tables": {},
            }
        )

    result = inspect_vector_health(
        s_path,
        sample_limit=sample_limit,
        expected_dense_dim=expected_dense_dim,
        min_dense_norm=min_dense_norm,
        max_dense_norm=max_dense_norm,
    )
    return json.dumps(result, indent=2)


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

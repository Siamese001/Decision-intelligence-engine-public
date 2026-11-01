"""Vector health diagnostics for sidecar embeddings (dense, sparse, ColBERT)."""

from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import Any

from storage.database import create_sqlite_conn, sqlite3


def _unpack_floats(blob: bytes) -> tuple[float, ...] | None:
    """Safely unpack float32 little-endian blob."""
    if len(blob) % 4 != 0:
        return None
    count = len(blob) // 4
    try:
        return struct.unpack(f"<{count}f", blob)
    except Exception:
        return None


def _check_dense_floats(
    rid: str,
    dim: int,
    floats: tuple[float, ...],
    expected_dense_dim: int,
    min_dense_norm: float,
    max_dense_norm: float,
    enforce_unit_norm: bool,
    dense_stats: dict[str, Any],
    anomalies: list[dict[str, Any]],
) -> float | None:
    """Validate float values, dimensions, and L2 norm for a single dense vector."""
    if len(floats) != expected_dense_dim or dim != expected_dense_dim:
        dense_stats["dimension_mismatch_count"] += 1
        anomalies.append(
            {
                "table": "dense_vector",
                "record_id": rid,
                "issue": f"DIMENSION_MISMATCH (expected {expected_dense_dim}, got {len(floats)} floats, column={dim})",
            }
        )

    has_nan = any(math.isnan(x) for x in floats)
    has_inf = any(math.isinf(x) for x in floats)
    if has_nan:
        dense_stats["nan_count"] += 1
        anomalies.append({"table": "dense_vector", "record_id": rid, "issue": "NAN_DETECTED"})
    if has_inf:
        dense_stats["inf_count"] += 1
        anomalies.append({"table": "dense_vector", "record_id": rid, "issue": "INF_DETECTED"})

    if has_nan or has_inf:
        return None

    norm = math.sqrt(sum(x * x for x in floats))
    if math.isclose(norm, 0.0, abs_tol=1e-7):
        dense_stats["zero_vector_count"] += 1
        anomalies.append(
            {"table": "dense_vector", "record_id": rid, "issue": "ZERO_VECTOR_DETECTED"}
        )
        return None

    if enforce_unit_norm and (norm < min_dense_norm or norm > max_dense_norm):
        dense_stats["norm_out_of_bounds_count"] += 1
        anomalies.append(
            {
                "table": "dense_vector",
                "record_id": rid,
                "issue": f"NORM_OUT_OF_BOUNDS (norm={round(norm, 4)} outside [{min_dense_norm}, {max_dense_norm}])",
            }
        )
    return norm


def _append_dense_issues(
    dense_stats: dict[str, Any],
    min_dense_norm: float,
    max_dense_norm: float,
    issues: list[str],
) -> None:
    """Format and append detected dense vector issues to the global issues list."""
    if dense_stats["nan_count"] > 0:
        issues.append(f"dense_vector contains {dense_stats['nan_count']} NaN embeddings.")
    if dense_stats["inf_count"] > 0:
        issues.append(
            f"dense_vector contains {dense_stats['inf_count']} infinite value embeddings."
        )
    if dense_stats["zero_vector_count"] > 0:
        issues.append(
            f"dense_vector contains {dense_stats['zero_vector_count']} all-zero vector embeddings."
        )
    if dense_stats["norm_out_of_bounds_count"] > 0:
        issues.append(
            f"dense_vector contains {dense_stats['norm_out_of_bounds_count']} embeddings with L2 norm outside [{min_dense_norm}, {max_dense_norm}] (min={dense_stats['min_norm']}, max={dense_stats['max_norm']})."
        )
    if dense_stats["dimension_mismatch_count"] > 0:
        issues.append(
            f"dense_vector contains {dense_stats['dimension_mismatch_count']} dimension mismatches."
        )
    if dense_stats["corrupt_blob_count"] > 0:
        issues.append(
            f"dense_vector contains {dense_stats['corrupt_blob_count']} corrupt/unparseable vector blobs."
        )


def _inspect_dense_vector(
    conn: sqlite3.Connection,
    existing_tables: set[str],
    sample_limit: int,
    expected_dense_dim: int,
    min_dense_norm: float,
    max_dense_norm: float,
    enforce_unit_norm: bool,
    anomalies: list[dict[str, Any]],
    issues: list[str],
) -> dict[str, Any]:
    """Inspect dense_vector table."""
    dense_stats: dict[str, Any] = {
        "table_present": "dense_vector" in existing_tables,
        "total_rows": 0,
        "sampled_rows": 0,
        "nan_count": 0,
        "inf_count": 0,
        "zero_vector_count": 0,
        "norm_out_of_bounds_count": 0,
        "dimension_mismatch_count": 0,
        "corrupt_blob_count": 0,
        "min_norm": None,
        "max_norm": None,
    }
    if not dense_stats["table_present"]:
        return dense_stats

    row_count = conn.execute("SELECT count(*) FROM dense_vector").fetchone()[0]
    dense_stats["total_rows"] = row_count

    query = "SELECT record_id, dimensions, vector FROM dense_vector"
    if sample_limit > 0:
        query += f" LIMIT {sample_limit}"
    rows = conn.execute(query).fetchall()
    dense_stats["sampled_rows"] = len(rows)

    min_norm = float("inf")
    max_norm = float("-inf")

    for r in rows:
        rid = str(r["record_id"])
        blob = r["vector"]
        if not isinstance(blob, (bytes, bytearray, memoryview)):
            dense_stats["corrupt_blob_count"] += 1
            anomalies.append(
                {"table": "dense_vector", "record_id": rid, "issue": "NON_BLOB_VECTOR"}
            )
            continue

        floats = _unpack_floats(bytes(blob))
        if floats is None:
            dense_stats["corrupt_blob_count"] += 1
            anomalies.append(
                {"table": "dense_vector", "record_id": rid, "issue": "CORRUPT_BLOB_ALIGNMENT"}
            )
            continue

        norm = _check_dense_floats(
            rid,
            r["dimensions"],
            floats,
            expected_dense_dim,
            min_dense_norm,
            max_dense_norm,
            enforce_unit_norm,
            dense_stats,
            anomalies,
        )
        if norm is not None:
            min_norm = min(min_norm, norm)
            max_norm = max(max_norm, norm)

    if dense_stats["sampled_rows"] > 0 and min_norm != float("inf"):
        dense_stats["min_norm"] = round(min_norm, 4)
        dense_stats["max_norm"] = round(max_norm, 4)

    _append_dense_issues(dense_stats, min_dense_norm, max_dense_norm, issues)
    return dense_stats


def _inspect_sparse_weight(
    conn: sqlite3.Connection,
    existing_tables: set[str],
    sample_limit: int,
    anomalies: list[dict[str, Any]],
    issues: list[str],
) -> dict[str, Any]:
    """Inspect sparse_weight table."""
    sparse_stats: dict[str, Any] = {
        "table_present": "sparse_weight" in existing_tables,
        "total_rows": 0,
        "sampled_rows": 0,
        "nan_count": 0,
        "inf_count": 0,
        "negative_count": 0,
    }
    if not sparse_stats["table_present"]:
        return sparse_stats

    row_count = conn.execute("SELECT count(*) FROM sparse_weight").fetchone()[0]
    sparse_stats["total_rows"] = row_count

    query = "SELECT record_id, token, weight FROM sparse_weight"
    if sample_limit > 0:
        query += f" LIMIT {sample_limit}"
    rows = conn.execute(query).fetchall()
    sparse_stats["sampled_rows"] = len(rows)

    for r in rows:
        raw_w = r["weight"]
        rid = str(r["record_id"])
        tok = str(r["token"])
        if raw_w is None:
            sparse_stats["nan_count"] += 1
            anomalies.append(
                {
                    "table": "sparse_weight",
                    "record_id": rid,
                    "token": tok,
                    "issue": "NULL_OR_NAN_WEIGHT",
                }
            )
            continue
        w = float(raw_w)
        if math.isnan(w):
            sparse_stats["nan_count"] += 1
            anomalies.append(
                {"table": "sparse_weight", "record_id": rid, "token": tok, "issue": "NAN_WEIGHT"}
            )
        elif math.isinf(w):
            sparse_stats["inf_count"] += 1
            anomalies.append(
                {"table": "sparse_weight", "record_id": rid, "token": tok, "issue": "INF_WEIGHT"}
            )
        elif w < 0.0:
            sparse_stats["negative_count"] += 1
            anomalies.append(
                {
                    "table": "sparse_weight",
                    "record_id": rid,
                    "token": tok,
                    "issue": f"NEGATIVE_WEIGHT ({w})",
                }
            )

    if sparse_stats["nan_count"] > 0:
        issues.append(f"sparse_weight contains {sparse_stats['nan_count']} NaN or NULL weights.")
    if sparse_stats["inf_count"] > 0:
        issues.append(f"sparse_weight contains {sparse_stats['inf_count']} infinite weights.")
    if sparse_stats["negative_count"] > 0:
        issues.append(f"sparse_weight contains {sparse_stats['negative_count']} negative weights.")

    return sparse_stats


def _inspect_colbert_token(
    conn: sqlite3.Connection,
    existing_tables: set[str],
    sample_limit: int,
    issues: list[str],
) -> dict[str, Any]:
    """Inspect colbert_token table."""
    colbert_stats: dict[str, Any] = {
        "table_present": "colbert_token" in existing_tables,
        "total_rows": 0,
        "sampled_rows": 0,
        "nan_count": 0,
        "inf_count": 0,
        "zero_vector_count": 0,
        "corrupt_blob_count": 0,
    }
    if not colbert_stats["table_present"]:
        return colbert_stats

    row_count = conn.execute("SELECT count(*) FROM colbert_token").fetchone()[0]
    colbert_stats["total_rows"] = row_count

    query = "SELECT record_id, position, token, dimensions, vector FROM colbert_token"
    if sample_limit > 0:
        query += f" LIMIT {sample_limit}"
    rows = conn.execute(query).fetchall()
    colbert_stats["sampled_rows"] = len(rows)

    for r in rows:
        blob = r["vector"]
        if not isinstance(blob, (bytes, bytearray, memoryview)):
            colbert_stats["corrupt_blob_count"] += 1
            continue
        floats = _unpack_floats(bytes(blob))
        if floats is None:
            colbert_stats["corrupt_blob_count"] += 1
            continue
        if any(math.isnan(x) for x in floats):
            colbert_stats["nan_count"] += 1
        elif any(math.isinf(x) for x in floats):
            colbert_stats["inf_count"] += 1
        else:
            norm = math.sqrt(sum(x * x for x in floats))
            if math.isclose(norm, 0.0, abs_tol=1e-7):
                colbert_stats["zero_vector_count"] += 1

    if colbert_stats["nan_count"] > 0:
        issues.append(f"colbert_token contains {colbert_stats['nan_count']} NaN vectors.")
    if colbert_stats["inf_count"] > 0:
        issues.append(f"colbert_token contains {colbert_stats['inf_count']} infinite vectors.")
    if colbert_stats["zero_vector_count"] > 0:
        issues.append(
            f"colbert_token contains {colbert_stats['zero_vector_count']} all-zero vectors."
        )

    return colbert_stats


def _inspect_retrieval_record(
    conn: sqlite3.Connection,
    existing_tables: set[str],
    dense_table_present: bool,
    issues: list[str],
) -> dict[str, Any]:
    """Inspect retrieval_record table alignment with dense_vector."""
    retrieval_stats: dict[str, Any] = {
        "table_present": "retrieval_record" in existing_tables,
        "total_rows": 0,
        "missing_vector_count": 0,
    }
    if not retrieval_stats["table_present"]:
        return retrieval_stats

    row_count = conn.execute("SELECT count(*) FROM retrieval_record").fetchone()[0]
    retrieval_stats["total_rows"] = row_count
    if dense_table_present:
        missing = conn.execute(
            """SELECT count(*) FROM retrieval_record r
               LEFT JOIN dense_vector d ON r.record_id = d.record_id
               WHERE d.record_id IS NULL"""
        ).fetchone()[0]
        retrieval_stats["missing_vector_count"] = missing
        if missing > 0:
            issues.append(
                f"retrieval_record contains {missing} records missing dense_vector representations."
            )

    return retrieval_stats


def inspect_vector_health(
    sidecar: Path | str | sqlite3.Connection | None,
    *,
    sample_limit: int = 2000,
    expected_dense_dim: int = 1024,
    min_dense_norm: float = 0.8,
    max_dense_norm: float = 1.2,
    enforce_unit_norm: bool = True,
) -> dict[str, Any]:
    """Inspect sidecar vector database for NaNs, zero vectors, dimension mismatches, norm bounds, and corruptions."""
    if sidecar is None:
        return {"status": "ERROR", "reason": "No sidecar database provided.", "tables": {}}

    conn: sqlite3.Connection | None = None
    should_close = False
    try:
        if isinstance(sidecar, sqlite3.Connection):
            conn = sidecar
        else:
            p = Path(sidecar)
            if not p.is_file():
                return {"status": "ERROR", "reason": f"Sidecar file not found: {p}", "tables": {}}
            conn = create_sqlite_conn(p, readonly=True)
            should_close = True

        conn.row_factory = sqlite3.Row

        # Check which tables are present
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row["name"] for row in cur.fetchall()}

        issues: list[str] = []
        anomalies: list[dict[str, Any]] = []

        dense_stats = _inspect_dense_vector(
            conn,
            existing_tables,
            sample_limit,
            expected_dense_dim,
            min_dense_norm,
            max_dense_norm,
            enforce_unit_norm,
            anomalies,
            issues,
        )
        sparse_stats = _inspect_sparse_weight(
            conn, existing_tables, sample_limit, anomalies, issues
        )
        colbert_stats = _inspect_colbert_token(conn, existing_tables, sample_limit, issues)
        retrieval_stats = _inspect_retrieval_record(
            conn, existing_tables, dense_stats.get("table_present", False), issues
        )

        # Overall Status
        is_healthy = len(issues) == 0
        status = "HEALTHY" if is_healthy else "UNHEALTHY"

        result = {
            "status": status,
            "is_healthy": is_healthy,
            "issues": issues,
            "tables": {
                "dense_vector": dense_stats,
                "sparse_weight": sparse_stats,
                "colbert_token": colbert_stats,
                "retrieval_record": retrieval_stats,
            },
            "anomalies_sample": anomalies[:25],
            "total_anomalies": len(anomalies),
        }
        result["markdown_report"] = format_vector_health_report(result)
        return result
    except Exception as exc:
        return {"status": "ERROR", "reason": f"Vector health inspection error: {exc}", "tables": {}}
    finally:
        if should_close and conn is not None:
            conn.close()


def format_vector_health_report(health: dict[str, Any]) -> str:
    """Format a clean markdown table of vector health inspection results."""
    status = health.get("status", "UNKNOWN")
    lines = [
        "### Vector Database Health Audit",
        "",
        f"- **Overall Vector Health**: `{status}`",
    ]
    issues = health.get("issues", [])
    if issues:
        lines.append("- **Detected Issues**:")
        for issue in issues:
            lines.append(f"  • ⚠️ {issue}")
    else:
        lines.append(
            "- **Integrity**: No NaNs, zero vectors, dimension mismatches, or corrupt blobs detected."
        )

    lines.append("")
    lines.append(
        "| Table | Present | Total Rows | Sampled | NaNs | Infs | Zero Vecs | Issues / Anomalies |"
    )
    lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

    tbls = health.get("tables", {})
    dense = tbls.get("dense_vector", {})
    sparse = tbls.get("sparse_weight", {})
    colbert = tbls.get("colbert_token", {})
    retrieval = tbls.get("retrieval_record", {})

    lines.append(
        f"| `dense_vector` | {dense.get('table_present', False)} | {dense.get('total_rows', 0)} | "
        f"{dense.get('sampled_rows', 0)} | {dense.get('nan_count', 0)} | {dense.get('inf_count', 0)} | "
        f"{dense.get('zero_vector_count', 0)} | "
        f"norm_out_of_bounds={dense.get('norm_out_of_bounds_count', 0)}, dim_mismatch={dense.get('dimension_mismatch_count', 0)}, corrupt={dense.get('corrupt_blob_count', 0)} |"
    )
    lines.append(
        f"| `sparse_weight` | {sparse.get('table_present', False)} | {sparse.get('total_rows', 0)} | "
        f"{sparse.get('sampled_rows', 0)} | {sparse.get('nan_count', 0)} | {sparse.get('inf_count', 0)} | "
        f"— | negative_weights={sparse.get('negative_count', 0)} |"
    )
    lines.append(
        f"| `colbert_token` | {colbert.get('table_present', False)} | {colbert.get('total_rows', 0)} | "
        f"{colbert.get('sampled_rows', 0)} | {colbert.get('nan_count', 0)} | {colbert.get('inf_count', 0)} | "
        f"{colbert.get('zero_vector_count', 0)} | corrupt={colbert.get('corrupt_blob_count', 0)} |"
    )
    if retrieval.get("table_present", False):
        lines.append(
            f"| `retrieval_record` | True | {retrieval.get('total_rows', 0)} | "
            f"— | — | — | — | missing_dense_vectors={retrieval.get('missing_vector_count', 0)} |"
        )

    return "\n".join(lines)

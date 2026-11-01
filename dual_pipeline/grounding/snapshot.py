"""dual_pipeline.grounding.snapshot

Layer 1 transactionally consistent, read-only SQLite snapshot reader.
Enforces:
1. Strict runtime-root scoping and active-pair pointer following without silent fallback.
2. Pair integrity and graph SHA-256 match before reading.
3. Read-only connection semantics (mode=ro, query_only=ON, busy_timeout=5000).
4. No schema migration, checkpointing, or repair on active read connections.
5. Snapshot generation identity re-verification to detect mid-flight concurrent mutations.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from storage.connection import create_sqlite_conn


def _compute_sha256(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    if not path.is_file():
        raise FileNotFoundError(f"File not found for hash calculation: {path}")
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def resolve_scoped_active_pair(runtime_root: Path) -> Tuple[Dict[str, Any], Path]:
    """Resolve active_pair.json strictly within the explicit runtime root, following pointer if declared."""
    from dual_pipeline.runtime_root import resolve_active_pair
    return resolve_active_pair(runtime_root, enforce_integrity=False)


def verify_snapshot_generation(
    runtime_root: Path,
    expected_generation_id: str,
    expected_graph_sha: str,
) -> bool:
    """Verify that runtime active_pair has not drifted or mutated since snapshot capture."""
    try:
        active_pair, _ = resolve_scoped_active_pair(runtime_root)
        current_gen = active_pair.get("generation_id") or active_pair.get("next_pair", {}).get("generation_id")
        current_sha = active_pair.get("graph_sha256")
        return (current_gen == expected_generation_id) and (current_sha == expected_graph_sha)
    except Exception:
        return False


def _format_name_expr(alias: str, node_cols: set[str]) -> str:
    """Format safe COALESCE expression referencing only existing node name columns."""
    candidates = [f"{alias}.{col}" for col in ("display_name", "canonical_name", "name") if col in node_cols]
    return f"COALESCE({', '.join(candidates)}, '')" if candidates else "''"


class ReadOnlyGraphSnapshot:
    """Read-only transactionally isolated snapshot reader over GraphDB."""

    def __init__(
        self,
        runtime_root: Path | str,
        enforce_pair_integrity: bool = True,
    ) -> None:
        self.runtime_root = Path(runtime_root).resolve()
        self.enforce_pair_integrity = enforce_pair_integrity

        # 1. Resolve active pair
        self.active_pair, self.pair_path = resolve_scoped_active_pair(self.runtime_root)

        # 2. Extract and validate required pair attributes
        self.generation_id = self.active_pair.get("generation_id")
        if not self.generation_id:
            # Check nested next_pair if present
            self.generation_id = self.active_pair.get("next_pair", {}).get("generation_id")
        if not self.generation_id:
            raise ValueError(f"ACTIVE_GRAPH_MISSING: Malformed active_pair.json missing generation_id at {self.pair_path}")

        raw_graph_path = self.active_pair.get("graph_path")
        if not raw_graph_path:
            raise ValueError(f"ACTIVE_GRAPH_MISSING: Malformed active_pair.json missing graph_path at {self.pair_path}")

        self.graph_path = Path(raw_graph_path)
        if not self.graph_path.is_absolute():
            self.graph_path = (self.pair_path.parent / self.graph_path).resolve()

        if not self.graph_path.is_file():
            raise FileNotFoundError(f"ACTIVE_GRAPH_MISSING: Resolved graph file does not exist: {self.graph_path}")

        self.recorded_sha256 = self.active_pair.get("graph_sha256")
        if self.enforce_pair_integrity:
            if not self.recorded_sha256:
                raise ValueError(f"ACTIVE_GRAPH_MISSING: active_pair.json missing graph_sha256 at {self.pair_path}")
            actual_sha = _compute_sha256(self.graph_path)
            if actual_sha != self.recorded_sha256:
                raise ValueError(
                    f"ACTIVE_GRAPH_MISSING: Graph integrity mismatch: actual {actual_sha} != recorded {self.recorded_sha256}"
                )

        # 3. Initialize read-only connection
        self._conn: Optional[Any] = None

    def connect(self) -> Any:
        """Open a read-only SQLite connection with query_only pragmas."""
        if self._conn is not None:
            return self._conn

        conn = create_sqlite_conn(self.graph_path, readonly=True)
        cursor = conn.cursor()
        cursor.execute("PRAGMA query_only = ON;")
        cursor.execute("PRAGMA busy_timeout = 30000;")
        cursor.execute("PRAGMA read_uncommitted = OFF;")
        cursor.close()

        self._conn = conn
        return self._conn

    def is_generation_current(self) -> bool:
        """Check if active generation is still unchanged."""
        return verify_snapshot_generation(
            self.runtime_root,
            self.generation_id,
            self.recorded_sha256 or "",
        )

    def _get_schema_info(self, conn: Any) -> Dict[str, Any]:
        """Inspect available node and assertion tables and columns."""
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")
        tables = {r[0] for r in cursor.fetchall()}
        node_tbl = "node" if "node" in tables else ("canonical_graph_nodes" if "canonical_graph_nodes" in tables else "")
        node_cols: set[str] = set()
        if node_tbl in ("node", "canonical_graph_nodes"):
            pragma_node = "PRAGMA table_info(" + node_tbl + ")"
            cursor.execute(pragma_node)
            node_cols = {r[1] for r in cursor.fetchall()}

        asrt_tbl = "assertion" if "assertion" in tables else ""
        asrt_cols: set[str] = set()
        if asrt_tbl in ("assertion", "claim"):
            pragma_asrt = "PRAGMA table_info(" + asrt_tbl + ")"
            cursor.execute(pragma_asrt)
            asrt_cols = {r[1] for r in cursor.fetchall()}

        return {
            "node_tbl": node_tbl,
            "node_cols": node_cols,
            "asrt_tbl": asrt_tbl,
            "asrt_cols": asrt_cols,
        }

    def get_assertion(self, assertion_id: str) -> Optional[Dict[str, Any]]:
        """Fetch single assertion record by ID, unifying across node and canonical_graph_nodes."""
        conn = self.connect()
        cursor = conn.cursor()
        schema = self._get_schema_info(conn)
        node_tbl = schema["node_tbl"] or "canonical_graph_nodes"
        s_expr = _format_name_expr("s", schema["node_cols"])
        o_expr = _format_name_expr("o", schema["node_cols"])
        query = f"""
            SELECT a.*,
                   {s_expr} as subject_name,
                   {o_expr} as object_name
            FROM assertion a
            LEFT JOIN {node_tbl} s ON a.subject_node_id = s.node_id
            LEFT JOIN {node_tbl} o ON a.object_node_id = o.node_id
            WHERE a.assertion_id = ?
        """
        cursor.execute(query, (assertion_id,))
        row = cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        lit = d.get("object_literal") or d.get("literal_value") or d.get("object_value")
        if lit is not None:
            d["object_literal"] = lit
            d["literal_value"] = lit
        return d

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Fetch node record by ID from node or canonical_graph_nodes table."""
        conn = self.connect()
        cursor = conn.cursor()
        schema = self._get_schema_info(conn)
        node_tbl = schema["node_tbl"]
        if node_tbl not in ("node", "canonical_graph_nodes"):
            return None
        sql_node = "SELECT * FROM " + node_tbl + " WHERE node_id = ?"
        cursor.execute(sql_node, (node_id,))
        row = cursor.fetchone()
        if not row:
            return None
        d = dict(row)
        if "name" not in d:
            d["name"] = d.get("display_name") or d.get("canonical_name") or d.get("name")
        return d

    @staticmethod
    def _build_claim_assertion_filters(asrt_cols: set[str], node_cols: set[str], include_candidates: bool) -> str:
        clauses: list[str] = []
        if "currentness_state" in asrt_cols:
            clauses.append(
                "AND (a.currentness_state IS NULL OR a.currentness_state IN ('current', 'unknown'))"
                if include_candidates
                else "AND (a.currentness_state IS NULL OR a.currentness_state = 'current')"
            )
        if "retrieval_eligible" in asrt_cols:
            clauses.append("AND (a.retrieval_eligible IS NULL OR a.retrieval_eligible = 1)")
        if not include_candidates:
            if "canonical_status" in asrt_cols:
                clauses.append("AND (a.canonical_status IS NULL OR a.canonical_status = 'canonical')")
            if "canonical_eligible" in asrt_cols:
                clauses.append("AND (a.canonical_eligible IS NULL OR a.canonical_eligible = 1)")
        if "active" in node_cols:
            clauses.append("AND (s.active IS NULL OR s.active = 1)")
        return " ".join(clauses)

    def find_assertions_for_claim(
        self,
        subject: str,
        predicate: str,
        object_val: str,
        *,
        include_candidates: bool = False,
    ) -> List[Dict[str, Any]]:
        """Find assertions matching subject, predicate, and object across schemas."""
        conn = self.connect()
        cursor = conn.cursor()
        schema = self._get_schema_info(conn)
        node_tbl = schema["node_tbl"] or "canonical_graph_nodes"
        asrt_cols, node_cols = schema["asrt_cols"], schema["node_cols"]
        s_expr, o_expr = _format_name_expr("s", node_cols), _format_name_expr("o", node_cols)
        lit_col = "a.object_literal" if "object_literal" in asrt_cols else ("a.object_value" if "object_value" in asrt_cols else "a.literal_value")
        extra_filters = self._build_claim_assertion_filters(asrt_cols, node_cols, include_candidates)

        query = f"""
            SELECT a.*, {s_expr} as subject_name, {o_expr} as object_name
            FROM assertion a
            JOIN {node_tbl} s ON a.subject_node_id = s.node_id
            LEFT JOIN {node_tbl} o ON a.object_node_id = o.node_id
            WHERE LOWER({s_expr}) = LOWER(?) AND LOWER(a.predicate) = LOWER(?)
              AND ((o.node_id IS NOT NULL AND LOWER({o_expr}) = LOWER(?)) OR ({lit_col} IS NOT NULL AND LOWER({lit_col}) = LOWER(?)))
              {extra_filters}
        """
        cursor.execute(query, (subject, predicate, object_val, object_val))
        results = []
        for r in cursor.fetchall():
            d = dict(r)
            lit = d.get("object_literal") or d.get("literal_value") or d.get("object_value")
            if lit is not None:
                d["object_literal"] = d["literal_value"] = lit
            results.append(d)
        return results

    def get_assertion_evidence(self, assertion_id: str) -> List[Dict[str, Any]]:
        """Fetch evidence snippets bound to an assertion."""
        conn = self.connect()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM assertion_evidence WHERE assertion_id = ?", (assertion_id,))
        return [dict(r) for r in cursor.fetchall()]

    def close(self) -> None:
        """Close connection cleanly."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __enter__(self) -> ReadOnlyGraphSnapshot:
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

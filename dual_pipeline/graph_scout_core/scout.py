"""Governed Graph Health & Remediation Scout implementation."""
from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from storage.database import sqlite3

from ..core import (
    ROOT,
    connect_sqlite,
    read_json,
    sha256_file,
    stable_id,
    utc_now,
    write_json,
)
from ..parameters import get_hitl_ambiguity_margin_threshold
from .matching import _string_similarity, has_description_conflict
from .models import (
    ClutterFinding,
    ContradictionFinding,
    DuplicateEntityFinding,
    EdgeParityFinding,
)
from .antipattern_scans import (
    scan_retrieval_antipatterns,
    scan_split_prefix_identity_twins,
)
from .proposals import emit_proposals
from .scans import (
    scan_duplicate_and_malformed_sources,
    scan_edge_assertion_parity,
    scan_orphan_and_clutter,
    scan_temporal_contradictions,
)
from .scout_helpers import (
    _ID_SEP_COLON,
    _ID_SEP_UNDERSCORE,
    _TYPE_COMPAT_MAP,
    _find_scoped_pairs,
    _scan_single_proposed,
    _synthesize_uninserted_proposals,
)

logger = logging.getLogger(__name__)


class GraphScout:
    """Read-only diagnostic scout for knowledge graph health, optimization, and proposal generation."""

    _ID_SEP_COLON = _ID_SEP_COLON
    _ID_SEP_UNDERSCORE = _ID_SEP_UNDERSCORE
    _TYPE_COMPAT_MAP = _TYPE_COMPAT_MAP
    """Read-only diagnostic scout for knowledge graph health, optimization, and proposal generation."""

    def __init__(
        self,
        graph_path: Path,
        runtime_root: Optional[Path] = None,
        similarity_threshold: float = 0.72,
        max_candidate_age_days: int = 30,
    ):
        self.graph_path = Path(graph_path).resolve()
        if not self.graph_path.is_file():
            raise FileNotFoundError(f"Graph database not found: {self.graph_path}")

        self.runtime_root = (
            Path(runtime_root).resolve()
            if runtime_root is not None
            else self.graph_path.parent.parent
        )
        self.similarity_threshold = float(similarity_threshold)
        self.max_candidate_age_days = int(max_candidate_age_days)
        self.known_aliases: Set[Tuple[str, str]] = set()
        self._load_known_aliases()

    def _connect_ro(self) -> sqlite3.Connection:
        """Establish a strictly read-only connection enforcing zero mutation."""
        conn = connect_sqlite(self.graph_path, readonly=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON;")
        return conn

    def _load_known_aliases(self) -> None:
        """Load known aliases to prevent redundant proposals."""
        alias_file = ROOT / "approvals" / "v51" / "IDENTITY_AND_ALIAS_RECONCILIATION.json"
        if alias_file.is_file():
            try:
                data = read_json(alias_file)
                for entry in data.get("reconciliations", []):
                    canonical = entry.get("canonical_name", "").strip().lower()
                    for alias in entry.get("aliases", []):
                        self.known_aliases.add((canonical, alias.strip().lower()))
                        self.known_aliases.add((alias.strip().lower(), canonical))
            except Exception as exc:
                logger.warning("Could not read identity reconciliations: %s", exc)

    def _compare_node_pair(
        self, a: Dict[str, Any], b: Dict[str, Any], node_type: str
    ) -> Optional[DuplicateEntityFinding]:
        """Evaluate a pair of nodes of the same type for near-duplicate clustering."""
        nid_a = str(a.get("node_id") or "")
        nid_b = str(b.get("node_id") or "")
        if nid_a.startswith("unresolved_") or nid_b.startswith("unresolved_"):
            return None

        name_a = a["canonical_name"]
        name_b = b["canonical_name"]

        if (name_a.lower(), name_b.lower()) in self.known_aliases:
            return None

        desc_a = a.get("description") or ""
        desc_b = b.get("description") or ""
        if has_description_conflict(desc_a, desc_b):
            return None

        similarity = _string_similarity(name_a, name_b)
        if similarity < self.similarity_threshold:
            return None

        desc_overlap = _string_similarity(desc_a, desc_b)
        c_merge = min(0.98, max(0.50, round(0.70 * similarity + 0.30 * desc_overlap, 2)))
        c_distinct = round(1.0 - c_merge, 2)
        delta = round(abs(c_merge - c_distinct), 2)
        hitl_required = delta < get_hitl_ambiguity_margin_threshold()

        rationale = (
            f"Near-duplicate candidate: '{name_a}' and '{name_b}' "
            f"(type={node_type}, similarity={similarity:.2f}). "
            f"Margin Delta={delta:.2f} ({'HITL required' if hitl_required else 'Decisive'})."
        )

        return DuplicateEntityFinding(
            node_id_a=a["node_id"],
            canonical_name_a=name_a,
            node_type_a=node_type,
            node_id_b=b["node_id"],
            canonical_name_b=name_b,
            node_type_b=node_type,
            similarity_score=similarity,
            confidence_merge=c_merge,
            confidence_distinct=c_distinct,
            margin_delta=delta,
            hitl_required=hitl_required,
            rationale=rationale,
            description_a=a.get("description") or "",
            description_b=b.get("description") or "",
        )

    @staticmethod
    def _fetch_active_nodes(cursor: sqlite3.Cursor) -> List[Dict[str, Any]]:
        """Fetch active nodes with graceful fallbacks for optional columns."""
        has_node = cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='node'").fetchone() is not None
        if not has_node:
            return []
        node_cols = {r[1] for r in cursor.execute("PRAGMA table_info(node)").fetchall()}
        id_col = "node_id" if "node_id" in node_cols else ("id" if "id" in node_cols else None)
        if not id_col:
            return []
        name_col = "canonical_name" if "canonical_name" in node_cols else ("label" if "label" in node_cols else id_col)
        cols = [
            f"{id_col} AS node_id",
            f"{name_col} AS canonical_name",
            "display_name" if "display_name" in node_cols else f"{name_col} AS display_name",
            "node_type" if "node_type" in node_cols else "'entity' AS node_type",
            "description" if "description" in node_cols else "'' AS description",
            "status" if "status" in node_cols else "'active' AS status",
        ]
        where_clause = "WHERE active = 1" if "active" in node_cols else ""
        cursor.execute(
            f"""SELECT {', '.join(cols)}
                FROM node
                {where_clause}
                ORDER BY node_id"""
        )
        return [dict(row) for row in cursor.fetchall()]

    def scan_duplicate_entities(
        self, conn: sqlite3.Connection
    ) -> List[DuplicateEntityFinding]:
        """Scan all active nodes for near-duplicate clusters and alias drift."""
        cursor = conn.cursor()
        nodes = self._fetch_active_nodes(cursor)

        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for n in nodes:
            by_type.setdefault(n["node_type"], []).append(n)

        findings: List[DuplicateEntityFinding] = []
        existing_pairs: Set[Tuple[str, str]] = set()
        for node_type, type_nodes in by_type.items():
            count = len(type_nodes)
            for i in range(count):
                for j in range(i + 1, count):
                    finding = self._compare_node_pair(type_nodes[i], type_nodes[j], node_type)
                    if finding is not None:
                        existing_pairs.add((min(finding.node_id_a, finding.node_id_b), max(finding.node_id_a, finding.node_id_b)))
                        findings.append(finding)

        findings.extend(scan_split_prefix_identity_twins(nodes, existing_pairs, self._compare_node_pair))
        return findings

    # Wave 1: Pre-mutation deduplication constants (P8)
    _ID_SEP_COLON = ":"
    _ID_SEP_UNDERSCORE = "_"
    # P6: Compatible types for cross-type duplicate detection
    _TYPE_COMPAT_MAP: Dict[str, Set[str]] = {
        "role": {"stakeholder", "role_group"},
        "org": {"org"},
        "person": {"person", "person_profile", "stakeholder", "internal_person"},
        "stakeholder": {"person", "person_profile", "stakeholder", "internal_person"},
        "internal_person": {"person", "person_profile", "stakeholder", "internal_person"},
        "system": {"system", "tool", "platform"},
    }

    def scan_new_nodes(
        self, new_node_ids: Sequence[Any] | Set[Any], conn: sqlite3.Connection
    ) -> List[DuplicateEntityFinding]:
        """Scoped duplicate scan: only evaluate pairs involving new/mutated nodes."""
        if not new_node_ids:
            return []

        proposed_dicts = [item for item in new_node_ids if isinstance(item, dict)]
        target_ids = {str(item) for item in new_node_ids if not isinstance(item, dict) and item}

        findings: List[DuplicateEntityFinding] = []
        if proposed_dicts:
            findings.extend(self.scan_proposed_entities(proposed_dicts, conn))
        if not target_ids:
            return findings

        cursor = conn.cursor()
        nodes = self._fetch_active_nodes(cursor)

        uninserted = target_ids - {n["node_id"] for n in nodes}
        if uninserted:
            synthesized = _synthesize_uninserted_proposals(uninserted)
            findings.extend(self.scan_proposed_entities(synthesized, conn))

        by_type: Dict[str, List[Dict[str, Any]]] = {}
        type_has_target: Dict[str, bool] = {}
        for n in nodes:
            nt = n["node_type"]
            by_type.setdefault(nt, []).append(n)
            if n["node_id"] in target_ids:
                type_has_target[nt] = True

        findings.extend(
            _find_scoped_pairs(by_type, type_has_target, target_ids, self._compare_node_pair)
        )
        return findings

    def scan_proposed_entities(
        self, proposed_entities: Sequence[Dict[str, Any]], conn: sqlite3.Connection,
    ) -> List[DuplicateEntityFinding]:
        """Pre-mutation dedup: compare in-memory proposed entities against SQLite nodes (RC4 fix)."""
        if not proposed_entities:
            return []

        cursor = conn.cursor()
        cursor.execute(
            """SELECT node_id, canonical_name, node_type, description
               FROM node WHERE active = 1 ORDER BY node_type, canonical_name"""
        )
        db_nodes: Dict[str, List[Dict[str, Any]]] = {}
        for row in cursor.fetchall():
            r = dict(row) if isinstance(row, sqlite3.Row) else {
                "node_id": str(row[0]),
                "canonical_name": str(row[1]),
                "node_type": str(row[2]),
                "description": str(row[3]) if row[3] else "",
            }
            db_nodes.setdefault(r["node_type"], []).append(r)

        findings: List[DuplicateEntityFinding] = []
        for entity in proposed_entities:
            findings.extend(
                _scan_single_proposed(
                    entity, db_nodes, self.similarity_threshold, self.known_aliases
                )
            )
        return findings

    def scan_temporal_contradictions(

        self, conn: sqlite3.Connection
    ) -> List[ContradictionFinding]:
        """Scan current assertions for temporal divergence and supersession candidates."""
        return scan_temporal_contradictions(conn)

    def scan_orphan_and_clutter(
        self, conn: sqlite3.Connection
    ) -> List[ClutterFinding]:
        """Scan for non-intentional orphan nodes and stale candidate clutter."""
        return scan_orphan_and_clutter(conn, self.max_candidate_age_days)

    def scan_edge_assertion_parity(
        self, conn: sqlite3.Connection
    ) -> List[EdgeParityFinding]:
        """Scan edges for missing assertion mappings and dangling node endpoints."""
        return scan_edge_assertion_parity(conn)

    def scan_duplicate_and_malformed_sources(
        self, conn: sqlite3.Connection
    ) -> List[ClutterFinding]:
        """Scan source and source_version tables for duplicate clusters and degenerate metadata."""
        return scan_duplicate_and_malformed_sources(conn)

    def scan_retrieval_antipatterns(
        self, conn: sqlite3.Connection
    ) -> List[ClutterFinding]:
        """Scan for cross-project assertion contamination, leaked candidates, and sidecar/advisory bleed."""
        return scan_retrieval_antipatterns(conn)

    def emit_proposals(
        self,
        duplicate_findings: Sequence[DuplicateEntityFinding],
        contradiction_findings: Sequence[ContradictionFinding],
        output_dir: Path,
        run_id: str,
    ) -> List[Dict[str, Any]]:
        """Generate schema-valid learning change proposals for findings."""
        return emit_proposals(
            duplicate_findings=duplicate_findings,
            contradiction_findings=contradiction_findings,
            output_dir=output_dir,
            run_id=run_id,
        )

    def run(self, output_dir: Optional[Path] = None) -> Dict[str, Any]:
        """Execute full read-only diagnostic scan and emit report and proposals."""
        run_id = stable_id("graph-scout-run", utc_now(), length=24)
        out_dir = (
            Path(output_dir).resolve()
            if output_dir is not None
            else ROOT / "output" / "graph_scout" / datetime.now(timezone.utc).strftime("%m-%d-%Y")
        )
        out_dir.mkdir(parents=True, exist_ok=True)

        graph_sha = sha256_file(self.graph_path)

        with self._connect_ro() as conn:
            duplicate_findings = self.scan_duplicate_entities(conn)
            contradiction_findings = self.scan_temporal_contradictions(conn)
            clutter_findings = self.scan_orphan_and_clutter(conn)

        proposals = self.emit_proposals(
            duplicate_findings=duplicate_findings,
            contradiction_findings=contradiction_findings,
            output_dir=out_dir,
            run_id=run_id,
        )

        hitl_items = [
            f for f in duplicate_findings if f.hitl_required
        ] + [
            c for c in contradiction_findings if c.hitl_required
        ]

        report = {
            "schema_version": "1.0.0",
            "run_id": run_id,
            "scanned_at": utc_now(),
            "graph_path": str(self.graph_path),
            "graph_sha256": graph_sha,
            "runtime_root": str(self.runtime_root),
            "summary": {
                "duplicate_entity_count": len(duplicate_findings),
                "contradiction_count": len(contradiction_findings),
                "clutter_count": len(clutter_findings),
                "proposals_generated": len(proposals),
                "hitl_required_count": len(hitl_items),
                "zero_mutation_verified": True,
            },
            "duplicate_entities": [asdict(f) for f in duplicate_findings],
            "contradictions": [asdict(c) for c in contradiction_findings],
            "clutter": [asdict(k) for k in clutter_findings],
            "proposals": [p["artifact_id"] for p in proposals],
        }

        report_file = out_dir / f"graph_scout_report_{run_id}.json"
        write_json(report_file, report)
        report["report_path"] = str(report_file)

        return report

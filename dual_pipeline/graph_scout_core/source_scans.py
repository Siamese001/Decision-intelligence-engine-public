"""Source and source_version table hygiene scanners for Graph Scout and ProvenanceInspectorAgent."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple

from storage.database import sqlite3

from ..graph_writer_core.sources import _title_tokens, normalize_source_archive_stem
from .models import ClutterFinding


def _table_exists(cursor: sqlite3.Cursor, name: str) -> bool:
    return (
        cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        is not None
    )


def _scan_degenerate_and_collided_sources(
    cursor: sqlite3.Cursor, rows: List[Dict[str, Any]], has_sv: bool
) -> List[ClutterFinding]:
    findings: List[ClutterFinding] = []
    for r in rows:
        sid = str(r["source_id"])
        sname = str(r.get("source_name") or "").strip()
        if sname.lower() in {"unknown", "unspecified", "meeting-unknown", ""} or re.fullmatch(
            r"document\s+[0-9a-f]{12}", sname, re.I
        ):
            findings.append(
                ClutterFinding(
                    finding_type="degenerate_source_metadata",
                    target_id=sid,
                    label=sname or sid,
                    details={
                        "source_id": sid,
                        "source_name": sname,
                        "source_date": r.get("source_date"),
                        "cold_archive_ref": r.get("cold_archive_ref"),
                    },
                    action_recommended="unmerge_or_retitle_degenerate_source",
                )
            )
        if has_sv:
            sv_dates = [
                str(sv[0])
                for sv in cursor.execute(
                    "SELECT DISTINCT source_date_normalized FROM source_version WHERE source_id=? AND source_date_normalized IS NOT NULL AND source_date_normalized != ''",
                    (sid,),
                ).fetchall()
            ]
            if len(sv_dates) > 1:
                findings.append(
                    ClutterFinding(
                        finding_type="multi_date_source_collision",
                        target_id=sid,
                        label=sname or sid,
                        details={
                            "source_id": sid,
                            "source_name": sname,
                            "distinct_dates": sorted(sv_dates),
                        },
                        action_recommended="split_collided_source_versions",
                    )
                )
    return findings


_SESSION_TAG_RE = re.compile(r"\bsession\s+(\d+|wrap\s*-?\s*up)\b", re.I)


def _has_conflicting_session_or_output_folder(
    name_a: str, stem_a: str, ref_a: Any, name_b: str, stem_b: str, ref_b: Any
) -> bool:
    sess_a = {m.lower().replace(" ", "").replace("-", "") for m in _SESSION_TAG_RE.findall(name_a)}
    sess_b = {m.lower().replace(" ", "").replace("-", "") for m in _SESSION_TAG_RE.findall(name_b)}
    if sess_a and sess_b and sess_a != sess_b:
        return True
    ra = str(ref_a or "").replace("\\", "/")
    rb = str(ref_b or "").replace("\\", "/")
    if "/output/" in f"/{ra}" and "/output/" in f"/{rb}" and stem_a and stem_b and stem_a != stem_b:
        return True
    return False


def _are_sources_duplicate(
    item_a: Tuple[str, str, str, str, Set[str], Any],
    item_b: Tuple[str, str, str, str, Set[str], Any],
) -> bool:
    _, name_a, date_a, stem_a, toks_a, ref_a = item_a
    _, name_b, date_b, stem_b, toks_b, ref_b = item_b
    if stem_a and stem_b and stem_a == stem_b and len(stem_a) >= 6:
        return True
    if _has_conflicting_session_or_output_folder(name_a, stem_a, ref_a, name_b, stem_b, ref_b):
        return False
    if not toks_a or not toks_b:
        return False
    inter = len(toks_a & toks_b)
    overlap = inter / max(1, min(len(toks_a), len(toks_b)))
    if overlap < 0.75 or inter < 2:
        return False
    if date_a and date_b and date_a == date_b:
        return True
    dates_a = re.findall(r"202\d{5}", name_a)
    dates_b = re.findall(r"202\d{5}", name_b)
    return bool(dates_a and dates_b and dates_a[0] == dates_b[0])


def _find_parent_root(parent: Dict[str, str], x: str) -> str:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _union_duplicate_pairs(
    enriched: List[Tuple[str, str, str, str, Set[str], Any]],
    parent: Dict[str, str],
) -> None:
    for i in range(len(enriched)):
        for j in range(i + 1, len(enriched)):
            if _are_sources_duplicate(enriched[i], enriched[j]):
                ra = _find_parent_root(parent, enriched[i][0])
                rb = _find_parent_root(parent, enriched[j][0])
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)


def _cluster_duplicate_sources(
    rows: List[Dict[str, Any]],
) -> List[ClutterFinding]:
    enriched: List[Tuple[str, str, str, str, Set[str], Any]] = []
    for r in rows:
        sid = str(r["source_id"])
        sname = str(r.get("source_name") or "")
        if not sname.lower().startswith("retired -"):
            stem = normalize_source_archive_stem(r.get("cold_archive_ref"))
            toks = _title_tokens(sname)
            sdate = str(r.get("source_date") or "")[:10]
            enriched.append((sid, sname, sdate, stem, toks, r.get("cold_archive_ref")))

    parent: Dict[str, str] = {item[0]: item[0] for item in enriched}
    _union_duplicate_pairs(enriched, parent)

    clusters: Dict[str, List[Dict[str, Any]]] = {}
    for sid, sname, sdate, stem, _, ref in enriched:
        root = _find_parent_root(parent, sid)
        clusters.setdefault(root, []).append(
            {
                "source_id": sid,
                "source_name": sname,
                "source_date": sdate,
                "stem": stem,
                "cold_archive_ref": ref,
            }
        )

    return [
        ClutterFinding(
            finding_type="duplicate_source_cluster",
            target_id=root_id,
            label=members[0]["source_name"] or root_id,
            details={
                "canonical_candidate_id": root_id,
                "member_source_ids": [m["source_id"] for m in members],
                "members": members,
            },
            action_recommended="consolidate_duplicate_sources",
        )
        for root_id, members in sorted(clusters.items())
        if len(members) > 1
    ]


def scan_duplicate_and_malformed_sources(conn: sqlite3.Connection) -> List[ClutterFinding]:
    """Scan source and source_version tables for duplicate clusters, degenerate names, and date collisions."""
    if conn.row_factory is None:
        conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if not _table_exists(cursor, "source"):
        return []
    cols = {r[1] for r in cursor.execute("PRAGMA table_info(source)").fetchall()}
    if "source_id" not in cols:
        return []
    name_col = "source_name" if "source_name" in cols else ("title" if "title" in cols else "''")
    date_col = "source_date" if "source_date" in cols else ("created_at" if "created_at" in cols else "''")
    ref_col = "cold_archive_ref" if "cold_archive_ref" in cols else ("source_path_or_uri" if "source_path_or_uri" in cols else "''")
    src_query = "SELECT source_id, " + name_col + " AS source_name, " + date_col + " AS source_date, " + ref_col + " AS cold_archive_ref FROM source WHERE source_id LIKE 'source-%'"
    rows = [dict(r) for r in cursor.execute(src_query).fetchall()]
    has_sv = _table_exists(cursor, "source_version")
    findings = _scan_degenerate_and_collided_sources(cursor, rows, has_sv)
    findings.extend(_cluster_duplicate_sources(rows))
    return findings


def _make_finding(finding_cls: Any, **kwargs: Any) -> Any:
    return finding_cls(**kwargs) if finding_cls is not None else kwargs


def audit_source_hygiene_findings(conn: Any, finding_cls: Any = None) -> List[Any]:
    """Format source hygiene findings for ProvenanceInspectorAgent."""
    raw_findings = scan_duplicate_and_malformed_sources(conn)
    if not raw_findings:
        return []
    dup_clusters = [f for f in raw_findings if f.finding_type == "duplicate_source_cluster"]
    degen_sources = [f for f in raw_findings if f.finding_type == "degenerate_source_metadata"]
    multi_date = [f for f in raw_findings if f.finding_type == "multi_date_source_collision"]
    out: List[Any] = []
    if dup_clusters:
        all_ids = [sid for c in dup_clusters for sid in c.details.get("member_source_ids", [])]
        out.append(
            _make_finding(
                finding_cls,
                detective="ProvenanceInspector",
                severity="HIGH",
                description=(
                    f"Detected {len(dup_clusters)} split meeting/document cluster(s) spanning "
                    f"{len(all_ids)} duplicate source_id rows."
                ),
                entity_references=all_ids,
                proposed_fix="consolidate_duplicate_sources",
                raw_data={"clusters": [c.details for c in dup_clusters]},
            )
        )
    if degen_sources or multi_date:
        bad_ids = list(dict.fromkeys([f.target_id for f in degen_sources + multi_date]))
        out.append(
            _make_finding(
                finding_cls,
                detective="ProvenanceInspector",
                severity="HIGH",
                description=(
                    f"Detected {len(degen_sources)} degenerate source title(s) ('Unknown' / hex-hash) "
                    f"and {len(multi_date)} multi-date source_version collision(s)."
                ),
                entity_references=bad_ids,
                proposed_fix="unmerge_or_retitle_degenerate_source",
                raw_data={
                    "degenerate_sources": [f.details for f in degen_sources],
                    "multi_date_collisions": [f.details for f in multi_date],
                },
            )
        )
    return out


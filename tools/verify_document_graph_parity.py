#!/usr/bin/env python3
"""Gate C4: Corpus-wide GraphDB-to-Output Parity & Completeness Verifier."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.storage.governed_context import GovernedStorageContext


def resolve_default_graph_db() -> Path:
    try:
        from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
        rt = resolve_authoritative_runtime_root()
        ap = rt / "active_pair.json"
        if ap.exists():
            ap_data = json.loads(ap.read_text(encoding="utf-8"))
            gp = rt / str(ap_data.get("graph_path") or "")
            if gp.exists() and gp.stat().st_size > 16384:
                return gp
    except Exception:
        pass
    candidates = [
        REPO_ROOT / "graph" / "nee_graph.sqlite",
        REPO_ROOT.parent.parent / "graph" / "nee_graph.sqlite",
        REPO_ROOT / "graph.sqlite",
        REPO_ROOT.parent.parent / "graph.sqlite",
    ]
    for c in candidates:
        if c.exists() and c.stat().st_size > 16384:
            return c
    for c in candidates:
        if c.exists():
            return c
    return REPO_ROOT / "graph" / "nee_graph.sqlite"


def resolve_default_output_dir() -> Path:
    for c in (REPO_ROOT / "output", REPO_ROOT.parent.parent / "output"):
        if c.exists():
            return c
    return REPO_ROOT / "output"


def _count_03_substantive_signals(proj: Dict[str, Any]) -> int:
    gm = proj.get("graph_mutations") or {}
    if not isinstance(gm, dict):
        return 0
    total = 0
    for key in (
        "relationship_signals",
        "decision_updates",
        "proposal_updates",
        "risk_updates",
        "action_updates",
        "strategic_intent_signals",
        "metrics",
    ):
        items = gm.get(key) or []
        if isinstance(items, list):
            total += sum(1 for item in items if isinstance(item, dict))
    for ent in gm.get("entity_upserts") or []:
        if isinstance(ent, dict):
            desc = str(ent.get("description") or "").strip()
            if len(desc) >= 10 and "is an identified" not in desc.lower():
                total += 1
    return total


def _index_output_projections(output_dir: Path) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    if not output_dir.exists():
        return index
    for p in sorted(output_dir.rglob("03_*_canonical_signal_projection.json")):
        if "transcript_cache" in p.parts or any(part.startswith(".staging_") for part in p.parts):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        sub_cnt = _count_03_substantive_signals(data)
        gm = data.get("graph_mutations") or {}
        ent_cnt = len(gm.get("entity_upserts") or []) if isinstance(gm, dict) else 0
        entry = {
            "path": str(p),
            "meeting_id": str(data.get("meeting_id") or ""),
            "substantive_signals": sub_cnt,
            "entity_upserts": ent_cnt,
        }
        mid = entry["meeting_id"].strip().lower()
        if mid:
            index[mid] = entry
            index[re.sub(r"[^a-z0-9]+", "-", mid).strip("-")] = entry
        if "output" in p.parts:
            idx = p.parts.index("output")
            rel_container = "/".join(p.parts[idx : idx + 4]).lower()
            index[rel_container] = entry
        index[str(p.parent).lower()] = entry
    return index


def _match_projection_for_source(
    source_id: str,
    ext_ref: str,
    title: str,
    cold_ref: str,
    proj_index: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    for key in (ext_ref, source_id, title):
        k = (key or "").strip().lower()
        if not k:
            continue
        if k in proj_index:
            return proj_index[k]
        norm_k = re.sub(r"[^a-z0-9]+", "-", k).strip("-")
        if norm_k in proj_index:
            return proj_index[norm_k]
    if cold_ref:
        parent_ref = str(Path(cold_ref).parent).lower()
        if parent_ref in proj_index:
            return proj_index[parent_ref]
    return None


def _is_synthetic_three_assertion_stub(claims: List[str], ev_cnt: int, tot_assert: int) -> bool:
    if tot_assert == 0:
        return False
    stub_phrases = (
        "canonical governance release promotion",
        "established strategic alignment on",
        "confirmed operational priority for",
        "recorded canonical source intake for",
    )
    low_claims = [c.lower() for c in claims]
    if any(any(p in c for p in stub_phrases) for c in low_claims):
        return True
    if ev_cnt == 1 and tot_assert == 3 and not any("[remediated_full_scope]" in c for c in low_claims):
        return True
    return False


def audit_source_parity(
    db_path: Path,
    output_dir: Optional[Path] = None,
    *,
    min_parity_ratio: float = 0.80,
    strict: bool = False,
) -> Dict[str, Any]:
    """Audit all sources in db_path for Gate C4 GraphDB-to-output parity."""
    resolved_db = Path(db_path).resolve()
    if not resolved_db.exists():
        return {
            "gate": "Gate_C4_Corpus_Graph_Parity",
            "status": "FAIL",
            "db_path": str(resolved_db),
            "violations": [{"code": "ERR_DB_NOT_FOUND", "detail": f"Database not found: {resolved_db}"}],
        }

    proj_index = _index_output_projections(output_dir) if output_dir else {}
    conn = GovernedStorageContext.connect(resolved_db, readonly=True)
    try:
        src_cols = {r[1] for r in conn.execute("PRAGMA table_info(source)").fetchall()}
        asrt_cols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()}
        node_cols = {r[1] for r in conn.execute("PRAGMA table_info(node)").fetchall()}

        title_col = "source_name" if "source_name" in src_cols else ("title" if "title" in src_cols else "source_id")
        ext_col = "external_ref" if "external_ref" in src_cols else title_col
        claim_expr = (
            "COALESCE(a.scope_text, a.object_literal, '')"
            if "scope_text" in asrt_cols
            else ("COALESCE(a.claim_text, '')" if "claim_text" in asrt_cols else "''")
        )
        has_primary_src = "primary_source_id" in asrt_cols

        sources = conn.execute(
            f"""SELECT s.source_id, COALESCE(s.source_type, ''), COALESCE(s.{ext_col}, ''),
                       COALESCE(s.{title_col}, ''), COALESCE(s.cold_archive_ref, '')
                FROM source s
                ORDER BY s.source_id"""
        ).fetchall()

        violations: List[Dict[str, Any]] = []
        zero_canonical_sources: List[str] = []
        staging_leak_sources: List[str] = []
        stub_sources: List[str] = []
        truncated_sources: List[str] = []
        boilerplate_sources: List[str] = []

        for source_id, source_type, ext_ref, title, cold_ref in sources:
            ev_cnt = conn.execute(
                "SELECT COUNT(*) FROM evidence_item WHERE source_id = ?", (source_id,)
            ).fetchone()[0]
            if has_primary_src:
                rows = conn.execute(
                    f"""SELECT DISTINCT a.assertion_id, a.canonical_status, a.canonical_eligible, {claim_expr}
                        FROM assertion a
                        LEFT JOIN assertion_evidence ae ON a.assertion_id = ae.assertion_id
                        LEFT JOIN evidence_item e ON ae.evidence_id = e.evidence_id
                        WHERE a.primary_source_id = ? OR e.source_id = ?""",
                    (source_id, source_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""SELECT DISTINCT a.assertion_id, a.canonical_status, a.canonical_eligible, {claim_expr}
                        FROM assertion a
                        JOIN assertion_evidence ae ON a.assertion_id = ae.assertion_id
                        JOIN evidence_item e ON ae.evidence_id = e.evidence_id
                        WHERE e.source_id = ?""",
                    (source_id,),
                ).fetchall()

            tot_assert = len(rows)
            can_rows = [r for r in rows if r[1] == "canonical" and int(r[2] or 0) == 1]
            can_assert = len(can_rows)
            claims = [str(r[3] or "") for r in rows]

            if str(cold_ref).startswith(".staging_"):
                staging_leak_sources.append(source_id)
                violations.append({
                    "source_id": source_id,
                    "title": title,
                    "code": "ERR_STAGING_PATH_LEAKAGE",
                    "detail": f"cold_archive_ref starts with .staging_: {cold_ref}",
                })

            if can_assert == 0:
                zero_canonical_sources.append(source_id)
                violations.append({
                    "source_id": source_id,
                    "title": title,
                    "code": "ERR_ZERO_CANONICAL_ASSERTIONS",
                    "detail": f"Source has {tot_assert} total assertions and 0 canonical assertions",
                })
                continue

            matched_proj = _match_projection_for_source(source_id, ext_ref, title, cold_ref, proj_index)
            expected_sub = matched_proj["substantive_signals"] if matched_proj else 0

            if _is_synthetic_three_assertion_stub(claims, ev_cnt, tot_assert) and (expected_sub == 0 or expected_sub > 3):
                stub_sources.append(source_id)
                violations.append({
                    "source_id": source_id,
                    "title": title,
                    "code": "ERR_STUB_GRAPH_PROJECTION",
                    "detail": f"Synthetic 3-assertion stub detected (ev_cnt={ev_cnt}, tot_assert={tot_assert}, expected_03={expected_sub})",
                })
                continue

            is_doc = (
                str(source_type).lower() == "document"
                or str(ext_ref).lower().startswith("document")
                or str(title).lower().startswith("document ")
                or str(cold_ref).lower().endswith(("source_document.md", "source_document.txt"))
            )
            boilerplate_cnt = sum(1 for c in claims if "is an identified " in c.lower())
            boilerplate_ratio = (boilerplate_cnt / tot_assert) if tot_assert > 0 else 0.0
            if is_doc and tot_assert >= 4 and boilerplate_ratio > 0.25:
                boilerplate_sources.append(source_id)
                violations.append({
                    "source_id": source_id,
                    "title": title,
                    "code": "ERR_BOILERPLATE_CLAIM_SUBSTITUTION",
                    "detail": f"Document has {boilerplate_cnt}/{tot_assert} ({boilerplate_ratio:.1%}) boilerplate 'is an identified' assertions",
                })

            if is_doc and can_assert <= 1:
                truncated_sources.append(source_id)
                violations.append({
                    "source_id": source_id,
                    "title": title,
                    "code": "ERR_TRUNCATED_DOCUMENT_PROJECTION",
                    "detail": f"Document source has only {can_assert} canonical assertion(s)",
                })
            elif matched_proj and expected_sub >= 5 and is_doc:
                parity_ratio = can_assert / expected_sub
                if parity_ratio < min_parity_ratio:
                    truncated_sources.append(source_id)
                    violations.append({
                        "source_id": source_id,
                        "title": title,
                        "code": "ERR_TRUNCATED_DOCUMENT_PROJECTION",
                        "detail": (
                            f"Parity ratio {parity_ratio:.2f} < {min_parity_ratio:.2f} "
                            f"(canonical_assertions={can_assert}, substantive_03_signals={expected_sub})"
                        ),
                    })

        active_filter = "AND active = 1" if "active" in node_cols else ""
        candidate_nodes_cnt = conn.execute(
            f"SELECT COUNT(*) FROM node WHERE status = 'candidate' {active_filter}"
        ).fetchone()[0]
        orphan_nodes_cnt = conn.execute(
            f"""SELECT COUNT(*) FROM node n
                WHERE n.status = 'canonical' {active_filter}
                  AND NOT EXISTS (SELECT 1 FROM edge e WHERE e.from_node_id = n.node_id OR e.to_node_id = n.node_id)
                  AND NOT EXISTS (SELECT 1 FROM assertion a WHERE a.subject_node_id = n.node_id OR a.object_node_id = n.node_id)"""
        ).fetchone()[0]

        if strict and candidate_nodes_cnt > 0:
            violations.append({
                "source_id": "GLOBAL_NODE_TABLE",
                "title": "Unpromoted candidate nodes",
                "code": "ERR_UNPROMOTED_CANDIDATE_NODES",
                "detail": f"Found {candidate_nodes_cnt} unpromoted status='candidate' nodes in strict mode",
            })
        if strict and orphan_nodes_cnt > 0:
            violations.append({
                "source_id": "GLOBAL_NODE_TABLE",
                "title": "Orphan canonical nodes",
                "code": "ERR_ORPHAN_CANONICAL_NODES",
                "detail": f"Found {orphan_nodes_cnt} disconnected canonical nodes in strict mode",
            })

        passed = len(violations) == 0
        return {
            "gate": "Gate_C4_Corpus_Graph_Parity",
            "status": "PASS" if passed else "FAIL",
            "db_path": str(resolved_db),
            "total_sources": len(sources),
            "zero_canonical_sources_count": len(zero_canonical_sources),
            "staging_leak_sources_count": len(staging_leak_sources),
            "stub_sources_count": len(stub_sources),
            "truncated_sources_count": len(truncated_sources),
            "boilerplate_sources_count": len(boilerplate_sources),
            "candidate_nodes_count": candidate_nodes_cnt,
            "orphan_canonical_nodes_count": orphan_nodes_cnt,
            "violation_count": len(violations),
            "violations": violations,
        }
    finally:
        conn.close()


def validate_container_graph_parity(container: Path, db_path: Optional[Path] = None) -> List[str]:
    if db_path is None or not Path(db_path).exists():
        return []
    res = audit_source_parity(Path(db_path), container)
    return [f"[{v['code']}] {v.get('source_id', '')}: {v['detail']}" for v in res.get("violations", [])]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Verify GraphDB-to-output document and corpus parity (Gate C4).")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="Path to SQLite knowledge graph database.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Path to production output directory.",
    )
    parser.add_argument("--strict", action="store_true", help="Enforce zero candidate nodes and zero orphan nodes.")
    parser.add_argument("--json", action="store_true", help="Emit JSON report to stdout.")
    args = parser.parse_args(argv)

    db_path = args.db_path or resolve_default_graph_db()
    output_dir = args.output_dir or resolve_default_output_dir()
    report = audit_source_parity(db_path, output_dir, strict=args.strict)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"[Gate C4] status={report['status']} sources={report.get('total_sources', 0)} "
            f"violations={report.get('violation_count', 0)} "
            f"(zero_canonical={report.get('zero_canonical_sources_count', 0)}, "
            f"staging_leaks={report.get('staging_leak_sources_count', 0)}, "
            f"stubs={report.get('stub_sources_count', 0)}, "
            f"truncated={report.get('truncated_sources_count', 0)}, "
            f"boilerplate={report.get('boilerplate_sources_count', 0)}, "
            f"candidate_nodes={report.get('candidate_nodes_count', 0)}, "
            f"orphan_nodes={report.get('orphan_canonical_nodes_count', 0)})"
        )
        for v in report.get("violations", [])[:50]:
            print(f"  - [{v['code']}] {v.get('source_id')} ({v.get('title')}): {v['detail']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())

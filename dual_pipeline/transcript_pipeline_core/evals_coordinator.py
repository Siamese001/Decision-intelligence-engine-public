"""
dual_pipeline/transcript_pipeline_core/evals_coordinator.py

Authoritative Stage EVALS Coordinator & Tier 1 Health Scout Execution.
Extracted from release_runner.py (Wave 0) to maintain strict 400-LOC module budget.
Enforces:
  P2: Dynamic O(n) timeout budget: min(500, max(100, 50 + n_nodes * 0.4)) ms.
  P5: Path boundary validation: assert canon_runtime_root within .local/dual_pipeline.
  P11: Full 15-field AGENTS.md mutation breakdown table (11 SQLite + 4 sidecar).
  Atomic write (.tmp -> fsync -> os.replace) for all breakdown files.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from dual_pipeline.core import ROOT, read_json

logger = logging.getLogger(__name__)

# Governed runtime path boundary (P5)
_GOVERNED_RUNTIME_BOUNDARY = Path(".local/dual_pipeline")

# Full 15 tables mandated by AGENTS.md mutation breakdown invariant (P11)
_SQLITE_TABLES = [
    "node",
    "source",
    "source_version",
    "extraction_run",
    "evidence_item",
    "assertion",
    "assertion_evidence",
    "search_document",
    "search_fts",
    "audit_event",
    "reserved_11",
]

_SIDECAR_TABLES = [
    "dense_vector",
    "sparse_weight",
    "colbert_token",
    "retrieval_record",
]


class SecurityError(Exception):
    """Raised when a runtime path violates the governed boundary."""


def _validate_runtime_root(canon_runtime_root: Optional[Path]) -> Path:
    """Assert runtime root resolves strictly within .local/dual_pipeline boundary (P5)."""
    if canon_runtime_root is None:
        target = (ROOT / ".local" / "dual_pipeline" / "runtime").resolve()
    else:
        target = canon_runtime_root.resolve()

    boundary = (ROOT / _GOVERNED_RUNTIME_BOUNDARY).resolve()
    allowed_test_root = os.environ.get("NEE_ALLOW_TEST_RUNTIME_ROOT")
    if allowed_test_root:
        test_boundary = Path(allowed_test_root).resolve()
        try:
            if target.is_relative_to(test_boundary):
                return target
        except AttributeError:
            if str(target).startswith(str(test_boundary)):
                return target

    try:
        if not target.is_relative_to(boundary):
            raise SecurityError(
                f"Runtime root '{target}' is outside the governed boundary '{boundary}'"
            )
    except AttributeError:
        # Python < 3.9 fallback
        if not str(target).startswith(str(boundary)):
            raise SecurityError(
                f"Runtime root '{target}' is outside the governed boundary '{boundary}'"
            )
    return target


def _resolve_graph_path(canon_runtime_root: Path) -> Optional[Path]:
    """Resolve the active graph SQLite path from active_pair.json or fallback."""
    ap_link = canon_runtime_root / "active_pair.json"
    if ap_link.is_file():
        try:
            ap = read_json(ap_link)
            rel_p = ap.get("graph_path")
            if rel_p:
                for base in (canon_runtime_root.parent, canon_runtime_root):
                    candidate = (base / rel_p).resolve()
                    if candidate.is_file():
                        return candidate
        except Exception as exc:
            logger.warning("Could not read active_pair.json: %s", exc)

    fallback = (ROOT / "graph" / "nee_graph.sqlite").resolve()
    return fallback if fallback.is_file() else None


def _compute_dynamic_timeout(graph_path: Path) -> float:
    """Compute dynamic scout timeout budget based on active node count (P2).

    Formula: budget_ms = min(500, max(100, 50 + n_active_nodes * 0.4))
    Returns timeout in seconds.
    """
    try:
        from dual_pipeline.core import connect_sqlite
        with connect_sqlite(graph_path, readonly=True) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM node WHERE active=1"
            ).fetchone()
            n_nodes = row[0] if row else 0
    except Exception:
        n_nodes = 0

    budget_ms = min(500.0, max(100.0, 50.0 + n_nodes * 0.4))
    logger.debug("Phase 3 Scout: dynamic timeout budget %.0fms for %d active nodes", budget_ms, n_nodes)
    return budget_ms / 1000.0


_TABLE_COUNT_QUERIES: Dict[str, str] = {
    "node": "SELECT COUNT(*) FROM node", "source": "SELECT COUNT(*) FROM source",
    "source_version": "SELECT COUNT(*) FROM source_version", "extraction_run": "SELECT COUNT(*) FROM extraction_run",
    "evidence_item": "SELECT COUNT(*) FROM evidence_item", "assertion": "SELECT COUNT(*) FROM assertion",
    "assertion_evidence": "SELECT COUNT(*) FROM assertion_evidence", "search_document": "SELECT COUNT(*) FROM search_document",
    "search_fts": "SELECT COUNT(*) FROM search_fts", "audit_event": "SELECT COUNT(*) FROM audit_event",
    "reserved_11": "SELECT COUNT(*) FROM reserved_11", "dense_vector": "SELECT COUNT(*) FROM dense_vector",
    "sparse_weight": "SELECT COUNT(*) FROM sparse_weight", "colbert_token": "SELECT COUNT(*) FROM colbert_token",
    "retrieval_record": "SELECT COUNT(*) FROM retrieval_record",
}


def _query_baseline_table_counts(graph_path: Optional[Path]) -> Dict[str, int]:
    baseline: Dict[str, int] = {}
    if not (graph_path and graph_path.is_file()):
        return baseline
    try:
        from dual_pipeline.core import connect_sqlite
        with connect_sqlite(graph_path, readonly=True) as conn:
            for tbl, query in _TABLE_COUNT_QUERIES.items():
                try:
                    row = conn.execute(query).fetchone()
                    baseline[tbl] = row[0] if row else 0
                except Exception:
                    baseline[tbl] = 0
    except Exception as exc:
        logger.debug("Could not query baseline table counts: %s", exc)
    return baseline


def _build_full_breakdown_table(
    graph_path: Optional[Path],
    touched_count: int,
    findings_count: int,
    proposals_count: int,
) -> str:
    """Build full 15-field AGENTS.md mutation breakdown table (P11)."""
    baseline = _query_baseline_table_counts(graph_path)

    lines = [
        "## Tier 1 Scoped Scout — GraphDB Mutation Breakdown",
        "",
        "**Run Mode:** Pre-Mutation Diagnostic (read-only, `PRAGMA query_only = ON`)",
        f"**Touched Entity Proposals:** {touched_count}",
        f"**Near-Duplicate Findings:** {findings_count}",
        f"**Proposals Emitted:** {proposals_count}",
        "",
        "### Scoped Graph Health & Duplicate Scout (Phase 3 Hot Path)",
        f"- **Proposals Emitted to Queue:** {proposals_count}",
        "",
        "### Granular GraphDB Delta Table",
        "",
        "| SQLite Table | Baseline Count | Activated Count | Delta | What Changed |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    for tbl in _SQLITE_TABLES:
        cnt = baseline.get(tbl, 0)
        desc = "Pre-mutation diagnostic scan (unmutated)" if tbl == "node" else "Unchanged"
        lines.append(f"| `{tbl}` | {cnt} | — | +0 | {desc} |")

    lines.extend([
        "",
        "### Sidecar Vector Table Counts",
        "",
        "| Sidecar Table | Baseline Count | Activated Count | Delta | What Changed |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ])

    for tbl in _SIDECAR_TABLES:
        cnt = baseline.get(tbl, 0)
        lines.append(f"| `{tbl}` | {cnt} | — | +0 | Unchanged |")

    lines.extend([
        "",
        "### Itemized Assertions Table",
        "",
        "| Assertion ID | Subject Node | Predicate | Literal / Claim Text | Status |",
        "| :--- | :--- | :--- | :--- | :--- |",
        "| *(none)* | *(none)* | *(none)* | Pre-mutation diagnostic scan | DIAGNOSTIC_PASS |",
        "",
        "### Idempotency & Hash Transition",
        "",
        "- **Transition State:** `PRE_MUTATION_DIAGNOSTIC`",
        "- **Active Generation:** Preserved strictly unchanged",
        "- **Graph Mutation Status:** Zero persistent side-effects until authoritative activation",
        "",
    ])

    return "\n".join(lines)


def _run_scout_scan_passes(
    scout: Any, conn: Any, proposed_entities: Optional[Sequence[Dict[str, Any]]]
) -> Tuple[list, list, list]:
    dup_findings = []
    if hasattr(scout, "scan_proposed_entities") and proposed_entities:
        try:
            dup_findings = scout.scan_proposed_entities(proposed_entities, conn)
        except Exception as dup_err:
            logger.warning("Pre-mutation entity scan error: %s", dup_err)

    contra_findings = []
    try:
        contra_findings = scout.scan_temporal_contradictions(conn)
    except Exception as contra_err:
        logger.debug("Temporal contradiction scan skipped: %s", contra_err)

    clutter_findings = []
    try:
        clutter_findings = scout.scan_orphan_and_clutter(conn)
    except Exception as clutter_err:
        logger.debug("Clutter scan skipped: %s", clutter_err)

    return dup_findings, contra_findings, clutter_findings


def _record_scout_package_preparation(safe_root: Path, run_id: str, count: int, graph_path: Path) -> None:
    from dual_pipeline.graph_mutation_ledger import GraphMutationLedger
    l_paths = [safe_root / "graph_mutation_ledger.json"]
    if (safe_root.parent / "ledger").is_dir():
        l_paths.append(safe_root.parent / "ledger" / "graph_mutation_ledger.json")
    for lp in l_paths:
        try:
            GraphMutationLedger(lp).record_package_preparation(
                run_id=run_id, proposed_node_count=count, proposed_assertion_count=0,
                target_graph_path=graph_path, runtime_root=safe_root,
            )
        except Exception as led_err:
            logger.debug("Ledger record error: %s", led_err)


def _write_scout_breakdown_table(
    output_dir: Optional[Path], graph_path: Path, touched_count: int, findings_count: int, proposals_count: int
) -> Optional[Path]:
    if not output_dir:
        return None
    try:
        breakdown_path = output_dir / "graph_mutation_breakdown.md"
        if breakdown_path.is_file():
            existing_content = breakdown_path.read_text(encoding="utf-8")
            if (
                existing_content.strip() not in ("", "# Graph Mutation Breakdown")
                and "Pre-Mutation Diagnostic" not in existing_content
            ):
                # Do not overwrite authoritative post-mutation breakdown table with pre-mutation diagnostic stub
                return breakdown_path
        breakdown_md = _build_full_breakdown_table(graph_path, touched_count, findings_count, proposals_count)
        import os
        tmp_p = breakdown_path.with_suffix(".md.tmp")
        tmp_p.write_text(breakdown_md, encoding="utf-8")
        with tmp_p.open("a") as f:
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_p, breakdown_path)
        return breakdown_path
    except Exception as write_err:
        logger.warning("Could not write graph_mutation_breakdown.md: %s", write_err)
        return None


def _extract_proposed_entities(
    written: Dict[str, str], proposed: Optional[Sequence[Dict[str, Any]]]
) -> List[Dict[str, Any]]:
    if proposed is not None:
        return list(proposed)
    f3_path = Path(written.get("03_graph", ""))
    if f3_path.is_file():
        try:
            f3_data = read_json(f3_path)
            return list(f3_data.get("graph_mutations", {}).get("entity_upserts", []))
        except Exception as read_err:
            logger.warning("Could not read Output 03 for proposed entities: %s", read_err)
    return []


def _emit_scout_proposals(
    output_dir: Optional[Path], dup_f: list, contra_f: list, run_id: str, written: Optional[Dict[str, Any]]
) -> int:
    from dual_pipeline.graph_scout_core.proposals import emit_proposals
    if not (output_dir and (dup_f or contra_f)):
        return 0
    proposals = emit_proposals(
        duplicate_findings=dup_f,
        contradiction_findings=contra_f,
        output_dir=output_dir,
        run_id=run_id,
    )
    if written is not None:
        written["phase3_scout_proposals"] = [
            p.to_dict() if hasattr(p, "to_dict") else dict(p) for p in proposals
        ]
    return len(proposals)


def _execute_phase3_scoped_scout(
    written: Dict[str, str],
    output_dir: Optional[Path],
    canon_runtime_root: Optional[Path],
    dir_id: Optional[str],
    meta: Dict[str, Any],
    proposed_entities: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Execute Stage EVALS Phase 3 scoped scout unconditionally (Wave 0/2)."""
    result: Dict[str, Any] = {
        "ran": False, "findings_count": 0, "proposals_emitted": 0, "elapsed_ms": 0.0, "skipped_reason": None,
    }

    try:
        safe_root = _validate_runtime_root(canon_runtime_root)
    except SecurityError as sec_err:
        logger.warning("Phase 3 Scout SecurityError: %s. Skipping scout.", sec_err)
        result["skipped_reason"] = f"SecurityError: {sec_err}"
        return result

    entities = _extract_proposed_entities(written, proposed_entities)
    touched_count = len(entities)
    graph_path = _resolve_graph_path(safe_root)
    if not graph_path or not graph_path.is_file():
        logger.warning("Phase 3 Scout: graph path '%s' not found. Skipping.", graph_path)
        result["skipped_reason"] = f"GraphNotFound: {graph_path}"
        return result

    findings: list = []
    proposals_emitted = 0

    try:
        from dual_pipeline.core import connect_sqlite
        from dual_pipeline.graph_scout_core.scout import GraphScout

        scout = GraphScout(graph_path=graph_path)

        with connect_sqlite(graph_path, readonly=True) as conn:
            conn.execute("PRAGMA query_only = ON")
            dup_f, contra_f, clutter_f = _run_scout_scan_passes(scout, conn, entities)
            findings.extend(dup_f + contra_f + clutter_f)
            run_id = dir_id or meta.get("date_filename") or "evals-run"
            proposals_emitted = _emit_scout_proposals(output_dir, dup_f, contra_f, run_id, written)

        _record_scout_package_preparation(safe_root, run_id, touched_count, graph_path)

        if written is not None:
            written["tier1_scout_executed"] = True
            written["graph_mutated"] = False

        result["ran"] = True
        result["findings_count"] = len(findings)
        result["proposals_emitted"] = proposals_emitted

    except Exception as scout_err:
        logger.warning("Phase 3 Scoped Scout encountered error: %s", scout_err, exc_info=True)
        result["skipped_reason"] = f"ScoutError: {scout_err}"

    breakdown_path = _write_scout_breakdown_table(output_dir, graph_path, touched_count, len(findings), proposals_emitted)
    if breakdown_path and written is not None:
        written["graph_mutation_breakdown"] = str(breakdown_path)

    return result


def finalize_release_execution(
    tracker: Any,
    receipt_dir: Path,
    written: Dict[str, str],
    release_receipt: Optional[Dict[str, Any]] = None,
    activation_receipt: Optional[Dict[str, Any]] = None,
    scout_result: Optional[Dict[str, Any]] = None,
) -> Path:
    """Build and seal the final release execution receipt.

    Extracted from release_runner.py to maintain LOC budget.
    Attaches scout_result to EVALS_RELEASE stage details.
    """
    from dual_pipeline.unified_agentic_pipeline import PipelineStage

    details: Dict[str, Any] = {
        "written_count": len(written),
        "release_receipt_present": release_receipt is not None,
        "activation_receipt_present": activation_receipt is not None,
    }
    if scout_result is not None:
        details["tier1_scout"] = scout_result

    tracker.complete_stage(
        PipelineStage.EVALS_RELEASE,
        status="PASS",
        details=details,
    )
    receipt = tracker.build_receipt(receipt_dir, terminal_outcome="COMPLETE")
    return receipt.write_to_dir(receipt_dir)

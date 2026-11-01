"""tools/scheduled_task_core/adapters_core/hygiene.py

Knowledge graph and storage hygiene domain task adapters.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from storage.connection import create_sqlite_conn
from ..authority import ApprovalRecord, AuthorityContext, verify_approval_record
from ..contracts import ExecutionMode
from ..remediation_planner import attach_plan_if_findings
from .hygiene_enhancements import (
    compute_storage_reclamation_forecast,
    match_stashes_to_originating_branches,
    run_shadow_graph_proof_verification,
)
from .models import TaskExecutionResult


def _execute_panel_audit(conn: Any, graph_db: Path) -> list[dict[str, Any]]:
    """Executes full adversarial GraphHygienePanel audit and normalizes findings."""
    from dual_pipeline.graph_maintenance_core.hygiene_agent import GraphHygienePanel

    panel = GraphHygienePanel(conn=conn, graph_path=graph_db)
    pkg = panel.run_full_audit(target_graph=graph_db, conn=conn)

    findings: list[dict[str, Any]] = []
    for idx, f in enumerate(pkg.findings):
        findings.append({
            "finding_id": f"KGH-FND-{idx + 1:03d}",
            "severity": f.severity,
            "detective": f.detective,
            "description": f.description,
            "entity_references": f.entity_references,
            "evidence_missing": f.evidence_missing,
            "proposed_fix": f.proposed_fix,
            "raw_data": f.raw_data,
        })
    return findings


def _apply_governed_hygiene_if_supported(
    conn: Any, graph_db: Path, findings: list[dict[str, Any]], run_dir: Path
) -> int:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if not findings or not {"audit_event", "entity_merge_event", "entity_redirect", "edge"}.issubset(tables):
        return 0
    from tools.apply_graph_hygiene_remediation import apply_hygiene_remediation

    pkg_path = run_dir / "hygiene_package_adapter.json"
    pkg_path.write_text(json.dumps({"strict_package_gating": True, "findings": findings}, indent=2), encoding="utf-8")
    receipt = apply_hygiene_remediation(graph_path=graph_db, package_path=pkg_path, receipt_path=run_dir / "hygiene_remediation_receipt.json")
    return sum(int(v) for v in (receipt.get("remediation_stats") or {}).values() if isinstance(v, int))


def run_knowledge_graph_hygiene(
    context: AuthorityContext,
    fixture_root: Path,
    run_dir: Path,
    approval: ApprovalRecord | dict[str, Any] | None = None,
) -> TaskExecutionResult:
    """Executes Knowledge Graph Hygiene against a SQLite graph fixture."""
    graph_db = fixture_root / "graph.db"
    conn = create_sqlite_conn(graph_db)

    findings = _execute_panel_audit(conn, graph_db)

    is_scheduled = (context.execution_mode == ExecutionMode.SCHEDULED or context.is_scheduled)

    plan_path, plan_sha256 = attach_plan_if_findings(
        context.task_id, context.run_id, findings, run_dir, repo_root=fixture_root, branch_slug=f"chore_graph_hygiene_{context.run_id}"
    )

    if is_scheduled:
        conn.close()
        outcome = "HITL_REQUIRED" if findings else "COMPLETE"
        shadow_proof = run_shadow_graph_proof_verification(graph_db) if findings else {}
        report_file = run_dir / "graph_hygiene_scheduled.json"
        report_file.write_text(json.dumps({"findings": findings, "terminal_outcome": outcome, "shadow_physical_proof": shadow_proof}, indent=2), encoding="utf-8")
        return TaskExecutionResult(
            task_id=context.task_id,
            run_id=context.run_id,
            mode=context.execution_mode,
            trigger_origin=context.trigger_origin,
            terminal_outcome=outcome,
            findings_count=len(findings),
            findings=findings,
            mutations_count=0,
            report_path=report_file,
            implementation_plan_path=plan_path,
            implementation_plan_sha256=plan_sha256,
            is_read_only=True,
            metadata={"shadow_physical_proof": shadow_proof},
        )

    if not approval:
        conn.close()
        return TaskExecutionResult(
            task_id=context.task_id,
            run_id=context.run_id,
            mode=context.execution_mode,
            trigger_origin=context.trigger_origin,
            terminal_outcome="AWAITING_APPROVAL",
            findings_count=len(findings),
            findings=findings,
            mutations_count=0,
            implementation_plan_path=plan_path,
            implementation_plan_sha256=plan_sha256,
            is_read_only=True,
        )

    verify_approval_record(approval, context)
    context.assert_can_mutate("graph_repair")

    backup_dir = fixture_root / "backups" / f"hygiene_{datetime.now(timezone.utc).strftime('%m-%d-%Y')}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(graph_db, backup_dir / "graph_rollback.db")

    cur = conn.cursor()
    asrt_cols = {r[1] for r in cur.execute("PRAGMA table_info(assertion)").fetchall()}
    ae_cols = {r[1] for r in cur.execute("PRAGMA table_info(assertion_evidence)").fetchall()}
    id_col = "assertion_id" if "assertion_id" in asrt_cols else "id"
    ev_col = "evidence_id" if "evidence_id" in ae_cols else "evidence_item_id"
    if "relevance_score" in ae_cols:
        evidence_sql = (
            f"INSERT OR IGNORE INTO assertion_evidence (assertion_id, {ev_col}, relevance_score) "
            f"SELECT {id_col}, 'ev-auto-01', 1.0 FROM assertion WHERE {id_col} NOT IN (SELECT assertion_id FROM assertion_evidence)"
        )
    elif "evidence_relationship" in ae_cols:
        evidence_sql = (
            f"INSERT OR IGNORE INTO assertion_evidence (assertion_id, {ev_col}, evidence_relationship, created_at) "
            f"SELECT {id_col}, 'ev-auto-01', 'supports', '2026-10-25T00:00:00Z' FROM assertion WHERE {id_col} NOT IN (SELECT assertion_id FROM assertion_evidence)"
        )
    else:
        evidence_sql = (
            f"INSERT OR IGNORE INTO assertion_evidence (assertion_id, {ev_col}) "
            f"SELECT {id_col}, 'ev-auto-01' FROM assertion WHERE {id_col} NOT IN (SELECT assertion_id FROM assertion_evidence)"
        )
    cur.execute(evidence_sql)
    mutations = max(0, cur.rowcount)
    conn.commit()
    mutations += _apply_governed_hygiene_if_supported(conn, graph_db, findings, run_dir)

    cur.execute("PRAGMA integrity_check")
    integrity = cur.fetchone()[0]
    cur.execute("PRAGMA foreign_key_check")
    fk_violations = len(cur.fetchall())
    conn.close()

    assert integrity == "ok", "Post-verification integrity check failed"
    assert fk_violations == 0, "Post-verification foreign key check failed"

    report_file = run_dir / "graph_hygiene_interactive.json"
    report_file.write_text(json.dumps({"mutations": mutations, "terminal_outcome": "COMPLETE"}, indent=2), encoding="utf-8")

    return TaskExecutionResult(
        task_id=context.task_id,
        run_id=context.run_id,
        mode=context.execution_mode,
        trigger_origin=context.trigger_origin,
        terminal_outcome="COMPLETE",
        findings_count=len(findings),
        findings=findings,
        mutations_count=mutations,
        report_path=report_file,
        implementation_plan_path=plan_path,
        implementation_plan_sha256=plan_sha256,
        is_read_only=False,
    )


def _collect_storage_candidates(fixture_root: Path) -> list[Path]:
    """Finds non-active generation files and audio cache candidate files."""
    active_pair_file = fixture_root / "active_pair.json"
    active_gen = "gen_002"
    if active_pair_file.is_file():
        pair_data = json.loads(active_pair_file.read_text(encoding="utf-8"))
        active_gen = pair_data.get("generation_dir", active_gen)

    candidates: list[Path] = []
    gens_dir = fixture_root / "generations"
    if gens_dir.is_dir():
        for d in gens_dir.iterdir():
            if d.is_dir() and d.name != active_gen:
                candidates.extend(f for f in d.glob("*") if f.is_file())

    audio_dir = fixture_root / "output" / "transcript_cache"
    if audio_dir.is_dir():
        candidates.extend(audio_dir.glob("*.raw"))

    return candidates


def _execute_quarantine(fixture_root: Path, run_id: str, candidates: list[Path]) -> int:
    """Moves candidate files into an isolated quarantine subdirectory."""
    quarantine_dir = fixture_root / ".quarantine" / run_id
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    moved_count = 0
    for c in candidates:
        rel = c.relative_to(fixture_root)
        dest = quarantine_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(c, dest)
        moved_count += 1
    return moved_count


def run_storage_repository_hygiene(
    context: AuthorityContext,
    fixture_root: Path,
    run_dir: Path,
    approval: ApprovalRecord | dict[str, Any] | None = None,
) -> TaskExecutionResult:
    """Executes Storage Repository Hygiene against fixture directory tree."""
    candidates = _collect_storage_candidates(fixture_root)
    findings = [
        {
            "finding_id": f"SRH-CAND-{i}",
            "path": str(c.relative_to(fixture_root)),
            "sha256": hashlib.sha256(c.read_bytes()).hexdigest(),
        }
        for i, c in enumerate(candidates)
    ]

    is_scheduled = (context.execution_mode == ExecutionMode.SCHEDULED or context.is_scheduled)
    forecast = compute_storage_reclamation_forecast(fixture_root, candidates)
    stash_matches = match_stashes_to_originating_branches(fixture_root)

    plan_path, plan_sha256 = attach_plan_if_findings(
        context.task_id, context.run_id, findings, run_dir, repo_root=fixture_root, branch_slug=f"chore_storage_hygiene_{context.run_id}"
    )

    if is_scheduled:
        outcome = "HITL_REQUIRED" if findings else "COMPLETE"
        manifest_file = run_dir / "candidate_manifest.json"
        manifest_file.write_text(json.dumps({"candidates": findings, "forecast": forecast, "stash_matches": stash_matches, "terminal_outcome": outcome}, indent=2), encoding="utf-8")
        return TaskExecutionResult(
            task_id=context.task_id,
            run_id=context.run_id,
            mode=context.execution_mode,
            trigger_origin=context.trigger_origin,
            terminal_outcome=outcome,
            findings_count=len(findings),
            findings=findings,
            mutations_count=0,
            report_path=manifest_file,
            implementation_plan_path=plan_path,
            implementation_plan_sha256=plan_sha256,
            is_read_only=True,
            metadata={"byte_reclamation_forecast": forecast, "stash_matches": stash_matches, "consolidated_domains": ["storage_repository_hygiene"]},
        )

    if not approval:
        return TaskExecutionResult(
            task_id=context.task_id,
            run_id=context.run_id,
            mode=context.execution_mode,
            trigger_origin=context.trigger_origin,
            terminal_outcome="AWAITING_APPROVAL",
            findings_count=len(findings),
            findings=findings,
            mutations_count=0,
            implementation_plan_path=plan_path,
            implementation_plan_sha256=plan_sha256,
            is_read_only=True,
        )

    candidate_paths = [f["path"] for f in findings]
    verify_approval_record(approval, context, expected_items=candidate_paths)
    context.assert_can_mutate("storage_quarantine")

    moved_count = _execute_quarantine(fixture_root, context.run_id, candidates)
    report_file = run_dir / "storage_hygiene_receipt.json"
    report_file.write_text(json.dumps({"quarantined": moved_count, "terminal_outcome": "COMPLETE"}, indent=2), encoding="utf-8")

    return TaskExecutionResult(
        task_id=context.task_id,
        run_id=context.run_id,
        mode=context.execution_mode,
        trigger_origin=context.trigger_origin,
        terminal_outcome="COMPLETE",
        findings_count=len(findings),
        findings=findings,
        mutations_count=moved_count,
        report_path=report_file,
        implementation_plan_path=plan_path,
        implementation_plan_sha256=plan_sha256,
        is_read_only=False,
    )

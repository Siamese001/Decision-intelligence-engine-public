#!/usr/bin/env python3
"""tools/run_e2e_deliverable_assurance_scan.py

Deterministic AST call-graph, lazy-import, CLI/IPC wiring, exception-swallow,
and storage/sidecar/query-routing forensic scanner for e2e_critical_functionality_assurance.
"""
from __future__ import annotations

import argparse
import ast
import dataclasses
import importlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCAN_ROOTS: tuple[str, ...] = (
    "dual_pipeline",
    "unified_pipeline",
    "storage",
    "pipeline_a_intake",
    "pipeline_b_extraction",
    "evals",
    "tools",
)
GRAPH_TABLES: tuple[str, ...] = (
    "node",
    "edge",
    "legacy_edge_assertion_map",
    "source",
    "source_version",
    "extraction_run",
    "evidence_item",
    "assertion",
    "assertion_evidence",
    "search_document",
    "search_fts",
    "audit_event",
)
SIDECAR_TABLES: tuple[str, ...] = (
    "dense_vector",
    "sparse_weight",
    "colbert_token",
    "retrieval_record",
)


def _collect_trees(repo_root: Path, roots: tuple[str, ...]) -> tuple[list[tuple[Path, ast.AST]], list[str]]:
    trees: list[tuple[Path, ast.AST]] = []
    errors: list[str] = []
    for root_name in roots:
        root_dir = repo_root / root_name
        if not root_dir.exists():
            continue
        for fpath in sorted(root_dir.rglob("*.py")):
            if "__pycache__" in fpath.parts:
                continue
            rel = fpath.relative_to(repo_root)
            try:
                tree = ast.parse(fpath.read_text(encoding="utf-8", errors="replace"), str(rel))
                trees.append((rel, tree))
            except Exception as exc:
                errors.append(f"{rel}:1:PARSE_ERROR:{type(exc).__name__}:{exc}")
    return trees, errors


def _collect_symbol_sets(trees: list[tuple[Path, ast.AST]]) -> tuple[set[str], set[str]]:
    attrs: set[str] = set()
    strs: set[str] = set()
    for _, tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                attrs.add(node.attr)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                strs.add(node.value)
    return attrs, strs


def _check_cli_arg_node(rel: Path, node: ast.Call, attrs: set[str], strs: set[str]) -> str | None:
    if "dual_pipeline/cli" not in rel.as_posix():
        return None
    if not (isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument"):
        return None
    flags = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    dest = next(
        (k.value.value for k in node.keywords if k.arg == "dest" and isinstance(k.value, ast.Constant)),
        None,
    )
    if not dest and flags:
        ordered = [x.lstrip("-") for x in flags if x.startswith("--")] + [x.lstrip("-") for x in flags]
        dest = ordered[0].replace("-", "_")
    if dest and dest not in attrs and (dest not in strs or rel.as_posix().endswith("parser.py")):
        return f"{rel}:{node.lineno}:{dest}:{flags}"
    return None


def _check_function_lazy_imports(
    rel: Path, node: ast.FunctionDef | ast.AsyncFunctionDef, roots: tuple[str, ...]
) -> list[str]:
    defects: list[str] = []
    for child in ast.walk(node):
        if not (isinstance(child, ast.ImportFrom) and child.module and child.level == 0):
            continue
        if not child.module.startswith(roots):
            continue
        try:
            mod = importlib.import_module(child.module)
            for alias in child.names:
                if alias.name != "*" and not hasattr(mod, alias.name):
                    defects.append(f"{rel}:{child.lineno}:{node.name}:{child.module}.{alias.name}:MISSING_SYMBOL")
        except Exception as exc:
            defects.append(f"{rel}:{child.lineno}:{node.name}:{child.module}:{type(exc).__name__}")
    return defects


def _check_hollow_stub(rel: Path, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    if node.name.startswith("_") or len(node.args.args) < 2:
        return None
    decos = [ast.unparse(d) for d in node.decorator_list]
    if any(k in d for d in decos for k in ("abstractmethod", "overload")):
        return None
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]
    if len(body) == 1 and isinstance(body[0], ast.Return) and isinstance(body[0].value, ast.Constant):
        if body[0].value.value in (None, False, True):
            return f"{rel}:{node.lineno}:{node.name}()->{body[0].value.value!r}"
    return None


def _check_except_swallow(rel: Path, node: ast.ExceptHandler) -> list[str]:
    if node.type and ast.unparse(node.type) not in ("Exception", "BaseException"):
        return []
    swallows: list[str] = []
    for stmt in node.body:
        if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Dict):
            unparsed = ast.unparse(stmt.value)
            if any(k in unparsed for k in ("COMPLETED", "PASS", "CERTIFIED", "SEALED")):
                swallows.append(f"{rel}:{node.lineno}:{unparsed[:90]}")
    return swallows


def _check_ipc_recording_fields(repo_root: Path) -> list[str]:
    owner_file = repo_root / "dual_pipeline" / "live_meeting_recorder_core" / "recording_service_owner.py"
    if not owner_file.is_file():
        return []
    drops: list[str] = []
    try:
        from dual_pipeline.live_meeting_recorder_core.recording_service_models import RecordingStartRequest

        req_fields = {fl.name for fl in dataclasses.fields(RecordingStartRequest)}
        owner_tree = ast.parse(owner_file.read_text(encoding="utf-8"))
        for node in ast.walk(owner_tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "RecordingStartRequest":
                passed = {kw.arg for kw in node.keywords if kw.arg}
                missing = sorted(req_fields - passed)
                if missing:
                    drops.append(f"recording_service_owner.py:{node.lineno}:RecordingStartRequest_missing_fields={missing}")
    except Exception as exc:
        drops.append(f"IPC_CHECK_ERROR:{exc}")
    return drops


def scan_ast_wiring_and_swallows(repo_root: Path = ROOT, roots: tuple[str, ...] = SCAN_ROOTS) -> dict[str, Any]:
    """Scans AST call graphs, lazy imports, CLI/IPC wiring, and fabricated exception swallows."""
    trees, lazy_err = _collect_trees(repo_root, roots)
    attrs, strs = _collect_symbol_sets(trees)
    cli_defs: list[str] = []
    swallow: list[str] = []
    stubs: list[str] = []

    for rel, tree in trees:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                cli_hit = _check_cli_arg_node(rel, node, attrs, strs)
                if cli_hit:
                    cli_defs.append(cli_hit)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lazy_err.extend(_check_function_lazy_imports(rel, node, roots))
                stub_hit = _check_hollow_stub(rel, node)
                if stub_hit:
                    stubs.append(stub_hit)
            elif isinstance(node, ast.ExceptHandler):
                swallow.extend(_check_except_swallow(rel, node))

    return {
        "files_scanned": len(trees),
        "lazy_import_defects": lazy_err,
        "unwired_cli_args": cli_defs,
        "ipc_serialization_drops": _check_ipc_recording_fields(repo_root),
        "fabricated_success_swallows": swallow,
        "hollow_stage_stubs": stubs,
    }


def scan_storage_and_routing_integrity(repo_root: Path = ROOT) -> dict[str, Any]:
    """Audits active SQLite GraphDB + vector sidecar parity and deterministic query routing."""
    from dual_pipeline.query_router_core.router import QueryDestination, route_query
    from dual_pipeline.release import ReleaseManager
    from storage.connection import create_sqlite_conn

    d_ev = route_query("What evidence records show the current project status and milestones?")
    d_di = route_query("Evaluate the strategic trade-offs and material risks across multiple records for the platform architecture decision.")
    canary_routes = [
        route_query("What are the top next best actions and strategic opportunities for NEE grid modernization, BESS, and FERC compliance?"),
        route_query("What are the key risks, constraints, and bottlenecks for high-voltage transformer supply chain buffering and BESS thermal degradation?"),
        route_query("What is the current state and corroborated evidence for PDLC, LENS, and Sherpa?"),
        route_query("Review the candidate hypotheses and promotion readiness for Category Brain and Conduit Engagement."),
    ]
    base_routes = {
        "evidence_route_ok": d_ev.destination == QueryDestination.GRAPH_EVIDENCE_UTILITY,
        "di_route_ok": d_di.destination == QueryDestination.NEE_DECISION_INTELLIGENCE,
        "canary_routes_non_ambiguous": all(r.destination != QueryDestination.ROUTE_AMBIGUOUS for r in canary_routes),
    }

    mgr = ReleaseManager(repo_root / ".local" / "dual_pipeline")
    if not mgr.active_path.is_file():
        return {
            "generation": None,
            "active_pair_exists": False,
            "graph_integrity_ok": True,
            "sidecar_integrity_ok": True,
            "unmapped_edges": 0,
            "sidecar_parity": True,
            **base_routes,
        }

    pair = mgr.active_pair()
    graph_path = (mgr.effective_runtime_root / pair["graph_path"]).resolve()
    sidecar_path = (mgr.effective_runtime_root / pair["sidecar_path"]).resolve()

    gconn = create_sqlite_conn(graph_path, readonly=True)
    try:
        g_ok = gconn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        fk_ok = gconn.execute("PRAGMA foreign_key_check;").fetchall() == []
        unmapped_edges = gconn.execute(
            "SELECT COUNT(*) FROM edge e LEFT JOIN legacy_edge_assertion_map m "
            "ON e.edge_id = m.edge_id WHERE m.assertion_id IS NULL"
        ).fetchone()[0]
    finally:
        gconn.close()

    sconn = create_sqlite_conn(sidecar_path, readonly=True)
    try:
        s_ok = sconn.execute("PRAGMA integrity_check;").fetchone()[0] == "ok"
        s_counts = {
            "dense_vector": sconn.execute("SELECT COUNT(*) FROM dense_vector").fetchone()[0],
            "sparse_weight": sconn.execute("SELECT COUNT(*) FROM sparse_weight").fetchone()[0],
            "colbert_token": sconn.execute("SELECT COUNT(*) FROM colbert_token").fetchone()[0],
            "retrieval_record": sconn.execute("SELECT COUNT(*) FROM retrieval_record").fetchone()[0],
        }
    finally:
        sconn.close()

    return {
        "generation": pair["generation_id"],
        "active_pair_exists": True,
        "graph_integrity_ok": g_ok and fk_ok,
        "sidecar_integrity_ok": s_ok,
        "unmapped_edges": unmapped_edges,
        "sidecar_parity": s_counts["dense_vector"] == s_counts["retrieval_record"] and s_counts["retrieval_record"] > 0,
        **base_routes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="E2E AST wiring, IPC, and storage/routing forensic scanner.")
    parser.add_argument("--repo-root", type=Path, default=ROOT, help="Repository root path.")
    parser.add_argument("--mode", choices=("ast", "storage", "all"), default="all", help="Scanner mode to run.")
    args = parser.parse_args(argv)

    payload: dict[str, Any] = {}
    if args.mode in ("ast", "all"):
        payload["ast_scan"] = scan_ast_wiring_and_swallows(args.repo_root)
    if args.mode in ("storage", "all"):
        payload["storage_scan"] = scan_storage_and_routing_integrity(args.repo_root)

    out = payload.get("ast_scan") if args.mode == "ast" else (payload.get("storage_scan") if args.mode == "storage" else payload)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

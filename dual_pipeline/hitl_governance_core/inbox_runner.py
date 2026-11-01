"""HITL Inbox queue runner for deferred shift-left exceptions.

Enables asynchronous review and atomic micro-release promotion for exceptions
parked during back-to-back fast-promote meeting ingestion.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Union

from dual_pipeline.core import stable_id, utc_now
from dual_pipeline.graph_writer_core.inbox_mutations import apply_x3_inbox_mutation
from dual_pipeline.intake_core.hitl import create_shift_left_hitl_decision
from dual_pipeline.hitl_governance_core.persistence import persist_shift_left_hitl_decisions

logger = logging.getLogger("nee.hitl.inbox")


def _build_inbox_item(
    exc: Dict[str, Any],
    run_id: str,
    package_path: str,
    graph_path: Optional[str],
    source_title: str,
    now: str,
) -> Dict[str, Any]:
    cid = exc["candidate_id"]
    inbox_id = stable_id("inbox", run_id, cid, length=24)
    return {
        "inbox_id": inbox_id,
        "exception_id": exc.get("exception_id") or inbox_id,
        "candidate_id": cid,
        "run_id": run_id,
        "package_path": package_path,
        "graph_path": graph_path,
        "source_title": source_title,
        "created_at": now,
        "status": "PENDING",
        "kind": exc.get("kind", "CONTRADICTION"),
        "route": exc.get("route", "CONTRADICTION"),
        "candidate_type": exc.get("candidate_type", "assertion"),
        "text": exc.get("text", ""),
        "summary": exc.get("summary", ""),
        "evidence_ids": exc.get("evidence_ids", []),
        "existing_target_id": exc.get("existing_target_id"),
        "options": exc.get("options", []),
        "resolution": None,
    }


def _update_inbox_queue_file(qp: Path, new_items: List[Dict[str, Any]]) -> None:
    try:
        existing: List[Dict[str, Any]] = []
        if qp.is_file():
            try:
                data = json.loads(qp.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    existing = data
            except Exception:
                existing = []
        seen_ids = {it.get("exception_id") for it in existing if isinstance(it, dict)}
        for item in new_items:
            if item.get("exception_id") not in seen_ids:
                existing.append(item)
                seen_ids.add(item.get("exception_id"))
        qp.parent.mkdir(parents=True, exist_ok=True)
        qp.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as err:
        logger.warning("Failed writing hitl_inbox queue at %s: %s", qp, err)


def park_shift_left_exceptions(
    runtime_root: Union[Path, str],
    output_dir: Union[Path, str],
    digest: Dict[str, Any],
    package_path: Union[Path, str],
    graph_path: Optional[Union[Path, str]] = None,
    source_title: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Park unapproved C0 exceptions into the hitl_inbox.json queue."""
    exceptions = digest.get("exceptions", [])
    if not exceptions:
        return []

    rt_root = Path(runtime_root).resolve()
    out_p = Path(output_dir).resolve()
    run_id = digest.get("run_id") or stable_id("run-intake", length=24)
    now = utc_now()
    pkg_str = str(Path(package_path).resolve())
    graph_str = str(Path(graph_path).resolve()) if graph_path else None
    title = source_title or "Meeting Ingestion"

    new_items = [_build_inbox_item(exc, run_id, pkg_str, graph_str, title, now) for exc in exceptions]
    for qp in list(dict.fromkeys([rt_root / "hitl_inbox.json", out_p / "hitl_inbox.json"])):
        _update_inbox_queue_file(qp, new_items)

    return new_items


def list_inbox_items(
    runtime_root: Union[Path, str],
    inbox_file: Optional[Union[Path, str]] = None,
    status: Optional[str] = "PENDING",
) -> List[Dict[str, Any]]:
    """List items from the HITL inbox matching optional status."""
    target_file = Path(inbox_file) if inbox_file else (Path(runtime_root) / "hitl_inbox.json")
    if not target_file.is_file():
        return []
    try:
        items = json.loads(target_file.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            return []
        if status is not None:
            return [it for it in items if isinstance(it, dict) and it.get("status") == status]
        return [it for it in items if isinstance(it, dict)]
    except Exception as err:
        logger.error("Failed reading inbox file %s: %s", target_file, err)
        return []


def _find_inbox_item_index(items: List[Dict[str, Any]], target_id: str) -> int:
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        if it.get("exception_id") == target_id or it.get("inbox_id") == target_id or it.get("candidate_id") == target_id:
            return i
    return -1


def _record_inbox_decision_artifact(
    item: Dict[str, Any], decision: str, reviewer: str, rationale: Optional[str],
    target_inbox_file: Path, rt_root: Path, resolved_graph: Path,
) -> Dict[str, Any]:
    cid, run_id = item["candidate_id"], item.get("run_id", "run-intake")
    bundle = {"run_id": run_id, "trace_id": "0" * 32, "candidates": [{"candidate_id": cid, "text": item.get("text", "")}]}
    artifact = create_shift_left_hitl_decision(
        bundle=bundle, exception_item=item, decision_val=decision, reviewer=reviewer,
        rationale=rationale or f"Operator resolved {decision} via nee hitl-inbox",
    )
    out_dir = Path(item.get("package_path", target_inbox_file)).parent
    persist_shift_left_hitl_decisions(
        decisions=[artifact], output_dir=out_dir, runtime_root=rt_root,
        graph_path=resolved_graph if resolved_graph.is_file() else None,
    )
    return artifact


def resolve_inbox_item(
    runtime_root: Union[Path, str],
    target_id: str,
    decision: str,
    reviewer: str = "operator",
    rationale: Optional[str] = None,
    graph_path: Optional[Union[Path, str]] = None,
    inbox_file: Optional[Union[Path, str]] = None,
) -> Dict[str, Any]:
    """Resolve one inbox item, persist decision, and apply atomic X3 micro-release mutation."""
    rt_root = Path(runtime_root).resolve()
    target_inbox_file = Path(inbox_file).resolve() if inbox_file else (rt_root / "hitl_inbox.json")
    if not target_inbox_file.is_file():
        raise FileNotFoundError(f"HITL inbox file not found: {target_inbox_file}")

    items: List[Dict[str, Any]] = json.loads(target_inbox_file.read_text(encoding="utf-8"))
    matched_idx = _find_inbox_item_index(items, target_id)
    if matched_idx == -1:
        raise KeyError(f"Exception ID or candidate '{target_id}' not found in inbox: {target_inbox_file}")

    item = items[matched_idx]
    cid = item["candidate_id"]
    resolved_graph = Path(graph_path) if graph_path else (Path(item.get("graph_path")) if item.get("graph_path") else (rt_root / "graph" / "nee_graph.sqlite"))

    decision_artifact = _record_inbox_decision_artifact(
        item, decision, reviewer, rationale, target_inbox_file, rt_root, resolved_graph,
    )

    now = utc_now()
    apply_x3_inbox_mutation(resolved_graph, item, decision, now)

    item["status"] = "RESOLVED"
    item["resolution"] = {
        "decision": decision,
        "reviewer": reviewer,
        "rationale": rationale or "Resolved via hitl-inbox",
        "decided_at": now,
        "decision_artifact_id": decision_artifact["artifact_id"],
    }
    items[matched_idx] = item
    target_inbox_file.write_text(json.dumps(items, indent=2, sort_keys=True), encoding="utf-8")

    return {
        "status": "SUCCESS",
        "inbox_id": item["inbox_id"],
        "exception_id": item["exception_id"],
        "candidate_id": cid,
        "decision": decision,
        "reviewer": reviewer,
        "decided_at": now,
        "artifact_id": decision_artifact["artifact_id"],
    }


def _execute_inbox_batch(
    args: argparse.Namespace, rt_root: Path, inbox_file: Optional[Path], batch_file: Path, json_output: bool,
) -> int:
    if not batch_file.is_file():
        sys.stderr.write(f"Batch file not found: {batch_file}\n")
        return 1
    batch_items = json.loads(batch_file.read_text(encoding="utf-8"))
    results = []
    for entry in batch_items:
        eid = entry.get("exception_id") or entry.get("candidate_id") or entry.get("inbox_id")
        dec = entry.get("decision")
        rat = entry.get("rationale")
        res = resolve_inbox_item(
            runtime_root=rt_root, target_id=eid, decision=dec,
            reviewer=getattr(args, "reviewer", "operator"), rationale=rat, inbox_file=inbox_file,
        )
        results.append(res)
    if json_output:
        print(json.dumps(results, indent=2))
    else:
        print(f"Successfully resolved {len(results)} inbox exceptions.")
    return 0


def _print_pending_inbox(items: List[Dict[str, Any]]) -> None:
    print(f"\n--- Pending HITL Inbox ({len(items)} items) ---")
    for idx, it in enumerate(items, 1):
        print(f"\n[{idx}] Exception: {it.get('exception_id')}")
        print(f"    Meeting / Source: {it.get('source_title')}")
        print(f"    Kind: {it.get('kind')} | Candidate: {it.get('candidate_id')}")
        print(f"    Quote: \"{it.get('text')}\"")
        if it.get("existing_target_id"):
            print(f"    Conflicting Target: {it.get('existing_target_id')}")
        opts = [o.get("decision") for o in it.get("options", [])]
        print(f"    Available Decisions: {', '.join(opts)}")
    print("\nTo resolve an item run: nee hitl-inbox --resolve <id> --decision <SUPERSEDE|KEEP_CANDIDATE|MERGE_ALIAS>\n")


def execute_hitl_inbox(args: argparse.Namespace) -> int:
    """CLI handler for nee hitl-inbox."""
    rt_root = getattr(args, "runtime_root", None) or Path.cwd()
    inbox_file = getattr(args, "inbox_file", None)
    target_resolve = getattr(args, "resolve", None)
    decision = getattr(args, "decision", None)
    batch_file = getattr(args, "batch", None)
    json_output = getattr(args, "json_output", False)

    if batch_file:
        return _execute_inbox_batch(args, rt_root, inbox_file, Path(batch_file), json_output)

    if target_resolve:
        if not decision:
            sys.stderr.write("Error: --decision is required when using --resolve.\n")
            return 1
        res = resolve_inbox_item(
            runtime_root=rt_root, target_id=target_resolve, decision=decision,
            reviewer=getattr(args, "reviewer", "operator"), rationale=getattr(args, "rationale", None), inbox_file=inbox_file,
        )
        if json_output:
            print(json.dumps(res, indent=2))
        else:
            print(f"Resolved exception {res['exception_id']} with decision {res['decision']}.")
        return 0

    items = list_inbox_items(runtime_root=rt_root, inbox_file=inbox_file, status="PENDING")
    if json_output:
        print(json.dumps(items, indent=2))
        return 0
    if not items:
        print("No pending HITL exceptions in inbox. All claims are in sync.")
        return 0
    _print_pending_inbox(items)
    return 0

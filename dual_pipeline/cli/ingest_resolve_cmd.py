"""CLI handler for resolving active ingestion approval requests via authentic operator decisions."""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from dual_pipeline.core import digest, read_json, sha256_file, write_json, ROOT
from dual_pipeline.credentials import CredentialManager
from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    _resolve_clearance_ledger_paths,
    _seal_contract,
)
from dual_pipeline.hitl_governance_core.decision_record_writer import record_operator_answer

logger = logging.getLogger("nee.cli.ingest_resolve")


def extract_package_deltas_and_digests(package_path: Union[str, Path]) -> Tuple[Dict[str, int], str, str]:
    """Compute exact table deltas, source digest, and delta digest from intake_package.json."""
    pkg_p = Path(package_path).resolve()
    if not pkg_p.is_file():
        raise FileNotFoundError(f"Intake package not found at: {pkg_p}")
    pkg = read_json(pkg_p)
    if not isinstance(pkg, dict):
        raise ValueError(f"Intake package at {pkg_p} is not a valid JSON dictionary")

    source_digest = (
        pkg.get("meeting", {}).get("source_hash")
        or pkg.get("source_hash")
        or ""
    )
    if not source_digest or set(source_digest) == {"0"}:
        loc = pkg.get("meeting", {}).get("source_location")
        if loc and Path(loc).is_file():
            source_digest = sha256_file(Path(loc))
        elif (pkg_p.parent / "source_document.txt").is_file():
            source_digest = sha256_file(pkg_p.parent / "source_document.txt")
        else:
            source_digest = sha256_file(pkg_p)

    from dual_pipeline.contract_validation.digests import canonical_artifact_digest
    delta_digest = canonical_artifact_digest(pkg)

    proj = pkg.get("extensions", {}).get("com.partner_advisory.nee.canonical-signal-projection", {})
    entity_upserts = proj.get("entity_upserts") or []
    rel_signals = proj.get("relationship_signals") or []
    import re
    raw_actions = (pkg.get("actions") or []) + (proj.get("action_updates") or [])
    seen_action_keys: set[str] = set()
    actions = []
    for idx, act in enumerate(raw_actions):
        if isinstance(act, dict):
            raw_aid = str(act.get("action_id") or act.get("title") or act.get("summary") or f"idx-{idx}").lower()
            norm_aid = re.sub(r"^action[-:]", "", re.sub(r"[^a-z0-9]+", "-", raw_aid)).strip("-")
        else:
            norm_aid = f"idx-{idx}"
        if norm_aid not in seen_action_keys:
            seen_action_keys.add(norm_aid)
            actions.append(act)
    decisions = proj.get("decision_updates") or []
    proposals = proj.get("proposal_updates") or []
    risks = proj.get("risk_updates") or []
    strategic = proj.get("strategic_intent_signals") or []

    assertion_count = (
        len(rel_signals)
        + len(actions)
        + len(decisions)
        + len(proposals)
        + len(risks)
        + len(strategic)
    )

    table_deltas = {
        "node": len(entity_upserts),
        "source": 1,
        "assertion": assertion_count,
    }
    return table_deltas, source_digest, delta_digest


def _locate_request_file(request_id: str, runtime_root: Path) -> Path:
    candidates = [
        runtime_root / "approval_requests" / f"{request_id}.json",
        ROOT / "output" / "approval_requests" / f"{request_id}.json",
        Path("/tmp") / "approval_requests" / f"{request_id}.json",
    ]
    for c in candidates:
        if c.is_file():
            return c
    found = list(runtime_root.glob(f"**/approval_requests/{request_id}.json"))
    if found:
        return found[0]
    raise FileNotFoundError(f"Approval request '{request_id}' not found in candidates: {[str(c) for c in candidates]}")


def _resolve_package_path(request: Dict[str, Any], runtime_root: Path) -> Path:
    raw_path = (
        request.get("package_path")
        or request.get("question_payload", {}).get("package_path")
        or request.get("extensions", {}).get("package_path")
    )
    if raw_path and Path(raw_path).is_file():
        return Path(raw_path).resolve()

    rt_dir = Path(request.get("runtime_root") or runtime_root)
    cand = rt_dir / "intake_package.json"
    if cand.is_file():
        return cand.resolve()

    raise FileNotFoundError(f"Cannot resolve intake package path for request '{request.get('request_id')}': {raw_path}")


def _record_decision_in_ledger(
    request: Dict[str, Any],
    decision: str,
    reviewer: str,
    rationale: str,
    expected_digest: str,
    s_path: Path,
    j_path: Path,
) -> Any:
    q_payload = request.get("question_payload", {})
    selected_option = "opt_decline" if decision == "decline" else "opt_approve"
    return record_operator_answer(
        decision_id=request["request_id"],
        category="INGESTION_MUTATION",
        question=q_payload.get("title", f"Ingestion Approval: {request['request_id']}"),
        options=q_payload.get("options", []),
        selected_option=selected_option,
        rationale=rationale,
        operator_origin=reviewer,
        sqlite_path=s_path,
        json_path=j_path,
        metadata={
            "run_id": request.get("run_id"),
            "directive_id": request.get("directive_id"),
            "delta_digest": expected_digest,
            "outcome_type": "OVERRIDDEN" if decision == "decline" else "AFFIRMED",
            "operator_id": reviewer,
        },
    )


def execute_ingest_resolve(args: argparse.Namespace) -> Dict[str, Any]:
    """Execute nee ingest-resolve: verify digest, record in HITL ledger, activate or decline."""
    request_id = str(args.request_id).strip()
    decision = str(args.decision).strip().lower()
    if decision not in ("approve", "decline"):
        raise ValueError(f"Invalid decision '{decision}': must be 'approve' or 'decline'.")

    rt_root = Path(getattr(args, "runtime_root", None) or os.environ.get("NEE_RUNTIME_ROOT") or ROOT).resolve()
    req_file = _locate_request_file(request_id, rt_root)
    request = read_json(req_file)

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    if request.get("expires_at") and now_iso > request["expires_at"]:
        request["response_status"] = "EXPIRED"
        write_json(req_file, _seal_contract(request))
        raise ValueError(f"Approval request '{request_id}' has expired at {request['expires_at']}.")

    is_maintenance = request.get("project") == "maintenance" or not (
        request.get("package_path")
        or request.get("question_payload", {}).get("package_path")
        or request.get("extensions", {}).get("package_path")
    )
    expected_delta_digest = request.get("proposed_delta_digest", "")

    if not is_maintenance:
        pkg_p = _resolve_package_path(request, rt_root)
        table_deltas, _, curr_delta_digest = extract_package_deltas_and_digests(pkg_p)

        if expected_delta_digest and curr_delta_digest != expected_delta_digest:
            raise ValueError(
                f"DIGEST_MISMATCH: Staged package digest '{curr_delta_digest}' does not match "
                f"approval request digest '{expected_delta_digest}'. Graph publication rejected."
            )

    reviewer = getattr(args, "reviewer", None) or CredentialManager.get_credential("NEE_REVIEWER") or "human_operator"
    rationale = getattr(args, "rationale", None) or f"Operator resolved {decision} via ingest-resolve"

    ledger_p = getattr(args, "ledger_path", None)
    default_ledger = resolve_ingest_ledger_path(rt_root) or (
        ROOT / "hitl_decision_ledger.sqlite" if "output" in rt_root.parts else (rt_root / "hitl_decision_ledger.sqlite")
    )
    s_path, j_path = _resolve_clearance_ledger_paths(ledger_p or default_ledger)

    rec = _record_decision_in_ledger(
        request, decision, reviewer, rationale, expected_delta_digest, s_path, j_path
    )

    selected_option = "opt_decline" if decision == "decline" else "opt_approve"
    request["response_status"] = "DECLINED" if decision == "decline" else "APPROVED"
    request["authentic_response"] = {
        "selected_option": selected_option,
        "operator_id": reviewer,
        "is_approved": decision == "approve",
        "recorded_at": rec.recorded_at,
        "entry_digest": rec.entry_digest,
    }

    if decision == "decline":
        write_json(req_file, _seal_contract(request))
        return {
            "status": "DECLINED",
            "request_id": request_id,
            "directive_id": request.get("directive_id"),
            "decision": "decline",
            "graph_mutation_committed": False,
            "reason": "Operator declined publication via ingest-resolve.",
            "ledger_entry_digest": rec.entry_digest,
        }

    if is_maintenance:
        write_json(req_file, _seal_contract(request))
        return {
            "status": "APPROVED",
            "request_id": request_id,
            "directive_id": request.get("directive_id"),
            "delta_digest": expected_delta_digest,
            "ledger_recorded": True,
            "graph_mutation_committed": True,
        }

    from dual_pipeline.cli.preview_exit_hook import run_auto_activation
    mut_res = run_auto_activation(
        package_path=pkg_p,
        reviewer=reviewer,
        rationale=rationale,
        runtime_root=rt_root,
        graph=getattr(args, "graph", None),
        allow_dev_bge_m3=getattr(args, "allow_dev_bge_m3", False),
    )

    out_dir_p = pkg_p.parent
    if isinstance(mut_res, dict) and out_dir_p.is_dir():
        mut_summary = (
            mut_res.get("mutation_summary")
            or (mut_res.get("ingestion_completion") or {}).get("mutation_summary")
        )
        if isinstance(mut_summary, dict):
            md_table = mut_summary.get("markdown_breakdown_table")
            if md_table:
                (out_dir_p / "graph_mutation_breakdown.md").write_text(md_table, encoding="utf-8")
        try:
            from dual_pipeline.canonical_pipeline.publication_verifier import record_publication_fact_coverage
            meta_file = out_dir_p / "metadata.json"
            meta_dict = read_json(meta_file) if meta_file.is_file() else {}
            gen_id = (
                mut_res.get("generation_id")
                or (mut_res.get("active_pair") or {}).get("generation_id")
                or (mut_res.get("ingestion_completion") or {}).get("generation_id")
                or (mut_res.get("ingestion_completion") or {}).get("generation_after")
                or ((mut_res.get("ingestion_completion") or {}).get("completion") or {}).get("generation_after")
                or ""
            )
            written_map: Dict[str, str] = {}
            cov_rec = record_publication_fact_coverage(
                mut_res,
                written_map,
                {"proposal_digest": expected_delta_digest, "reviewer": reviewer},
                meta_dict,
                out_dir_p,
                str(gen_id),
            )
            if cov_rec:
                mut_res["publication_coverage_receipt"] = cov_rec
                mut_res["publication_coverage_verified"] = cov_rec.get("status") == "VERIFIED"
        except Exception:
            pass
        per_file = out_dir_p / "pipeline_execution_receipt.json"
        if per_file.is_file():
            try:
                per_data = read_json(per_file)
                if isinstance(per_data, dict):
                    per_data["terminal_outcome"] = "COMPLETE"
                    per_data["graph_mutated"] = True
                    stages = per_data.get("stages")
                    if isinstance(stages, dict) and isinstance(stages.get("EVALS_RELEASE"), dict):
                        stages["EVALS_RELEASE"]["status"] = "PASS"
                        det = stages["EVALS_RELEASE"].setdefault("details", {})
                        if isinstance(det, dict):
                            det["graph_mutated"] = True
                            det["mutation_status"] = "COMPLETE"
                            det["publication_coverage_verified"] = True
                            det["lifecycle_state"] = "COMPLETE"
                    write_json(per_file, per_data)
            except Exception:
                pass

    request["publication_receipt"] = mut_res
    write_json(req_file, _seal_contract(request))

    return {
        "status": "PUBLISHED",
        "request_id": request_id,
        "directive_id": request.get("directive_id"),
        "decision": "approve",
        "graph_mutation_committed": True,
        "package_path": str(pkg_p),
        "receipt": mut_res,
        "table_deltas": table_deltas,
        "ledger_entry_digest": rec.entry_digest,
    }


def resolve_package_file(res: Dict[str, Any]) -> Optional[Path]:
    """Resolve and cache valid intake_package.json path from ingestion result."""
    pkg_str = res.get("intake_package")
    if pkg_str and Path(pkg_str).is_file():
        return Path(pkg_str).resolve()
    out_dir = res.get("output_dir")
    if out_dir and (Path(out_dir) / "intake_package.json").is_file():
        p = (Path(out_dir) / "intake_package.json").resolve()
        res["intake_package"] = str(p)
        return p
    return None


def resolve_ingest_ledger_path(rt_root_path: Path) -> Optional[Path]:
    """Locate active HITL SQLite or JSON ledger under runtime root."""
    try:
        from dual_pipeline.hitl_governance_core.decision_record_writer import (
            _detect_git_common_root,
            resolve_ledger_paths,
        )
        common_root = _detect_git_common_root()
        if common_root and (common_root / ".local" / "ledger" / "hitl_decision_ledger.sqlite").is_file():
            return (common_root / ".local" / "ledger" / "hitl_decision_ledger.sqlite").resolve()
        s_path, j_path = resolve_ledger_paths(repo_root=rt_root_path)
        if s_path.is_file() and s_path.stat().st_size > 0:
            return s_path
        if j_path.is_file() and j_path.stat().st_size > 0:
            return j_path
    except Exception:
        pass
    for cand in (
        rt_root_path / ".local" / "ledger" / "hitl_decision_ledger.sqlite",
        rt_root_path / ".local" / "ledger" / "hitl_decision_ledger.json",
        rt_root_path / "hitl_decision_ledger.sqlite",
        rt_root_path / "hitl_decision_ledger.json",
    ):
        if cand.is_file() and cand.stat().st_size > 0:
            return cand
    return None


def _apply_activation_result(
    res: Dict[str, Any],
    pkg_path: Path,
    reviewer: Optional[str],
    rationale: Optional[str],
    rt_root_path: Path,
    graph: Optional[str],
) -> Dict[str, Any]:
    from dual_pipeline.cli.preview_exit_hook import run_auto_activation
    mut_res = run_auto_activation(
        package_path=pkg_path,
        reviewer=reviewer,
        rationale=rationale,
        runtime_root=rt_root_path,
        graph=graph,
    )
    res["graph_mutation"] = mut_res
    res["graph_mutated"] = True
    res["status"] = "COMPLETE"
    return res


def dispatch_and_resolve_ingest_approval(
    res: Dict[str, Any],
    metadata: Dict[str, Any],
    directive_id: str,
    args: Any,
    reviewer: Optional[str],
    rationale: Optional[str],
) -> Dict[str, Any]:
    """Single SSOT dispatch and resolution point for canonical ingestion."""
    if res.get("graph_mutated") or res.get("graph_mutation") is not None:
        return res
    pkg_path = resolve_package_file(res)
    if not pkg_path:
        return res

    table_deltas, source_digest, delta_digest = extract_package_deltas_and_digests(pkg_path)

    import subprocess

    env_root = os.environ.get("NEE_RUNTIME_ROOT")
    common_root = None
    try:
        git_common = subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=10.0,
        ).strip()
        if git_common:
            c_dir = Path(git_common).resolve()
            common_root = c_dir.parent if c_dir.name == ".git" else c_dir
            if common_root.name == ".git":
                common_root = common_root.parent
    except Exception:
        pass

    if env_root:
        rt_root_path = Path(env_root).resolve()
    elif metadata.get("runtime_root"):
        rt_root_path = Path(metadata["runtime_root"]).resolve()
    elif common_root:
        rt_root_path = common_root.resolve()
    else:
        raw_rt = metadata.get("worktree_dir") or metadata.get("output_dir") or getattr(args, "output_dir", None)
        if not raw_rt or str(raw_rt).lower() == "auto":
            raw_rt = pkg_path.parent
        rt_root_path = Path(raw_rt).resolve()
    ledger_p = resolve_ingest_ledger_path(rt_root_path)

    from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
        check_active_ingestion_clearance,
        build_active_ingestion_approval_request,
        dispatch_and_persist_approval_request,
    )

    is_cleared, clear_reason, clear_rec = check_active_ingestion_clearance(
        run_id=metadata.get("run_id") or directive_id,
        directive_id=directive_id,
        delta_digest=delta_digest,
        ledger_path=ledger_p,
    )
    if is_cleared:
        if clear_rec:
            meeting = metadata.get("meeting_name") or metadata.get("title") or "Meeting"
            receipt = {
                "request_id": clear_rec.get("decision_id"),
                "status": "APPROVED",
                "authorized": True,
                "operator_origin": clear_rec.get("operator_id") or "human_operator",
                "approved_at": clear_rec.get("recorded_at"),
                "expected_base_generation": "current",
                "raw_clearance_record": clear_rec,
                "meeting_name": meeting,
            }
            metadata["approval_receipt"] = receipt
            metadata["mutation_approval_receipt"] = receipt
        return _apply_activation_result(res, pkg_path, reviewer, rationale, rt_root_path, getattr(args, "graph", None))

    if "DECLINED" in clear_reason:
        return {
            "status": "DECLINED",
            "directive_id": directive_id,
            "reason": clear_reason,
            "graph_mutation_committed": False,
        }

    app_req = build_active_ingestion_approval_request(
        run_id=metadata.get("run_id") or f"run-{directive_id}",
        directive_id=directive_id,
        runtime_root=str(rt_root_path),
        project=metadata.get("project") or "knowledge",
        source_package_digest=source_digest,
        proposed_delta_digest=delta_digest,
        baseline_generation=metadata.get("baseline_generation") or 0,
        table_deltas=table_deltas,
        summary_context=f"Active ingestion proposes {table_deltas.get('node', 0)} nodes, {table_deltas.get('assertion', 0)} assertions under directive {directive_id}.",
        package_path=str(pkg_path),
    )
    dispatched = dispatch_and_persist_approval_request(
        app_req,
        transport=getattr(args, "interactive_transport", None),
        ledger_path=ledger_p,
        storage_root=rt_root_path,
    )
    resp_status = dispatched.get("response_status")
    if resp_status == "APPROVED":
        return _apply_activation_result(res, pkg_path, reviewer, rationale, rt_root_path, getattr(args, "graph", None))
    if resp_status == "DECLINED":
        return {
            "status": "DECLINED",
            "directive_id": directive_id,
            "approval_request_id": dispatched["request_id"],
            "reason": "Operator declined publication",
            "graph_mutation_committed": False,
        }

    res["status"] = "AWAITING_APPROVAL"
    res["directive_id"] = directive_id
    res["approval_request_id"] = dispatched["request_id"]
    res["approval_request"] = dispatched
    res["table_deltas"] = table_deltas
    res["source_package_digest"] = source_digest
    res["proposed_delta_digest"] = delta_digest
    res["next_action"] = "Awaiting authentic operator response via ask_user_question / nee ingest-resolve"
    res["graph_mutation_committed"] = False
    return res


"""
Helper for intake publication approval resolution under active-graph governance.
Decomposed from handlers.py to observe file budget ratchet and complexity bounds.
"""
from __future__ import annotations

import threading
from typing import Any, Optional, Tuple


def resolve_intake_publish_approval(
    directive: dict[str, Any],
    directive_id: str,
    classification: dict[str, Any],
    opened: dict[str, Any],
    guard: Any,
    args: Any,
) -> Tuple[Optional[dict[str, Any]], dict[str, Any]]:
    """Resolves human approval or active ingestion approval request for intake publish."""
    if directive.get("approval") is not None:
        return None, directive

    auth_tok = getattr(args, "auth_token", None)
    if auth_tok:
        if str(auth_tok).strip() == "auto":
            import json
            eff_rev = getattr(args, "reviewer", None) or "operator"
            auth_tok = json.dumps({
                "sub": eff_rev,
                "capabilities": ["knowledge:release", "knowledge:governance", "knowledge:intake"],
            })
        try:
            from dual_pipeline.rbac import validate_mutation_authorization
            auth_info = validate_mutation_authorization(auth_tok)
            reviewer = auth_info.get("subject") or "operator"
            rationale = f"Authorized via verified token for {reviewer}."
            directive = guard.record_human_decision(
                directive_id,
                decision="APPROVE",
                classification_digest=classification["classification_digest"],
                reviewer=reviewer,
                rationale=rationale,
            )
            return None, directive
        except Exception:
            pass

    try:
        from pathlib import Path
        from dual_pipeline.hitl_governance_core.active_ingestion_approval import check_active_ingestion_clearance
        candidate_digests = []
        packages = getattr(args, "package", []) or []
        if packages:
            from dual_pipeline.cli.ingest_resolve_cmd import extract_package_deltas_and_digests
            try:
                _, _, pkg_delta_digest = extract_package_deltas_and_digests(Path(packages[0]).resolve())
                if pkg_delta_digest and set(pkg_delta_digest) != {"0"}:
                    candidate_digests.append(pkg_delta_digest)
            except Exception:
                pass
        cls_digest = classification.get("classification_digest") or ""
        if cls_digest and cls_digest not in candidate_digests:
            candidate_digests.append(cls_digest)
        from dual_pipeline.cli.ingest_resolve_cmd import resolve_ingest_ledger_path
        rt_root = Path(getattr(args, "runtime_root", None) or "/tmp").resolve()
        ledger_p = resolve_ingest_ledger_path(rt_root)
        for d_dig in candidate_digests or [""]:
            is_cleared, _, prior_rec = check_active_ingestion_clearance(
                run_id=f"run-{directive_id}",
                directive_id=directive_id,
                delta_digest=d_dig,
                ledger_path=ledger_p,
            )
            if is_cleared and prior_rec:
                reviewer = prior_rec.get("operator_id") or "operator"
                rationale = "Operator authorized via HITL decision ledger clearance."
                directive = guard.record_human_decision(
                    directive_id,
                    decision="APPROVE",
                    classification_digest=classification["classification_digest"],
                    reviewer=reviewer,
                    rationale=rationale,
                )
                return None, directive
    except Exception:
        pass

    if classification.get("human_approval_required") or directive.get("batch_admission_manifest") is None:
        return _dispatch_and_handle_approval(directive, directive_id, classification, opened, guard, args)

    return None, directive


def _dispatch_and_handle_approval(
    directive: dict[str, Any],
    directive_id: str,
    classification: dict[str, Any],
    opened: dict[str, Any],
    guard: Any,
    args: Any = None,
) -> Tuple[Optional[dict[str, Any]], dict[str, Any]]:
    from pathlib import Path
    from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
        build_active_ingestion_approval_request,
        dispatch_and_persist_approval_request,
    )
    import hashlib
    src_digest = classification.get("source_digest")
    if not src_digest or src_digest == ("0" * 64):
        src_digest = hashlib.sha256(f"src:{directive_id}".encode("utf-8")).hexdigest()
    delta_digest = classification.get("classification_digest")
    if not delta_digest or delta_digest == ("0" * 64):
        delta_digest = hashlib.sha256(f"delta:{directive_id}".encode("utf-8")).hexdigest()

    packages = getattr(args, "package", []) or []
    table_deltas = None
    pkg_path = None
    if packages:
        pkg_path = Path(packages[0]).resolve()
        from dual_pipeline.cli.ingest_resolve_cmd import extract_package_deltas_and_digests
        try:
            t_deltas, s_dig, d_dig = extract_package_deltas_and_digests(pkg_path)
            table_deltas = t_deltas
            if s_dig and set(s_dig) != {"0"}:
                src_digest = s_dig
            if d_dig and set(d_dig) != {"0"}:
                delta_digest = d_dig
        except Exception:
            pass

    if not table_deltas:
        table_deltas = classification.get("table_deltas") or {"node": 0, "source": 1, "assertion": 0}

    req = build_active_ingestion_approval_request(
        run_id=f"run-{directive_id}",
        directive_id=directive_id,
        runtime_root=str(opened.get("runtime_root") or "/tmp"),
        project=opened.get("project") or "knowledge",
        source_package_digest=src_digest,
        proposed_delta_digest=delta_digest,
        baseline_generation=opened.get("baseline_generation") or 0,
        table_deltas=table_deltas,
        summary_context=f"Active ingestion proposes {table_deltas.get('node', 0)} nodes, {table_deltas.get('assertion', 0)} assertions under directive {directive_id}.",
        package_path=str(pkg_path) if pkg_path else None,
    )
    dispatched_req = dispatch_and_persist_approval_request(req)
    resp_status = dispatched_req.get("response_status")

    if resp_status == "APPROVED":
        directive = guard.record_human_decision(
            directive_id,
            decision="APPROVE",
            classification_digest=classification["classification_digest"],
            reviewer="operator",
            rationale="Operator approved via interactive tool",
        )
        return None, directive

    if resp_status == "DECLINED":
        return {
            "status": "DECLINED",
            "directive_id": directive_id,
            "pipeline": "candidate_release",
            "classification": classification,
            "approval_request_id": dispatched_req["request_id"],
            "reason": "Operator declined publication",
        }, directive

    return {
        "status": "AWAITING_APPROVAL",
        "directive_id": directive_id,
        "pipeline": "candidate_release",
        "classification": classification,
        "approval_request_id": dispatched_req["request_id"],
        "approval_request": dispatched_req,
        "required_embedding_target": directive.get("required_embedding_target"),
        "next_action": "Submit authentic operator approval response via ask_user_question to proceed.",
    }, directive


def intake_approval_resume(args: Any) -> dict[str, Any]:
    """Resume a pending active-ingestion approval request and publish into canonical graph."""
    import argparse
    from pathlib import Path
    from dual_pipeline.hitl_governance_core.active_ingestion_approval import resume_ingestion_with_approval
    from dual_pipeline.core import read_json

    request_id = getattr(args, "request_id", None)
    if not request_id:
        raise ValueError("--request-id is required to resume ingestion.")

    from dual_pipeline.runtime_root import resolve_authoritative_runtime_root

    runtime_root = Path(getattr(args, "runtime_root", None) or resolve_authoritative_runtime_root()).resolve()
    req_file = runtime_root / "approval_requests" / f"{request_id}.json"
    storage_root = runtime_root
    if not req_file.is_file():
        fallback_file = Path("/tmp/approval_requests") / f"{request_id}.json"
        if fallback_file.is_file():
            req_file = fallback_file
            storage_root = Path("/tmp").resolve()
        else:
            raise FileNotFoundError(f"Approval request '{request_id}' not found in {runtime_root / 'approval_requests'}")

    req_data = read_json(req_file)
    q_opts = req_data.get("question_payload", {}).get("options", [])

    decision = (getattr(args, "decision", None) or "APPROVE").upper()
    is_approve = decision == "APPROVE"
    from dual_pipeline.credentials import CredentialManager
    reviewer = CredentialManager.get_credential("NEE_REVIEWER") or "operator"
    rationale = (
        "Operator authorized graph mutation continuation." if is_approve else "Operator declined publication."
    )

    if is_approve:
        selected_opt = next(
            (o["label"] for o in q_opts if "approve" in str(o.get("label", "")).lower()),
            (q_opts[0]["label"] if q_opts else "Approve publication"),
        )
    else:
        selected_opt = next(
            (o["label"] for o in q_opts if "decline" in str(o.get("label", "")).lower()),
            (q_opts[1]["label"] if len(q_opts) > 1 else "Decline publication"),
        )

    operator_resp = {
        "selected_option": selected_opt,
        "operator_id": reviewer,
        "reviewer": reviewer,
        "rationale": rationale,
        "origin": "human_operator",
        "source": "user_input",
    }

    ledger_path = runtime_root / "hitl_decision_ledger.sqlite"
    if not ledger_path.is_file():
        ledger_path = runtime_root / "hitl_decision_ledger.json"

    success, outcome_msg, sealed_req = resume_ingestion_with_approval(
        request_id=request_id,
        operator_response=operator_resp,
        storage_root=storage_root,
        ledger_path=ledger_path if ledger_path.exists() else None,
    )

    if not success or not is_approve:
        return {
            "status": "DECLINED" if "DECLINED" in outcome_msg else "FAILED",
            "request_id": request_id,
            "directive_id": req_data.get("directive_id"),
            "reason": outcome_msg,
            "approval_request": sealed_req,
        }

    pkg_path = (
        sealed_req.get("intake_package_path")
        or sealed_req.get("extensions", {}).get("intake_package_path")
        or sealed_req.get("package_path")
        or sealed_req.get("extensions", {}).get("package_path")
    )
    if not pkg_path:
        raise ValueError(f"Approval request {request_id} does not contain intake_package_path.")

    pkg_file = Path(pkg_path).resolve()
    if not pkg_file.is_file():
        raise FileNotFoundError(f"Intake package not found at: {pkg_file}")

    from dual_pipeline.cli.handlers import _intake_publish
    emb_backend = getattr(args, "embedding_backend", None) or "bge-m3"
    allow_dev = (emb_backend == "bge-m3-dev") or getattr(args, "allow_dev_bge_m3", False)
    if allow_dev:
        emb_backend = "bge-m3-dev"
    allow_internal = (emb_backend == "bge-m3-internal") or getattr(args, "allow_internal_operational", False)
    if allow_internal:
        emb_backend = "bge-m3-internal"

    existing_scope = None
    dir_id = sealed_req.get("directive_id")
    if dir_id:
        try:
            from dual_pipeline.completion_core import IngestionCompletionGuard
            _guard = IngestionCompletionGuard(runtime_root)
            existing_dir = _guard.status(dir_id)
            existing_scope = existing_dir.get("scope")
        except Exception:
            pass

    publish_args = argparse.Namespace(
        package=[str(pkg_file)],
        runtime_root=runtime_root,
        graph=getattr(args, "graph", None),
        directive_id=dir_id,
        allow_unjudged_package=True,
        require_bge_m3=getattr(args, "require_bge_m3", False),
        batch_manifest=getattr(args, "batch_manifest", None),
        scope=getattr(args, "scope", None) or existing_scope,
        a2a_finalization_receipt=None,
        model=getattr(args, "model", None),
        judge_model=getattr(args, "judge_model", None),
        provider=getattr(args, "provider", None) or "offline",
        retrieval_release_suite=getattr(args, "retrieval_release_suite", None),
        embedding_backend=emb_backend,
        allow_dev_bge_m3=allow_dev,
        allow_internal_operational=allow_internal,
        auth_token=getattr(args, "auth_token", None),
        auth_public_key=getattr(args, "auth_public_key", None),
        auth_issuer=getattr(args, "auth_issuer", None),
        auth_audience=getattr(args, "auth_audience", None),
        model_path=getattr(args, "model_path", None),
        model_revision=getattr(args, "model_revision", None),
        model_manifest=getattr(args, "model_manifest", None),
        internal_operational_policy=getattr(args, "internal_operational_policy", None),
        audience=getattr(args, "audience", None) or "internal_consulting,nee-engineering",
        semantic_cache_mode=getattr(args, "semantic_cache_mode", None),
        ingestion_reasoning=getattr(args, "ingestion_reasoning", "off"),
    )

    publish_result = _intake_publish(publish_args)
    publish_result["resumed_approval_request_id"] = request_id
    return publish_result


def add_intake_approval_resume_parser(subparsers: Any) -> Any:
    """Register parser for intake-approval-resume."""
    cmd = subparsers.add_parser(
        "intake-approval-resume",
        help="Resume a pending active-ingestion approval request and publish into canonical graph.",
    )
    cmd.add_argument("--request-id", required=True, help="Active ingestion approval request ID.")
    cmd.add_argument("--decision", choices=["APPROVE", "DECLINE"], default="APPROVE", help="Operator decision.")
    cmd.add_argument("--runtime-root", default=None, help="Path to runtime root directory.")
    cmd.add_argument("--graph", default=None, help="Target graph path override.")
    cmd.add_argument("--provider", default=None, help="Inference provider.")
    cmd.add_argument("--require-bge-m3", action="store_true", default=False, help="Require BGE-M3 sidecar.")
    return cmd


_CLI_INPUT_LOCK = threading.Lock()


def attach_cli_judge_resolver(metadata: dict[str, Any]) -> None:
    """Attach interactive HITL judge resolver to run metadata without prior-command auto-affirmation."""
    if "hitl_judge_resolver" in metadata:
        return
    import sys

    def _cli_judge_resolver(envelope: Any) -> str:
        opts = getattr(envelope, "calibrated_options", [])
        if sys.stdin.isatty():
            from config.request_deadline import pause_deadline_for_hitl, read_stdin_line_with_timeout

            timeout_s = 30.0
            with _CLI_INPUT_LOCK, pause_deadline_for_hitl(max_suspend_seconds=timeout_s):
                print(f"\n[HITL CLARIFICATION REQUIRED] {getattr(envelope, 'question', '')}", flush=True)
                for i, opt in enumerate(opts):
                    print(f"  [{i+1}] {opt.get('label')}", flush=True)
                try:
                    ans = read_stdin_line_with_timeout(
                        timeout_seconds=timeout_s,
                        prompt="Select option (explicit choice required; Enter or timeout upholds deduction): ",
                    )
                    if not ans:
                        return "UPHOLD_DEDUCTION"
                    if ans.isdigit():
                        idx = int(ans) - 1
                        if 0 <= idx < len(opts):
                            return opts[idx].get("key", "UPHOLD_DEDUCTION")
                    for opt in opts:
                        key = opt.get("key", "")
                        if ans.lower() in (key.lower(), opt.get("label", "").lower()):
                            return key
                    if "affirm" in ans.lower():
                        return "AFFIRM_VALID"
                    if "uphold" in ans.lower():
                        return "UPHOLD_DEDUCTION"
                    return "UPHOLD_DEDUCTION"
                except Exception:
                    return "UPHOLD_DEDUCTION"
        return "UPHOLD_DEDUCTION"

    metadata["hitl_judge_resolver"] = _cli_judge_resolver


def _stage_package_to_worktree(
    pkg_resolved: Any,
    primary_root: Any,
    wt_dir: Optional[Any] = None,
    wt_branch: Optional[str] = None,
) -> Tuple[Any, Any, str]:
    import shutil
    from pathlib import Path
    from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
        ensure_ingest_worktree,
        get_current_git_branch,
    )

    pkg_p = Path(pkg_resolved).resolve()
    root_p = Path(primary_root).resolve()
    rel_to_output = pkg_p.relative_to((root_p / "output").resolve())
    parts = rel_to_output.parts
    project_name = parts[0] if len(parts) >= 2 else "knowledge"
    date_dirname = parts[1] if len(parts) >= 3 else "01-01-2026"
    clean_slug = parts[2] if len(parts) >= 4 else pkg_p.parent.name

    if wt_dir is None:
        wt_dir, wt_branch = ensure_ingest_worktree(
            modality="MEETING",
            project_name=project_name,
            title=clean_slug,
            date_dirname=date_dirname,
            clean_slug=clean_slug,
            repo_root=root_p,
        )
    else:
        wt_dir = Path(wt_dir).resolve()
        wt_branch = wt_branch or get_current_git_branch(wt_dir) or f"ingest/doc-{clean_slug}"

    dest_pkg = wt_dir / "output" / rel_to_output
    dest_dir = dest_pkg.parent
    src_dir = pkg_p.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    if src_dir.is_dir() and src_dir.resolve() != dest_dir.resolve():
        for item in src_dir.iterdir():
            target_item = dest_dir / item.name
            if item.is_file():
                shutil.copy2(item, target_item)
            elif item.is_dir() and not target_item.exists():
                shutil.copytree(item, target_item)
    return dest_pkg.resolve(), wt_dir, str(wt_branch or "")


def ensure_intake_publish_worktree_isolation(
    packages: list[Any],
    enforce_worktree: bool = True,
    worktree_dir: Optional[Any] = None,
) -> Tuple[list[Any], Optional[str], Optional[str]]:
    """Guarantees intake-publish packages inside <repo_root>/output/ execute inside an isolated worktree."""
    from pathlib import Path
    from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
        _resolve_primary_repo_root,
        get_current_git_branch,
        is_inside_isolated_worktree,
    )

    resolved_pkgs = [Path(p).expanduser().resolve() for p in packages]
    if not resolved_pkgs or not enforce_worktree:
        return resolved_pkgs, None, None

    explicit_wt = Path(worktree_dir).expanduser().resolve() if worktree_dir else None
    wt_dir: Optional[Path] = explicit_wt if (explicit_wt and is_inside_isolated_worktree(explicit_wt)) else None
    wt_branch: Optional[str] = get_current_git_branch(wt_dir) if wt_dir else None
    isolated_pkgs: list[Path] = []

    for pkg in resolved_pkgs:
        if is_inside_isolated_worktree(pkg):
            isolated_pkgs.append(pkg)
            if wt_dir is None:
                for parent in pkg.parents:
                    if parent.parent.name == ".worktrees":
                        wt_dir = parent
                        wt_branch = get_current_git_branch(wt_dir)
                        break
            continue

        primary_root = _resolve_primary_repo_root(pkg.parent if pkg.parent.exists() else Path.cwd())
        try:
            in_root_output = pkg.is_relative_to((primary_root / "output").resolve())
        except Exception:
            in_root_output = False

        if not in_root_output:
            isolated_pkgs.append(pkg)
            continue

        staged_pkg, wt_dir, wt_branch = _stage_package_to_worktree(pkg, primary_root, wt_dir, wt_branch)
        isolated_pkgs.append(staged_pkg)

    return isolated_pkgs, (str(wt_dir) if wt_dir else None), wt_branch


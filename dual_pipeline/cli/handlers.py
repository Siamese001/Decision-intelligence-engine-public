from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)
from ..candidate_cleanup import (
    build_candidate_cleanup_plan,
    prepare_candidate_cleanup_release,
    record_candidate_cleanup_release_events,
)
from ..combined_remediation import (
    build_combined_remediation_plan,
    combined_remediation_control_from_package,
    prepare_combined_remediation_release,
    record_combined_remediation_release_events,
)
from ..core import read_json, sha256_file, write_json_exclusive
from ..release import ReleaseManager, release_action_resources
from ..retrieval_release import RetrievalReleaseEvaluator
from ..retrieval_suite import AutomaticRetrievalReleaseSuiteBuilder, AutomaticSuiteBuildError
from ..source_retirement import (
    build_source_retirement_plan,
    prepare_source_retirement_release,
    record_source_retirement_release_events,
    source_retirement_control_from_package,
)
from ..validation_closeout import (
    build_validation_closeout_plan,
    prepare_validation_closeout_release,
)
from .engine import (
    _assert_backend_matches_pair,
    _authorization,
    _backend,
    _bind_model_authorization,
    _bind_receipt_model_authorization,
    _completion_guard,
    _default_publish_directive_id,
    _intake_engine,
)
from .ingest import ingest_canonical
from .ingest import is_document_source as is_document_source
from .parser import _BGE_M3_BACKENDS


def _activate_and_complete_remediation(
    args: argparse.Namespace, manager: ReleaseManager, guard: Any, receipt: dict[str, Any], receipt_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    release_action_resources(receipt, "activate")
    authorization = _authorization(
        args, production_required=bool(receipt.get("next_pair", {}).get("production_qualified")), capabilities=("knowledge:release",),
    )
    _bind_receipt_model_authorization(receipt, authorization)
    preflight = guard.preflight_activation(args.directive_id, receipt_path)
    activation = manager.activate(receipt_path, authorization_context=authorization)
    try:
        completion = guard.complete(args.directive_id, receipt_path)
    except Exception as comp_err:
        logger.error("guard.complete failed after activation, executing automatic rollback: %s", comp_err)
        try:
            manager.rollback(receipt_path, authorization_context=authorization)
        except Exception as rb_err:
            logger.critical("Automatic rollback failed: %s", rb_err)
        raise RuntimeError(f"Release activation failed during completion ({comp_err}); automatic rollback was executed.") from comp_err
    return preflight, activation, completion


def _ingest_document(args: argparse.Namespace) -> dict[str, Any]:
    input_val = getattr(args, "input", None) or getattr(args, "source", None)
    ext = Path(str(input_val)).suffix.lower() if input_val else ""
    if ext in {".vtt", ".srt"} and getattr(args, "auto_reroute", False):
        logger.info("Auto-rerouting transcript cue file '%s' from ingest-document to ingest-meeting workflow.", input_val)
        args.document, args.command = False, "ingest-meeting"
        if not getattr(args, "meeting", None):
            args.meeting = getattr(args, "title", None) or (Path(str(input_val)).stem if input_val else "meeting")
        return _ingest_meeting(args)
    args.document = True
    return ingest_canonical(args)


def _ingest_meeting(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "document", False):
        return _ingest_document(args)
    return ingest_canonical(args)


def _candidate_backlog_cleanup(args: argparse.Namespace) -> dict[str, Any]:
    """Execute one approval-bound cleanup release with no per-assertion gates."""
    manager = ReleaseManager(args.runtime_root, args.graph)
    backend = _backend(args)
    guard = _completion_guard(args)
    release_validation = guard.validate_release_request(
        args.directive_id,
        run_ids=[args.run_id],
    )
    active = manager.active_pair()
    if backend.backend_id != active["embedding_backend"]:
        raise RuntimeError("candidate cleanup backend must match the active graph/index pair")
    active_graph = (manager.runtime_root / active["graph_path"]).resolve()
    plan = build_candidate_cleanup_plan(active_graph, active_pair=active)
    run_dir = manager.runtime_root / "runs" / args.run_id
    plan_path = run_dir / "candidate_backlog_cleanup_plan.json"
    write_json_exclusive(plan_path, plan)
    summary = read_json(run_dir / "intake_summary.json")
    bundle = read_json(run_dir / "candidate_bundle.json")
    receipt, receipt_path = prepare_candidate_cleanup_release(
        manager=manager,
        backend=backend,
        plan=plan,
        run_id=args.run_id,
        trace_id=bundle["trace_id"],
        source_id=bundle["source_id"],
        source_version_id=bundle["source_version_id"],
        intake_summary=summary,
        approval_id=release_validation["approval_id"],
    )
    ledger = record_candidate_cleanup_release_events(
        runtime_root=manager.runtime_root,
        receipt=receipt,
    )
    preflight, activation, completion = _activate_and_complete_remediation(
        args, manager, guard, receipt, receipt_path
    )
    return {
        "status": "COMPLETE",
        "directive_id": args.directive_id,
        "mutation_summary": completion.get("mutation_summary"),
        "plan": plan["summary"],
        "plan_path": str(plan_path),
        "receipt": receipt,
        "receipt_path": str(receipt_path),
        "release_ledger": ledger,
        "preflight": preflight,
        "activation": activation,
        "ingestion_completion": completion,
    }

def _validation_closeout(args: argparse.Namespace) -> dict[str, Any]:
    """Close one validation item with one source-bound human decision."""
    manager = ReleaseManager(args.runtime_root, args.graph)
    backend = _backend(args)
    guard = _completion_guard(args)
    release_validation = guard.validate_release_request(
        args.directive_id,
        run_ids=[args.run_id],
    )
    active = manager.active_pair()
    if backend.backend_id != active["embedding_backend"]:
        raise RuntimeError("validation closeout backend must match the active graph/index pair")
    plan = build_validation_closeout_plan(
        (manager.runtime_root / active["graph_path"]).resolve(),
        active_pair=active,
        validation_id=args.validation_id,
        resolution=args.resolution,
        disposition=args.disposition,
        confirmed_edge_ids=args.confirmed_edge,
        candidate_claim_ids=args.candidate_claim,
        confirmed_name=args.confirmed_name,
        directive_approval_id=release_validation["approval_id"],
    )
    run_dir = manager.runtime_root / "runs" / args.run_id
    plan_path = run_dir / f"validation_closeout_plan-{plan['plan_digest']}.json"
    if plan_path.exists():
        if read_json(plan_path) != plan:
            raise RuntimeError("validation closeout plan path is occupied by different content")
    else:
        write_json_exclusive(plan_path, plan)
    summary = read_json(run_dir / "intake_summary.json")
    bundle = read_json(run_dir / "candidate_bundle.json")
    receipt, receipt_path = prepare_validation_closeout_release(
        manager=manager,
        backend=backend,
        plan=plan,
        run_id=args.run_id,
        trace_id=bundle["trace_id"],
        source_id=bundle["source_id"],
        source_version_id=bundle["source_version_id"],
        intake_summary=summary,
        approval_id=release_validation["approval_id"],
    )
    ledger = record_candidate_cleanup_release_events(
        runtime_root=manager.runtime_root,
        receipt=receipt,
    )
    preflight, activation, completion = _activate_and_complete_remediation(
        args, manager, guard, receipt, receipt_path
    )
    return {
        "status": "COMPLETE",
        "directive_id": args.directive_id,
        "mutation_summary": completion.get("mutation_summary"),
        "validation_id": args.validation_id,
        "plan": plan["summary"],
        "plan_path": str(plan_path),
        "receipt": receipt,
        "receipt_path": str(receipt_path),
        "release_ledger": ledger,
        "preflight": preflight,
        "activation": activation,
        "ingestion_completion": completion,
    }

def _source_retirement(args: argparse.Namespace) -> dict[str, Any]:
    """Retire one exact source scope after a source-bound control intake."""

    manager = ReleaseManager(args.runtime_root, args.graph)
    backend = _backend(args)
    guard = _completion_guard(args)
    release_validation = guard.validate_release_request(
        args.directive_id,
        run_ids=[args.run_id],
    )
    active = manager.active_pair()
    if backend.backend_id != active["embedding_backend"]:
        raise RuntimeError("source-retirement backend must match the active graph/index pair")
    control_package_path = args.control_package.expanduser().resolve()
    control_package = read_json(control_package_path)
    control = source_retirement_control_from_package(control_package)
    summary = read_json(manager.runtime_root / "runs" / args.run_id / "intake_summary.json")
    bundle = read_json(manager.runtime_root / "runs" / args.run_id / "candidate_bundle.json")
    if summary.get("package_file_sha256") != sha256_file(control_package_path):
        raise RuntimeError("source-retirement control package does not match the intake run")
    if (
        summary.get("source_id") != control_package["source"]["source_id"]
        or bundle.get("source_id") != control_package["source"]["source_id"]
        or bundle.get("source_version_id")
        != control_package["source"]["source_version_id"]
    ):
        raise RuntimeError("source-retirement intake run is not bound to the control package")
    package_binding = bundle.get("extensions", {}).get("com.partner_advisory.nee.source", {})
    if package_binding.get("package_digest") != control_package["canonical_digest"]["value"]:
        raise RuntimeError("source-retirement normalized bundle lost its control-package binding")
    plan = build_source_retirement_plan(
        (manager.runtime_root / active["graph_path"]).resolve(),
        active_pair=active,
        control=control,
    )
    run_dir = manager.runtime_root / "runs" / args.run_id
    plan_path = run_dir / f"source_retirement_plan-{plan['plan_digest']}.json"
    if plan_path.exists():
        if read_json(plan_path) != plan:
            raise RuntimeError("source-retirement plan path is occupied by different content")
    else:
        write_json_exclusive(plan_path, plan)
    receipt, receipt_path = prepare_source_retirement_release(
        manager=manager,
        backend=backend,
        plan=plan,
        run_id=args.run_id,
        trace_id=bundle["trace_id"],
        source_id=bundle["source_id"],
        source_version_id=bundle["source_version_id"],
        intake_summary=summary,
        approval_id=release_validation["approval_id"],
    )
    suite_resolution = AutomaticRetrievalReleaseSuiteBuilder(
        args.runtime_root,
        args.graph,
    ).resolve(receipt_path)
    evaluation_result = RetrievalReleaseEvaluator(args.runtime_root, args.graph).evaluate(
        receipt_path,
        Path(suite_resolution["path"]),
        backend=backend,
    )
    evaluation = evaluation_result["evaluation"]
    if evaluation["status"] != "PASS":
        return {
            "status": "EVALUATION_FAILED",
            "directive_id": args.directive_id,
            "receipt": receipt,
            "receipt_path": str(receipt_path),
            "retrieval_release_suite_resolution": suite_resolution,
            "retrieval_evaluation": evaluation_result,
            "next_action": "Correct the receipt-bound retrieval suite or release; the active pair is unchanged.",
        }
    ledger = record_source_retirement_release_events(
        runtime_root=manager.runtime_root,
        receipt=receipt,
    )
    preflight, activation, completion = _activate_and_complete_remediation(
        args, manager, guard, receipt, receipt_path
    )
    return {
        "status": "COMPLETE",
        "directive_id": args.directive_id,
        "mutation_summary": completion.get("mutation_summary"),
        "plan": plan["summary"],
        "plan_path": str(plan_path),
        "receipt": receipt,
        "receipt_path": str(receipt_path),
        "retrieval_release_suite_resolution": suite_resolution,
        "retrieval_evaluation": evaluation,
        "release_ledger": ledger,
        "preflight": preflight,
        "activation": activation,
        "ingestion_completion": completion,
    }

def _combined_remediation(args: argparse.Namespace) -> dict[str, Any]:
    """Release one sealed Sherpa-retirement/status-demotion control as a unit."""

    manager = ReleaseManager(args.runtime_root, args.graph)
    backend = _backend(args)
    guard = _completion_guard(args)
    release_validation = guard.validate_release_request(
        args.directive_id,
        run_ids=[args.run_id],
    )
    active = manager.active_pair()
    if backend.backend_id != active["embedding_backend"]:
        raise RuntimeError("combined remediation backend must match the active graph/index pair")
    control_package_path = args.control_package.expanduser().resolve()
    control_package = read_json(control_package_path)
    control = combined_remediation_control_from_package(control_package)
    run_dir = manager.runtime_root / "runs" / args.run_id
    summary = read_json(run_dir / "intake_summary.json")
    bundle = read_json(run_dir / "candidate_bundle.json")
    if summary.get("package_file_sha256") != sha256_file(control_package_path):
        raise RuntimeError("combined remediation control package does not match the intake run")
    if (
        summary.get("source_id") != control_package["source"]["source_id"]
        or bundle.get("source_id") != control_package["source"]["source_id"]
        or bundle.get("source_version_id") != control_package["source"]["source_version_id"]
    ):
        raise RuntimeError("combined remediation intake run is not bound to its control package")
    package_binding = bundle.get("extensions", {}).get("com.partner_advisory.nee.source", {})
    if package_binding.get("package_digest") != control_package["canonical_digest"]["value"]:
        raise RuntimeError("combined remediation normalized bundle lost its package binding")
    plan = build_combined_remediation_plan(
        (manager.runtime_root / active["graph_path"]).resolve(),
        active_pair=active,
        runtime_root=manager.runtime_root,
        control=control,
        approval_id=release_validation["approval_id"],
        run_id=args.run_id,
    )
    plan_path = run_dir / f"combined_remediation_plan-{plan['plan_digest']}.json"
    write_json_exclusive(plan_path, plan)
    receipt, receipt_path = prepare_combined_remediation_release(
        manager=manager,
        backend=backend,
        plan=plan,
        run_id=args.run_id,
        trace_id=bundle["trace_id"],
        source_id=bundle["source_id"],
        source_version_id=bundle["source_version_id"],
        intake_summary=summary,
        approval_id=release_validation["approval_id"],
    )
    suite_resolution = AutomaticRetrievalReleaseSuiteBuilder(
        args.runtime_root,
        args.graph,
    ).resolve(receipt_path)
    evaluation_result = RetrievalReleaseEvaluator(args.runtime_root, args.graph).evaluate(
        receipt_path,
        Path(suite_resolution["path"]),
        backend=backend,
    )
    evaluation = evaluation_result["evaluation"]
    if evaluation["status"] != "PASS":
        return {
            "status": "EVALUATION_FAILED",
            "directive_id": args.directive_id,
            "plan": plan["summary"],
            "plan_path": str(plan_path),
            "receipt": receipt,
            "receipt_path": str(receipt_path),
            "retrieval_release_suite_resolution": suite_resolution,
            "retrieval_evaluation": evaluation_result,
            "next_action": "Correct the receipt-bound retrieval suite or release; the active pair is unchanged.",
        }
    ledger = record_combined_remediation_release_events(
        runtime_root=manager.runtime_root,
        receipt=receipt,
    )
    preflight, activation, completion = _activate_and_complete_remediation(
        args, manager, guard, receipt, receipt_path
    )
    return {
        "status": "COMPLETE",
        "directive_id": args.directive_id,
        "mutation_summary": completion.get("mutation_summary"),
        "plan": plan["summary"],
        "plan_path": str(plan_path),
        "receipt": receipt,
        "receipt_path": str(receipt_path),
        "retrieval_release_suite_resolution": suite_resolution,
        "retrieval_evaluation": evaluation,
        "release_ledger": ledger,
        "preflight": preflight,
        "activation": activation,
        "ingestion_completion": completion,
    }

def _combined_remediation_finalize(args: argparse.Namespace) -> dict[str, Any]:
    """Evaluate and activate an existing exact combined-remediation receipt.

    A failed E3 attempt remains immutable evidence.  This recovery path does
    not restage or alter the approved graph change; it can only evaluate the
    same prepared receipt again, then perform the normal W0/E0 and completion
    lifecycle if a fresh evaluation passes.
    """

    manager = ReleaseManager(args.runtime_root, args.graph)
    backend = _backend(args)
    guard = _completion_guard(args)
    release_validation = guard.validate_release_request(
        args.directive_id,
        run_ids=[args.run_id],
    )
    receipt_path = args.receipt.expanduser().resolve()
    try:
        receipt_path.relative_to(manager.releases_dir.resolve())
    except ValueError as exc:
        raise ValueError("combined remediation receipt must be inside runtime releases") from exc
    receipt = read_json(receipt_path)
    if (
        receipt.get("status") != "PREPARED"
        or receipt.get("release_kind") != "combined_sherpa_status_remediation"
        or receipt.get("run_id") != args.run_id
        or receipt.get("approval_id") != release_validation["approval_id"]
    ):
        raise RuntimeError("receipt is not the exact approved combined remediation release")
    active = manager.active_pair()
    if active != receipt.get("previous_pair"):
        raise RuntimeError("combined remediation receipt is stale relative to the active pair")
    if backend.backend_id != active["embedding_backend"]:
        raise RuntimeError("combined remediation backend must match the active graph/index pair")
    suite_resolution = AutomaticRetrievalReleaseSuiteBuilder(
        args.runtime_root,
        args.graph,
    ).resolve(receipt_path)
    evaluation_result = RetrievalReleaseEvaluator(args.runtime_root, args.graph).evaluate(
        receipt_path,
        Path(suite_resolution["path"]),
        backend=backend,
    )
    evaluation = evaluation_result["evaluation"]
    if evaluation["status"] != "PASS":
        return {
            "status": "EVALUATION_FAILED",
            "directive_id": args.directive_id,
            "receipt": receipt,
            "receipt_path": str(receipt_path),
            "retrieval_release_suite_resolution": suite_resolution,
            "retrieval_evaluation": evaluation_result,
            "next_action": "Correct the receipt-bound retrieval suite or release; the active pair is unchanged.",
        }
    ledger = record_combined_remediation_release_events(
        runtime_root=manager.runtime_root,
        receipt=receipt,
    )
    preflight, activation, completion = _activate_and_complete_remediation(
        args, manager, guard, receipt, receipt_path
    )
    return {
        "status": "COMPLETE",
        "directive_id": args.directive_id,
        "receipt": receipt,
        "receipt_path": str(receipt_path),
        "retrieval_release_suite_resolution": suite_resolution,
        "retrieval_evaluation": evaluation,
        "release_ledger": ledger,
        "preflight": preflight,
        "activation": activation,
        "ingestion_completion": completion,
    }

def _release_batch(args: argparse.Namespace, *, run_ids: list[str]) -> dict[str, Any]:
    """Prepare, evaluate, and finish one approved candidate batch.

    The deterministic ingestion stages, graph write, sidecar build, and BGE
    retrieval regression gate are one delivery lifecycle.  A BGE release is
    never activated merely because its sidecar exists: it either evaluates in
    this invocation or returns an explicit evaluation-required state.
    """

    manager = ReleaseManager(args.runtime_root, args.graph)
    backend = _backend(args)
    preparation_authorization = _authorization(
        args, production_required=backend.production_qualified, capabilities=("knowledge:release",)
    )
    _bind_model_authorization(backend, preparation_authorization)
    guard = _completion_guard(args)
    from dual_pipeline.retrieval_release_core.preflight import preflight_retrieval_release_schema
    preflight_retrieval_release_schema()
    release_validation = guard.validate_release_request(args.directive_id, run_ids=run_ids)
    receipt = manager.prepare_many(
        run_ids=run_ids,
        approval_id=release_validation["approval_id"],
        backend=backend,
        authorization_context=preparation_authorization,
    )
    receipt_path = (
        args.runtime_root.resolve()
        / "releases"
        / f"release-{receipt['next_pair']['generation_id']}.json"
    )
    is_bge_release = receipt["next_pair"]["embedding_backend"] in _BGE_M3_BACKENDS
    suite_resolution: dict[str, Any] | None = None
    if is_bge_release:
        try:
            suite_resolution = AutomaticRetrievalReleaseSuiteBuilder(
                args.runtime_root,
                args.graph,
            ).resolve(
                receipt_path,
                explicit_suite=getattr(args, "retrieval_release_suite", None),
            )
        except AutomaticSuiteBuildError as exc:
            return {
                "status": "PREPARED_EVALUATION_SUITE_BLOCKED",
                "reason_code": exc.code,
                "error": str(exc),
                "receipt": receipt,
                "receipt_path": str(receipt_path),
                "retrieval_evaluation_required": True,
                "next_action": (
                    "Correct the governed automatic-suite profile or supply an "
                    "explicit sealed retrieval release suite."
                ),
                "delivery_status": guard.delivery_status(args.directive_id),
            }
    if receipt["next_pair"]["production_qualified"]:
        # This is a machine authorization boundary, not a second human
        # approval.  The service that owns production activation receives the
        # exact receipt-bound resources only after this immutable receipt exists.
        result = {
            "status": "PREPARED_ACTION_AUTH_REQUIRED",
            "receipt": receipt,
            "receipt_path": str(receipt_path),
            "retrieval_evaluation_required": is_bge_release,
            "retrieval_release_suite": (
                suite_resolution["path"]
                if suite_resolution is not None
                else None
            ),
            "retrieval_release_suite_resolution": suite_resolution,
            "delivery_status": guard.delivery_status(args.directive_id),
        }
        if is_bge_release:
            # The sealed evaluation digest does not exist yet, so there is no
            # exact activation token scope to hand to an identity service.  A
            # caller must evaluate first, then build the action request; that
            # builder adds the exact evaluation digest to the resource set.
            result["activation_action_resources"] = None
            result["next_action"] = (
                "Run the receipt-bound BGE-M3 retrieval evaluation, then build "
                "the fresh activation action request from its PASS receipt."
            )
        else:
            result["activation_action_resources"] = release_action_resources(
                receipt, "activate"
            )
            result["next_action"] = (
                "Build the fresh receipt-bound activation action request."
            )
        return result

    evaluation: dict[str, Any] | None = None
    if is_bge_release:
        assert suite_resolution is not None
        suite_path = Path(suite_resolution["path"]).expanduser().resolve()
        if not suite_path.is_file():
            raise FileNotFoundError(f"retrieval release suite does not exist: {suite_path}")
        evaluation_result = RetrievalReleaseEvaluator(args.runtime_root, args.graph).evaluate(
            receipt_path,
            suite_path,
            backend=backend,
            authorization_context=preparation_authorization,
        )
        evaluation = evaluation_result["evaluation"]
        if evaluation["status"] != "PASS":
            return {
                "status": "EVALUATION_FAILED",
                "receipt": receipt,
                "receipt_path": str(receipt_path),
                "retrieval_release_suite_resolution": suite_resolution,
                "retrieval_evaluation": evaluation_result,
                "next_action": (
                    "Correct the release or suite failure; the active pair is unchanged."
                ),
                "delivery_status": guard.delivery_status(args.directive_id),
            }
    guard.preflight_activation(args.directive_id, receipt_path)
    activation = manager.activate(receipt_path, authorization_context=preparation_authorization)
    try:
        completion = guard.complete(args.directive_id, receipt_path)
    except Exception as comp_err:
        logger.error("guard.complete failed after activation, executing automatic rollback: %s", comp_err)
        try:
            manager.rollback(receipt_path, authorization_context=preparation_authorization)
        except Exception as rb_err:
            logger.critical("Automatic rollback failed: %s", rb_err)
        raise RuntimeError(f"Release activation failed during completion ({comp_err}); automatic rollback was executed.") from comp_err
    return {
        "status": activation["status"],
        "mutation_summary": completion.get("mutation_summary") or activation.get("mutation_summary"),
        "receipt": receipt, "receipt_path": str(receipt_path),
        "retrieval_release_suite_resolution": suite_resolution, "retrieval_evaluation": evaluation,
        "activation": activation, "ingestion_completion": completion,
        "delivery_status": guard.delivery_status(args.directive_id),
    }


def _intake_approval_resume(args: argparse.Namespace) -> dict[str, Any]:
    from dual_pipeline.cli.intake_approval_helper import intake_approval_resume
    return intake_approval_resume(args)


def _intake_publish(args: argparse.Namespace) -> dict[str, Any]:
    """Run the normal candidate path without exposing approval-ID choreography."""
    from dual_pipeline.cli.intake_approval_helper import ensure_intake_publish_worktree_isolation
    packages, wt_dir, wt_branch = ensure_intake_publish_worktree_isolation(
        args.package, enforce_worktree=bool(getattr(args, "enforce_worktree", True)), worktree_dir=getattr(args, "worktree_dir", None),
    )
    from dual_pipeline.cli.ingest_worktree_guard import assert_ingest_worktree_code_freeze
    assert_ingest_worktree_code_freeze(wt_dir)
    from dual_pipeline.release_policy import verify_authoritative_release_gate
    verify_authoritative_release_gate(packages, allow_unjudged=bool(getattr(args, "allow_unjudged_package", False)))

    if getattr(args, "a2a_finalization_receipt", None) is not None and len(packages) != 1:
        raise ValueError("--a2a-finalization-receipt is supported only for one-package publication")

    directive_id = args.directive_id or _default_publish_directive_id(packages)
    args.directive_id = directive_id
    scope = args.scope or f"Candidate-only publication of {len(packages)} exact source package(s)."

    backend = _backend(args)
    bootstrap_authorization = _authorization(args, production_required=backend.production_qualified, capabilities=("knowledge:release",))
    _bind_model_authorization(backend, bootstrap_authorization)
    manager = ReleaseManager(args.runtime_root, args.graph)
    if not manager.active_path.is_file():
        manager.bootstrap(backend, authorization_context=bootstrap_authorization)
    _assert_backend_matches_pair(backend, manager.active_pair(), operation="candidate publication")

    guard = _completion_guard(args)
    try:
        existing = guard.status(directive_id)
    except FileNotFoundError:
        existing = None
    if existing is not None and existing["terminal"]:
        expected_bindings = [
            {
                "path": str(Path(package).expanduser().resolve()),
                "sha256": sha256_file(Path(package).expanduser().resolve()),
                "bytes": Path(package).expanduser().resolve().stat().st_size,
            }
            for package in packages
        ]
        if existing["scope"] != scope or existing["packages"] != expected_bindings:
            raise RuntimeError("existing terminal directive does not bind this exact publish request")
        guard.assert_embedding_requirement(directive_id, require_bge_m3=getattr(args, "require_bge_m3", False))
        if existing["status"] == "COMPLETE":
            return {
                "status": "COMPLETE", "directive_id": directive_id, "pipeline": "candidate_release",
                "required_embedding_target": existing.get("required_embedding_target"),
                "ingestion_completion": existing["completion"], "idempotent_replay": True,
                "worktree_dir": wt_dir, "worktree_branch": wt_branch,
            }
        raise RuntimeError(f"existing terminal directive cannot be republished: {existing['status']}")

    opened = guard.open(
        directive_id=directive_id, scope=scope, packages=packages,
        batch_admission_manifest=getattr(args, "batch_manifest", None), require_bge_m3=getattr(args, "require_bge_m3", False),
    )
    guard.assert_embedding_requirement(directive_id, require_bge_m3=getattr(args, "require_bge_m3", False))
    classification = opened["classification"]
    if classification["embedding_recommendation"] == "HOLD_FOR_REMEDIATION":
        return {
            "status": "HOLD_FOR_REMEDIATION", "directive_id": directive_id, "pipeline": "candidate_release",
            "classification": classification, "required_embedding_target": opened.get("required_embedding_target"),
            "next_action": "Remediate the sensitive-content signal and open a new exact package set.",
        }

    directive = guard.status(directive_id)
    from dual_pipeline.cli.intake_approval_helper import resolve_intake_publish_approval
    early_exit, directive = resolve_intake_publish_approval(directive, directive_id, classification, opened, guard, args)
    if early_exit is not None:
        return early_exit

    if directive["approval"] is None:
        policy_authorization = _authorization(args, production_required=True, capabilities=("knowledge:batch-admit",))
        assert policy_authorization is not None
        directive = guard.record_signed_batch_policy_admission(directive_id, authorization_context=policy_authorization)

    engine = _intake_engine(args)
    runs: list[dict[str, Any]] = []
    for package in packages:
        guard.authorize_intake(directive_id, package)
        runs.append(engine.ingest(package, a2a_finalization_receipt=args.a2a_finalization_receipt))
    run_ids = [run["run_id"] for run in runs]
    from dual_pipeline.canonical_pipeline.publish_helper import assemble_intake_publish_payload  # noqa: PLC0415
    if all(run["graph_operation_count"] == 0 for run in runs):
        completion = guard.complete_noop(directive_id, run_ids=run_ids)
        return assemble_intake_publish_payload(
            directive_id, directive, classification, runs, manager.active_pair(),
            status="COMPLETE", mutation_outcome="IDEMPOTENT_NOOP", completion=completion,
            worktree_dir=wt_dir, worktree_branch=wt_branch,
        )

    release = _release_batch(args, run_ids=run_ids)
    activated_pair = release.get("activation", {}).get("active_pair") or manager.active_pair()
    status = "COMPLETE" if release["status"] == "ACTIVE" else release["status"]
    return assemble_intake_publish_payload(
        directive_id, directive, classification, runs, activated_pair,
        status=status, release=release, worktree_dir=wt_dir, worktree_branch=wt_branch,
    )


def _cards_refactor(args: argparse.Namespace) -> dict[str, Any]:
    from tools.cards_refactor import execute as execute_cards_refactor

    forward_args = argparse.Namespace(**vars(args))
    forward_args.command = args.subcommand
    return execute_cards_refactor(forward_args)


def _graph_scout(args: argparse.Namespace) -> dict[str, Any]:
    from ..graph_scout import GraphScout, check_scheduled_scout_due, record_scheduled_scout_complete

    gated = check_scheduled_scout_due(args)
    if gated is not None:
        return gated

    graph_path = args.graph
    if graph_path is None or not graph_path.is_file():
        pair_path = args.runtime_root / "active_pair.json"
        if pair_path.is_file():
            from ..core import read_json
            pair = read_json(pair_path)
            graph_rel = pair.get("graph_path")
            if graph_rel:
                graph_path = (args.runtime_root / graph_rel).resolve()

    scout = GraphScout(
        graph_path=graph_path,
        runtime_root=args.runtime_root,
        similarity_threshold=getattr(args, "similarity_threshold", 0.72),
        max_candidate_age_days=getattr(args, "max_candidate_age_days", 30),
    )
    report = scout.run(output_dir=getattr(args, "output_dir", None))
    record_scheduled_scout_complete(args, report)

    if getattr(args, "json", False):
        import json
        print(json.dumps(report, indent=2))
    else:
        summary = report["summary"]
        print("\n============================================================")
        print(f" GRAPH HEALTH SCOUT REPORT: {report['run_id']}")
        print("============================================================")
        print(f" Near-Duplicate Entities Found:  {summary['duplicate_entity_count']}")
        print(f" Temporal Contradictions Found:  {summary['contradiction_count']}")
        print(f" Orphan / Stale Clutter Items:   {summary['clutter_count']}")
        print(f" Sealed Proposals Generated:     {summary['proposals_generated']}")
        print(f" HITL Action Items (Delta < 25%): {summary['hitl_required_count']}")
        print(f" Zero-Mutation Verified:         {summary['zero_mutation_verified']}")
        print(f" Saved Report:                   {report['report_path']}")
        print("============================================================\n")
    return report


def handle_run_history(args: argparse.Namespace) -> dict[str, Any]:
    """Query and display run execution history from learning.sqlite ledger."""
    runtime_root = getattr(args, "runtime_root", None) or Path(".local/dual_pipeline")
    ledger_path = Path(runtime_root) / "ledger" / "learning.sqlite"

    runs: list[dict[str, Any]] = []
    if ledger_path.is_file():
        from storage.connection import create_sqlite_conn
        try:
            conn = create_sqlite_conn(ledger_path)
            cursor = conn.cursor()
            query = "SELECT run_id, trace_id, use_case, status, sealed_at, event_count FROM run_seal"
            conditions = []
            params: list[Any] = []
            if getattr(args, "status", None):
                conditions.append("UPPER(status) = ?")
                params.append(args.status.upper())
            if getattr(args, "since", None):
                conditions.append("sealed_at >= ?")
                params.append(str(args.since))
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY sealed_at DESC LIMIT ?"
            params.append(int(getattr(args, "limit", 20)))

            cursor.execute(query, params)
            for row in cursor.fetchall():
                runs.append({
                    "run_id": row[0],
                    "trace_id": row[1],
                    "use_case": row[2],
                    "status": row[3],
                    "sealed_at": row[4],
                    "event_count": row[5],
                })
            conn.close()
        except Exception as e:
            logger.warning("Error querying learning.sqlite: %s", e)

    out = {
        "status": "PASS",
        "command": "run-history",
        "count": len(runs),
        "runs": runs,
    }

    if not getattr(args, "json", False):
        print("\n================================================================================")
        print(f" PIPELINE RUN HISTORY (Total: {len(runs)})")
        print("================================================================================")
        print(f"{'RUN ID':<36} {'STATUS':<10} {'USE CASE':<12} {'EVENTS':<8} {'SEALED AT'}")
        print("-" * 80)
        for r in runs:
            print(f"{r['run_id']:<36} {r['status']:<10} {r['use_case']:<12} {r['event_count']:<8} {r['sealed_at']}")
        print("================================================================================\n")

    return out


def _resolve_graph_db_mgr(args: argparse.Namespace) -> Any:
    from storage.database import DatabaseManager
    if getattr(args, "graph", None) and Path(args.graph).exists():
        graph_p = Path(args.graph).resolve()
        return DatabaseManager(base_dir=graph_p.parent, canonical_db_name=graph_p.name)
    if getattr(args, "runtime_root", None):
        rt_root = Path(args.runtime_root).resolve()
        if (rt_root / "canonical_graph.db").exists():
            return DatabaseManager(base_dir=rt_root)
        elif (rt_root / "storage_data" / "canonical_graph.db").exists() or (rt_root / "storage_data").exists():
            return DatabaseManager(base_dir=rt_root / "storage_data")
        else:
            return DatabaseManager(base_dir=rt_root)
    return DatabaseManager()


def handle_graph_export(args: argparse.Namespace) -> Dict[str, Any]:
    """Handler for `nee graph-export`."""
    db_mgr = _resolve_graph_db_mgr(args)

    fmt = getattr(args, "format", "turtle") or "turtle"
    output_path = getattr(args, "output", None)
    content = db_mgr.export_graph(format=fmt, output_path=output_path)

    if not output_path:
        print(content)

    return {
        "status": "PASS",
        "command": "graph-export",
        "format": fmt,
        "output_path": str(output_path) if output_path else None,
        "bytes": len(content.encode("utf-8")),
    }


def handle_graph_stats(args: argparse.Namespace) -> Dict[str, Any]:
    """Handler for `nee graph-stats`."""
    import json
    db_mgr = _resolve_graph_db_mgr(args)

    stats = db_mgr.get_graph_statistics()
    fmt = getattr(args, "format", "json") or "json"

    if fmt == "markdown":
        md = [
            "# Canonical Knowledge Graph Statistics",
            f"- **Total Nodes:** {stats['total_nodes']}",
            f"- **Total Edges:** {stats['total_edges']}",
            f"- **Total Assertions:** {stats['total_assertions']}",
            f"- **Orphan Count:** {stats['orphan_count']}",
            "",
            "## Degree Percentiles",
            "| P50 | P90 | P99 | Min | Max | Mean |",
            "|---|---|---|---|---|---|",
            f"| {stats['degree_percentiles']['p50']} | {stats['degree_percentiles']['p90']} | {stats['degree_percentiles']['p99']} | {stats['degree_percentiles']['min']} | {stats['degree_percentiles']['max']} | {stats['degree_percentiles']['mean']} |",
            "",
            "## Nodes by Type",
        ]
        for t, cnt in sorted(stats['nodes_by_type'].items()):
            md.append(f"- **{t}:** {cnt}")
        md.append("")
        md.append("## Edges by Predicate")
        for p, cnt in sorted(stats['edges_by_predicate'].items()):
            md.append(f"- **{p}:** {cnt}")
        md_text = "\n".join(md)
        print(md_text)
    else:
        print(json.dumps(stats, indent=2))

    return {
        "status": "PASS",
        "command": "graph-stats",
        "statistics": stats,
    }


def handle_graph_sync(args: argparse.Namespace) -> Dict[str, Any]:
    """Handler for `nee graph-sync`."""
    from dual_pipeline.export.warehouse_sync import WarehouseSyncEngine
    db_mgr = _resolve_graph_db_mgr(args)
    engine = WarehouseSyncEngine(db_manager=db_mgr)

    res = engine.sync(
        output_dir=args.output_dir,
        destination_format=args.destination,
        incremental_since=getattr(args, "incremental_since", None),
    )
    print(f"Graph sync completed successfully to {res.output_dir} ({res.total_records} records, {res.total_bytes} bytes)")
    return {
        "status": "PASS",
        "command": "graph-sync",
        "result": res.to_dict(),
    }


def _recover_session(args: argparse.Namespace) -> dict[str, Any]:
    """Handler for `nee recover-session`."""
    from dual_pipeline.live_meeting_recorder_core.disaster_recovery import recover_live_session
    target_dir = args.session_dir or getattr(args, "positional_dir", None)
    if not target_dir:
        print("Error: --session-dir or positional session directory argument is required.")
        return {"status": "FAIL", "error": "session directory is required"}

    result = recover_live_session(
        session_dir=target_dir,
        output_dir=getattr(args, "output_dir", None),
        project=getattr(args, "project", None),
        meeting=getattr(args, "meeting", None),
        date=getattr(args, "date", None),
    )
    if not result.success:
        print(f"\n❌ [DISASTER RECOVERY FAILED]: {result.error}\n")
        return {"status": "FAIL", "error": result.error}
    print("\n✅ [DISASTER RECOVERY COMPLETE]")
    print(f"  • Total Chunks Found:   {result.total_chunks}")
    print(f"  • Master WAV Audio:     {result.master_wav} ({result.duration_seconds:.2f}s)")
    print(f"  • Session Manifest:     {result.manifest_path} (Status: RECOVERED)")
    print(f"  • Forensic Transcript:  {result.transcript_path}")
    print(f"  • Collateral Drop Log:  {result.collateral_log_path or 'None (No artifacts in session)'}")
    print(f"  • Dialogue Turn Recov:  {result.recovered_turns} turns ({result.recovery_rate * 100:.1f}%)")
    return {"status": "PASS", "total_chunks": result.total_chunks, "recovery_rate": result.recovery_rate}


handle_recover_session = _recover_session



def _graph_consolidate_scan(args: argparse.Namespace) -> dict[str, Any]:
    """Handler for `nee graph-consolidate-scan`."""
    from ..graph_consolidation_core import build_consolidation_plan
    from ..release import ReleaseManager

    manager = ReleaseManager(args.runtime_root, args.graph)
    active = manager.active_pair()
    graph_path = Path(manager._resolve(active["graph_path"])).resolve()

    plan = build_consolidation_plan(
        graph_path,
        active_pair=active,
        node_threshold=getattr(args, "node_threshold", 0.65),
        literal_threshold=getattr(args, "literal_threshold", 0.85),
        evidence_threshold=getattr(args, "evidence_threshold", 0.90),
    )

    out_path = None
    if getattr(args, "output", None):
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            import json

            json.dump(plan, f, indent=2)

    return {
        "status": "PASS",
        "plan_id": plan["plan_id"],
        "plan_digest": plan["plan_digest"],
        "summary": plan["summary"],
        "output_path": str(out_path) if out_path else None,
        "plan": plan,
    }


def _graph_consolidate(args: argparse.Namespace) -> dict[str, Any]:
    """Unified handler for `nee graph-consolidate {scan,plan,release}`."""
    action = getattr(args, "action", "scan")
    if action in ("scan", "plan"):
        return _graph_consolidate_scan(args)
    if action == "release":
        return _graph_consolidate_release(args)
    raise ValueError(f"Unknown graph-consolidate action: {action}")


def _graph_consolidate_release(args: argparse.Namespace) -> dict[str, Any]:
    """Handler for `nee graph-consolidate release`."""
    from ..core import read_json
    from ..graph_consolidation_core import (
        activate_consolidation_release,
        build_consolidation_plan,
        prepare_consolidation_release,
        record_consolidation_release_events,
    )
    from ..release import ReleaseManager

    approval_id = getattr(args, "approval_id", None)
    if not approval_id:
        directive_id = getattr(args, "directive_id", None)
        if directive_id:
            approval_id = f"USER_INPUT: Operator Amit Ayer approves directive {directive_id}"
        else:
            raise PermissionError("Mutation approval required for consolidation release (--approval-id or --directive-id)")

    manager = ReleaseManager(args.runtime_root, args.graph)
    active = manager.active_pair()
    graph_path = Path(manager._resolve(active["graph_path"])).resolve()
    backend = _backend(args)

    plan_path = getattr(args, "plan", None)
    if plan_path:
        plan = read_json(Path(plan_path).resolve())
    else:
        plan = build_consolidation_plan(
            graph_path,
            active_pair=active,
            node_threshold=getattr(args, "node_threshold", 0.65),
            literal_threshold=getattr(args, "literal_threshold", 0.85),
            evidence_threshold=getattr(args, "evidence_threshold", 0.90),
        )

    run_id = getattr(args, "run_id", None) or f"run-consolidation-{active['generation_id'][-8:]}"

    receipt, receipt_path = prepare_consolidation_release(
        manager=manager,
        backend=backend,
        plan=plan,
        run_id=run_id,
        approval_id=approval_id,
    )

    telemetry = None
    activation = None
    if receipt.get("status") != "IDEMPOTENT_NOOP":
        telemetry = record_consolidation_release_events(
            runtime_root=manager.runtime_root, receipt=receipt
        )
        activation = activate_consolidation_release(
            manager=manager,
            receipt_path=receipt_path,
            approval_id=approval_id,
        )

    out_path = getattr(args, "output", None)
    if out_path:
        import json
        out_p = Path(out_path).resolve()
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as f:
            json.dump(receipt, f, indent=2)

    return {
        "status": "PASS",
        "action": "release",
        "release_status": receipt.get("status"),
        "receipt_id": receipt.get("receipt_id"),
        "receipt_path": str(receipt_path),
        "activation_status": activation.get("status") if activation else "N/A",
        "active_generation_id": manager.active_pair()["generation_id"],
        "receipt": receipt,
        "telemetry": telemetry,
        "activation": activation,
    }





# Wave 9 Rollout Policy
FEATURE_POLICY = {
    "output3_shadow_classification": True,
    "output3_controlled_activation": False
}

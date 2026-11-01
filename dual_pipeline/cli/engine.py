from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any

from ..core import ROOT, read_json, sha256_file, stable_id, write_json_exclusive
from ..exceptions import HITLRequiredError
from .parser import _l1_control_policy_from_args, build_parser
from .registry import registry

# Handlers are imported lazily to avoid circular imports with engine.py

def _ingestion_reasoning_service(args: argparse.Namespace) -> Any | None:
    """Import and configure the provider boundary only for an enabled mode."""

    mode = getattr(args, "ingestion_reasoning", "off")
    if mode == "off":
        return None
    from ..ingestion_openai import OpenAIIngestionProviderFactory
    from ..ingestion_reasoning import (
        IngestionReasoningService,
        load_ingestion_reasoning_policy,
    )

    policy = load_ingestion_reasoning_policy(args.ingestion_reasoning_policy)
    return IngestionReasoningService(
        policy=policy,
        mode=mode,
        provider_factory=OpenAIIngestionProviderFactory(policy),
        offline=bool(args.offline_ingestion_reasoning),
    )

def _backend(args: argparse.Namespace):
    from .model_resolution import auto_resolve_backend_config

    auto_resolve_backend_config(args)
    if getattr(args, "allow_dev_bge_m3", False) and getattr(args, "embedding_backend", None) != "bge-m3-dev":
        raise ValueError("--allow-dev-bge-m3 is valid only with --embedding-backend bge-m3-dev")
    if getattr(args, "allow_internal_operational", False) and getattr(args, "embedding_backend", None) != "bge-m3-internal":
        raise ValueError(
            "--allow-internal-operational is valid only with --embedding-backend bge-m3-internal"
        )
    if args.embedding_backend in {"bge-m3", "bge-m3-dev", "bge-m3-internal"}:
        model_root = Path(args.model_path).expanduser().resolve()
        runtime_root = args.runtime_root.expanduser().resolve()
        try:
            runtime_root.relative_to(model_root)
            overlaps = True
        except ValueError:
            try:
                model_root.relative_to(runtime_root)
                overlaps = True
            except ValueError:
                overlaps = False
        if overlaps:
            raise ValueError(
                "runtime root and immutable BGE-M3 snapshot cannot overlap"
            )
        from config.credentials import CredentialManager
        from ..preflight import (
            check_cpu,
            check_locked_environment,
            check_pip_dependencies,
            check_runtime_contract,
        )

        CredentialManager.set_credentials(
            {
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "HF_DATASETS_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "DO_NOT_TRACK": "1",
            }
        )
        runtime_checks = {
            "runtime_contract": check_runtime_contract(),
            "pip_dependencies": check_pip_dependencies(),
            "locked_environment": check_locked_environment(
                ROOT / "requirements" / "runtime-cpu.lock.txt"
            ),
            "cpu": check_cpu(),
        }
        if args.embedding_backend in {"bge-m3-dev", "bge-m3-internal"}:
            locked = runtime_checks["locked_environment"]
            # A development BGE run uses the identical pinned runtime but may
            # coexist with local developer-only tools. Production remains an
            # exact distribution-set lock; this narrow exception admits only
            # extras, never a missing or version-mismatched pinned package.
            if (
                locked.get("status") != "PASS"
                and not locked.get("missing")
                and not locked.get("version_mismatches")
            ):
                runtime_checks["locked_environment"] = {
                    **locked,
                    "status": "PASS",
                    "development_only_extras": locked.get("extra", []),
                }
            if args.embedding_backend == "bge-m3-dev" and platform.system() == "Darwin":
                # On macOS (Darwin), PyTorch wheels do not have the Linux-specific '+cpu'
                # local version suffix, and dependency closures reflect Darwin platform wheels.
                # Development runs remain explicitly non-production (production_qualified: false)
                # while executing real verified model weights on the local CPU/MPS runtime.
                if runtime_checks["cpu"].get("status") == "PASS":
                    runtime_checks["runtime_contract"] = {"status": "PASS", "darwin_dev_admitted": True}
                    runtime_checks["pip_dependencies"] = {"status": "PASS", "darwin_dev_admitted": True}
                    runtime_checks["locked_environment"] = {"status": "PASS", "darwin_dev_admitted": True}
        failures = [
            name
            for name, result in runtime_checks.items()
            if result.get("status") != "PASS"
        ]
        if failures:
            reason_codes = [
                runtime_checks[name]["reason_code"]
                for name in failures
                if runtime_checks[name].get("reason_code")
            ]
            raise RuntimeError(
                f"BGE-M3 runtime contract failed: {failures}"
                + (f"; reason_codes={reason_codes}" if reason_codes else "")
            )
    from ..index import get_backend

    return get_backend(
        getattr(args, "embedding_backend", "bge-m3"),
        allow_dev_backend=getattr(args, "allow_dev_bge_m3", False),
        allow_internal_backend=getattr(args, "allow_internal_operational", False),
        model_path=getattr(args, "model_path", None),
        model_revision=getattr(args, "model_revision", None),
        model_manifest=getattr(args, "model_manifest", None),
        internal_policy=getattr(args, "internal_operational_policy", None),
        device=getattr(args, "embedding_device", None),
        use_fp16=getattr(args, "embedding_fp16", None),
    )

def _authorization(
    args: argparse.Namespace,
    *,
    production_required: bool,
    capabilities: tuple[str, ...],
    signed_required: bool = False,
) -> Any | None:
    values = (
        getattr(args, "auth_token", None),
        getattr(args, "auth_public_key", None),
        getattr(args, "auth_issuer", None),
        getattr(args, "auth_audience", None),
    )
    if not any(values):
        if production_required or signed_required:
            raise PermissionError(
                "signed execution requires --auth-token, --auth-public-key, "
                "--auth-issuer, and --auth-audience"
            )
        return None
    if not all(values):
        if not production_required and not signed_required:
            return None
        raise ValueError(
            "all four enterprise-production authorization arguments are required together"
        )
    from ..authorization import load_production_authorization_context

    return load_production_authorization_context(
        args.auth_token,
        args.auth_public_key,
        supplied_issuer=args.auth_issuer,
        supplied_audience=args.auth_audience,
        required_capabilities=capabilities,
    )

def _bind_model_authorization(
    backend: Any, authorization: Any | None
) -> None:
    if not backend.production_qualified:
        return
    if (
        authorization is None
        or authorization.approved_model_manifest_digest
        != backend.model_manifest_digest
    ):
        raise PermissionError(
            "authorization token is not bound to the verified BGE-M3 model manifest"
        )

def _bind_receipt_model_authorization(
    receipt: dict[str, Any], authorization: Any | None
) -> None:
    if not receipt["next_pair"].get("production_qualified"):
        return
    expected = receipt["next_pair"].get("model_manifest_digest")
    if authorization is None or authorization.approved_model_manifest_digest != expected:
        raise PermissionError(
            "authorization token is not bound to the release BGE-M3 model manifest"
        )

def _graph_for_intake(runtime_root: Path, seed_graph: Path) -> Path:
    """Return the validated active graph, falling back to the immutable seed."""
    from ..release import ReleaseManager

    manager = ReleaseManager(runtime_root, seed_graph)
    if not manager.active_path.is_file():
        return seed_graph.resolve()
    pair = manager.active_pair()
    return (manager.runtime_root / pair["graph_path"]).resolve()

def _audiences(value: str) -> list[str]:
    audiences = list(
        dict.fromkeys(item.strip() for item in value.split(",") if item.strip())
    )
    if not audiences:
        raise ValueError("at least one audience is required")
    return audiences

def _assert_backend_matches_pair(
    backend: Any, pair: dict[str, Any], *, operation: str
) -> None:
    expected = {
        "embedding_backend": backend.backend_id,
        "model_id": backend.model_id,
        "model_revision": backend.model_revision,
        "model_manifest_digest": backend.model_manifest_digest,
        "production_qualified": backend.production_qualified,
    }
    mismatches = {
        field: {"active": pair.get(field), "requested": required}
        for field, required in expected.items()
        if pair.get(field) != required
    }
    if mismatches:
        raise RuntimeError(
            f"{operation} backend/model does not match the active pair: {mismatches}"
        )

def _intake_engine(args: argparse.Namespace) -> Any:
    backend = _backend(args)
    authorization = _authorization(
        args,
        production_required=backend.production_qualified,
        capabilities=("knowledge:intake",),
    )
    _bind_model_authorization(backend, authorization)
    from ..index import HybridRetriever
    from ..intake_engine import IntakeEngine
    from ..release import ReleaseManager

    manager = ReleaseManager(args.runtime_root, args.graph)
    pair = (
        manager.bootstrap(backend, authorization_context=authorization)
        if not manager.active_path.is_file()
        else manager.active_pair()
    )
    _assert_backend_matches_pair(backend, pair, operation="intake comparison")
    graph_path = manager._resolve(pair["graph_path"])
    sidecar_path = manager._resolve(pair["sidecar_path"])
    return IntakeEngine(
        graph_path,
        args.runtime_root,
        retriever=HybridRetriever(sidecar_path, backend),
        retrieval_audiences=(
            list(authorization.audiences)
            if authorization
            else _audiences(args.audience)
        ),
        authorization_context=authorization,
        active_pair=pair,
        batch_admission_manifest=getattr(args, "batch_manifest", None),
        ingestion_reasoning=_ingestion_reasoning_service(args),
        semantic_cache_mode=getattr(args, "semantic_cache_mode", None),
    )

def _completion_guard(args: argparse.Namespace) -> Any:
    from .ingest_worktree_guard import assert_ingest_worktree_code_freeze
    assert_ingest_worktree_code_freeze(getattr(args, "worktree_dir", None))
    from ..completion import IngestionCompletionGuard

    return IngestionCompletionGuard(args.runtime_root, args.graph)

def _default_publish_directive_id(packages: list[Path]) -> str:
    bindings = [
        {
            "path": str(Path(package).expanduser().resolve()),
            "sha256": sha256_file(Path(package).expanduser().resolve()),
        }
        for package in packages
    ]
    return stable_id("intake-publish", bindings, length=28)

def _audit_run(args: argparse.Namespace) -> dict[str, Any]:
    event_dir = args.runtime_root.resolve() / "runs" / args.run_id / "learning_events"
    event_paths = sorted(
        event_dir.glob("*.json"),
        key=lambda p: (int(p.name.split("-", 1)[0]) if p.name.split("-", 1)[0].isdigit() else 0, p.name),
    )
    if not event_paths:
        raise ValueError(f"run has no learning events: {args.run_id}")
    first = read_json(event_paths[0])
    from ..telemetry import RunRecorder

    recorder = RunRecorder(
        args.runtime_root,
        run_id=args.run_id,
        trace_id=first["trace_id"],
        use_case=first["use_case"],
        graph_snapshot=first.get("graph_snapshot"),
        model_or_index_generation=first.get("model_or_index_generation"),
    )
    return recorder.audit(args.required_stages)


def _handle_intake_directive_admit_batch(args: argparse.Namespace) -> dict[str, Any]:
    authorization = _authorization(
        args,
        production_required=True,
        capabilities=("knowledge:batch-admit",),
    )
    assert authorization is not None
    return _completion_guard(args).record_signed_batch_policy_admission(
        args.directive_id,
        authorization_context=authorization,
    )

def _handle_ingest(args: argparse.Namespace) -> dict[str, Any]:
    guard = _completion_guard(args)
    guard.authorize_intake(args.directive_id, args.package)
    result = _intake_engine(args).ingest(
        args.package,
        a2a_finalization_receipt=getattr(args, "a2a_finalization_receipt", None),
    )
    result["ingestion_directive"] = {
        "directive_id": args.directive_id,
        "status": "OPEN",
        "terminal": False,
    }
    return result

def _handle_intake_status(args: argparse.Namespace) -> dict[str, Any]:
    from ..intake_engine import IntakeEngine
    return IntakeEngine(args.graph, args.runtime_root).status(args.run_id)

def _handle_intake_review(args: argparse.Namespace) -> dict[str, Any]:
    return _intake_engine(args).review(args.run_id, args.decisions)

def _handle_semantic_cache_config(args: argparse.Namespace) -> dict[str, Any]:
    from ..semantic_cache import configure_semantic_cache
    return {
        "status": "PASS",
        "configuration": configure_semantic_cache(
            args.runtime_root,
            args.mode,
        ),
    }

def _handle_semantic_cache_status(args: argparse.Namespace) -> dict[str, Any]:
    from ..semantic_cache import SemanticCache
    return SemanticCache(args.runtime_root).status()

def _handle_bootstrap_runtime(args: argparse.Namespace) -> dict[str, Any]:
    backend = _backend(args)
    authorization = _authorization(
        args,
        production_required=backend.production_qualified,
        capabilities=("knowledge:release",),
    )
    _bind_model_authorization(backend, authorization)
    from ..release import ReleaseManager
    return ReleaseManager(args.runtime_root, args.graph).bootstrap(
        backend,
        authorization_context=authorization,
    )

def _handle_uwg_evaluate(args: argparse.Namespace) -> dict[str, Any]:
    backend = _backend(args)
    authorization = _authorization(
        args,
        production_required=backend.production_qualified,
        capabilities=("knowledge:query",),
    )
    _bind_model_authorization(backend, authorization)
    from ..uwg import UniversalWriteGateway
    return UniversalWriteGateway(args.runtime_root, args.graph).evaluate(
        args.benchmark,
        backend=backend,
        output_path=args.output,
        evaluation_id=args.evaluation_id,
        authorization_context=authorization,
    )

def _handle_retrieval_release_evaluate(args: argparse.Namespace) -> dict[str, Any]:
    backend = _backend(args)
    authorization = _authorization(
        args,
        production_required=backend.production_qualified,
        capabilities=("knowledge:query",),
    )
    _bind_model_authorization(backend, authorization)
    from ..retrieval_suite import AutomaticRetrievalReleaseSuiteBuilder
    from ..retrieval_release import RetrievalReleaseEvaluator
    suite_resolution = AutomaticRetrievalReleaseSuiteBuilder(
        args.runtime_root,
        args.graph,
    ).resolve(
        args.receipt,
        explicit_suite=args.suite,
    )
    result = RetrievalReleaseEvaluator(args.runtime_root, args.graph).evaluate(
        args.receipt,
        Path(suite_resolution["path"]),
        backend=backend,
        authorization_context=authorization,
    )
    result["suite_resolution"] = suite_resolution
    return result

def _handle_retrieval_release_suite_build(args: argparse.Namespace) -> dict[str, Any]:
    from ..retrieval_suite import AutomaticRetrievalReleaseSuiteBuilder
    return AutomaticRetrievalReleaseSuiteBuilder(
        args.runtime_root,
        args.graph,
    ).build(args.receipt, output_path=args.output)

def _handle_uwg_register_approval(args: argparse.Namespace) -> dict[str, Any]:
    backend = _backend(args)
    from ..assurance import backend_assurance_tier, INTERNAL_OPERATIONAL
    if backend_assurance_tier(backend) == INTERNAL_OPERATIONAL:
        raise PermissionError(
            "internal operational policy forbids autonomous UWG governance writes; "
            "use the directive and HITL release lifecycle"
        )
    authorization = _authorization(
        args,
        production_required=backend.production_qualified,
        capabilities=("knowledge:release", "knowledge:governance"),
        signed_required=True,
    )
    _bind_model_authorization(backend, authorization)
    from ..uwg import UniversalWriteGateway
    return UniversalWriteGateway(
        args.runtime_root, args.graph
    ).register_governance_approval(
        args.approval_request,
        backend=backend,
        authorization_context=authorization,
    )

def _handle_uwg_promote(args: argparse.Namespace) -> dict[str, Any]:
    backend = _backend(args)
    from ..assurance import backend_assurance_tier, INTERNAL_OPERATIONAL
    if backend_assurance_tier(backend) == INTERNAL_OPERATIONAL:
        raise PermissionError(
            "internal operational policy forbids autonomous UWG promotion; "
            "use the directive and HITL release lifecycle"
        )
    authorization = _authorization(
        args,
        production_required=backend.production_qualified,
        capabilities=("knowledge:release",),
    )
    _bind_model_authorization(backend, authorization)
    from ..uwg import UniversalWriteGateway
    return UniversalWriteGateway(args.runtime_root, args.graph).prepare(
        args.evaluation,
        args.decision,
        backend=backend,
        authorization_context=authorization,
    )

def _handle_uwg_activate_or_rollback(args: argparse.Namespace) -> dict[str, Any]:
    receipt = read_json(args.receipt)
    from ..assurance import pair_assurance_tier, INTERNAL_OPERATIONAL
    if pair_assurance_tier(receipt["next_pair"]) == INTERNAL_OPERATIONAL:
        raise PermissionError(
            "internal operational policy forbids UWG activation and rollback"
        )
    action = "activate" if args.command == "uwg-activate" else "rollback"
    from ..release import release_action_resources
    release_action_resources(receipt, action)
    authorization = _authorization(
        args,
        production_required=receipt["next_pair"]["production_qualified"],
        capabilities=("knowledge:release",),
    )
    _bind_receipt_model_authorization(receipt, authorization)
    from ..uwg import UniversalWriteGateway
    gateway = UniversalWriteGateway(args.runtime_root, args.graph)
    if action == "activate":
        return gateway.activate(args.receipt, authorization_context=authorization)
    return gateway.rollback(args.receipt, authorization_context=authorization)

def _handle_intake_release_prepare(args: argparse.Namespace) -> dict[str, Any]:
    from ..release import ReleaseManager
    manager = ReleaseManager(args.runtime_root, args.graph)
    backend = _backend(args)
    authorization = _authorization(
        args,
        production_required=backend.production_qualified,
        capabilities=("knowledge:release",),
    )
    _bind_model_authorization(backend, authorization)
    release_validation = _completion_guard(args).validate_release_request(
        args.directive_id,
        run_ids=args.run_id,
    )
    receipt = manager.prepare_many(
        run_ids=args.run_id,
        approval_id=release_validation["approval_id"],
        backend=backend,
        authorization_context=authorization,
    )
    receipt["receipt_path"] = str(
        (args.runtime_root.resolve() / "releases" / f"release-{receipt['next_pair']['generation_id']}.json")
    )
    return receipt

def _handle_intake_release_activate(args: argparse.Namespace) -> dict[str, Any]:
    receipt = read_json(args.receipt)
    from ..release import ReleaseManager, release_action_resources
    release_action_resources(receipt, "activate")
    authorization = _authorization(
        args,
        production_required=receipt["next_pair"]["production_qualified"],
        capabilities=("knowledge:release",),
    )
    _bind_receipt_model_authorization(receipt, authorization)
    guard = _completion_guard(args)
    guard.preflight_activation(args.directive_id, args.receipt)
    manager = ReleaseManager(args.runtime_root, args.graph)
    activation = manager.activate(
        args.receipt,
        authorization_context=authorization,
    )
    result = dict(activation)
    try:
        completion_result = guard.complete(
            args.directive_id, args.receipt
        )
    except Exception as comp_err:
        with contextlib.suppress(Exception):
            manager.rollback(args.receipt, authorization_context=authorization)
        raise RuntimeError(
            f"Release activation failed during completion ({comp_err}); automatic rollback was executed."
        ) from comp_err
    result["ingestion_completion"] = completion_result
    if "mutation_summary" in completion_result:
        result["mutation_summary"] = completion_result["mutation_summary"]
    elif "mutation_summary" in activation:
        result["mutation_summary"] = activation["mutation_summary"]
    return result

def _handle_intake_release_rollback(args: argparse.Namespace) -> dict[str, Any]:
    receipt = read_json(args.receipt)
    from ..release import ReleaseManager, release_action_resources
    release_action_resources(receipt, "rollback")
    authorization = _authorization(
        args,
        production_required=receipt["next_pair"]["production_qualified"],
        capabilities=("knowledge:release",),
    )
    _bind_receipt_model_authorization(receipt, authorization)
    from ..release import ReleaseManager
    return ReleaseManager(args.runtime_root, args.graph).rollback(
        args.receipt,
        authorization_context=authorization,
    )

def _apply_seamless_lineage_formatting(res: Any, graph_path: Any | None = None) -> None:
    from dual_pipeline.query_core.lineage_formatter import apply_seamless_lineage_formatting
    apply_seamless_lineage_formatting(res, graph_path)


def _handle_ask(args: argparse.Namespace) -> dict[str, Any]:
    from ..query_router import route_query, QueryDestination
    from ..decision_service import DecisionService
    from ..l2 import load_l2_config
    from ..system_directory import find_systems_in_text
    question = getattr(args, "question", None) or getattr(args, "query_text", None)
    if not question:
        raise ValueError("A query string must be provided via positional query_text or --question.")
    args.question = question
    preview = route_query(
        question,
        query_type_override=args.query_type,
        destination_override=args.destination,
        requested_materiality=args.materiality,
        delivery_profile=args.delivery_profile,
        artifact_type=args.artifact_type,
        authorized=True,
    )
    provider_egress = (
        getattr(args, "provider_egress_allowed", None)
        if getattr(args, "provider_egress_allowed", None) is not None
        else load_l2_config().provider_egress_allowed
    )
    service_arguments = {
        "options": args.option,
        "criteria": args.criterion,
        "constraints": args.constraint,
        "assumptions": args.assumption,
        "time_horizon": args.time_horizon,
        "query_type": args.query_type,
        "output_dir": args.output_dir,
        "user_id": args.user_id,
        "classification": args.classification,
        "audience": args.audience,
        "materiality": args.materiality,
        "provider_egress_allowed": provider_egress,
        "card_id": args.card_id,
        "card_view_version": args.card_view_version,
        "card_candidate_review": args.card_candidate_review,
        "destination": args.destination,
        "delivery_profile": args.delivery_profile,
        "artifact_type": args.artifact_type,
    }
    if not preview.enters_query_runtime or (
        preview.destination is QueryDestination.GRAPH_EVIDENCE_UTILITY and find_systems_in_text(args.question)
    ):
        res = DecisionService(
            query_engine=None,
            runtime_root=args.runtime_root,
        ).query(args.question, **service_arguments)
        _apply_seamless_lineage_formatting(res, getattr(args, "graph", None))
        from dual_pipeline.answer_ledger import record_query_answer_safe
        record_query_answer_safe(
            args.runtime_root,
            args.question,
            res,
            query_type=args.query_type or preview.query_type,
        )
        return res
    backend = _backend(args)
    from ..l2 import OpenAIProviderFactory, load_l2_config
    l2_config = load_l2_config()
    l1_control_policy = _l1_control_policy_from_args(args)
    authorization = _authorization(
        args,
        production_required=backend.production_qualified,
        capabilities=("knowledge:query",),
    )
    _bind_model_authorization(backend, authorization)
    from ..query import QueryEngine
    engine = QueryEngine(
        args.runtime_root,
        args.graph,
        backend,
        provider_factory=OpenAIProviderFactory(l2_config),
        l2_config=l2_config,
        semantic_cache_mode=args.semantic_cache_mode,
        l1_controller_mode=args.l1_controller_mode,
        l1_control_policy=l1_control_policy,
    )
    _t_query_start = time.perf_counter()
    res = DecisionService(
        query_engine=engine,
        runtime_root=args.runtime_root,
    ).query(
        args.question,
        authorization_context=authorization,
        **service_arguments,
    )
    _latency_ms = (time.perf_counter() - _t_query_start) * 1000.0
    graph_p = getattr(args, "graph", None) or getattr(backend, "graph_path", None)
    _apply_seamless_lineage_formatting(res, graph_p)
    from dual_pipeline.answer_ledger import record_query_answer_safe
    active_p = getattr(getattr(engine, "manager", None), "active_pair", lambda: {})() if hasattr(getattr(engine, "manager", None), "active_pair") else {}
    record_query_answer_safe(
        args.runtime_root,
        args.question,
        res,
        query_type=args.query_type or preview.query_type,
        latency_ms=_latency_ms,
        graph_sha256=active_p.get("graph_sha256") if isinstance(active_p, dict) else None,
        generation=(
            active_p.get("generation_id")
            if isinstance(active_p, dict) and active_p.get("generation_id")
            else getattr(getattr(engine, "manager", None), "active_generation_id", None)
        ),
    )
    return res

def _handle_decision_serve(args: argparse.Namespace) -> dict[str, Any]:
    from ..decision_service import DecisionService, LazyDecisionService
    from ..decision_api import runtime_readiness, serve_decision_api
    from ..query import QueryEngine
    from ..l2 import OpenAIProviderFactory, load_l2_config

    def runtime_service_factory() -> DecisionService:
        backend = _backend(args)
        l2_config = load_l2_config()
        authorization = _authorization(
            args,
            production_required=backend.production_qualified,
            capabilities=("knowledge:query",),
        )
        _bind_model_authorization(backend, authorization)
        engine = QueryEngine(
            args.runtime_root,
            args.graph,
            backend,
            provider_factory=OpenAIProviderFactory(l2_config),
            l2_config=l2_config,
            semantic_cache_mode=args.semantic_cache_mode,
            l1_controller_mode=args.l1_controller_mode,
            l1_control_policy=_l1_control_policy_from_args(args),
        )
        return DecisionService(
            query_engine=engine,
            runtime_root=args.runtime_root,
            query_defaults={"authorization_context": authorization},
        )

    service = LazyDecisionService(
        runtime_root=args.runtime_root,
        runtime_service_factory=runtime_service_factory,
    )
    serve_decision_api(
        service, host=args.host, port=args.port, max_concurrency=args.max_concurrency,
        max_queue_size=args.max_queue_size, request_timeout_seconds=args.request_timeout_seconds,
        readiness_check=lambda: runtime_readiness(args.runtime_root),
    )
    return {"status": "STOPPED"}

def _handle_eval_route_run(args: argparse.Namespace) -> dict[str, Any]:
    from ..evals import run_route_suite
    result, result_path = run_route_suite(
        suite_path=args.suite,
        output_root=args.output_root,
        evaluation_id=args.evaluation_id,
        tags=args.tag,
    )
    return {"status": result["status"], "result": result, "result_path": str(result_path)}

def _handle_eval_l1_controller_run(args: argparse.Namespace) -> dict[str, Any]:
    from ..l1_evaluation import run_l1_evaluation_suite, run_l1_route_integration
    e1 = run_l1_evaluation_suite(
        suite_path=args.suite,
        output_root=args.output_root / "e1",
        evaluation_id=args.evaluation_id,
    )
    e2 = run_l1_route_integration(
        suite_path=args.suite,
        baseline_path=args.baseline_suite,
        output_root=args.output_root / "e2-route",
        evaluation_id=args.evaluation_id,
    )
    return {"status": "PASS" if e1["status"] == "PASS" and e2["status"] == "PASS" else "FAIL", "e1": e1, "e2": e2}

def _handle_eval_run(args: argparse.Namespace) -> dict[str, Any]:
    backend = _backend(args)
    from ..evals import run_suite
    result, result_path = run_suite(
        suite_path=args.suite,
        runtime_root=args.runtime_root,
        seed_graph=args.graph,
        backend=backend,
        evaluation_id=args.evaluation_id,
        tags=args.tag,
    )
    return {"status": result["status"], "result": result, "result_path": str(result_path)}

def _handle_eval_bridge_run(args: argparse.Namespace) -> dict[str, Any]:
    backend = _backend(args)
    from ..evals import run_bridge_suite
    result, result_path = run_bridge_suite(
        suite_path=args.suite.expanduser().absolute().resolve(),
        runtime_root=args.runtime_root,
        seed_graph=args.graph,
        backend=backend,
        evaluation_id=args.evaluation_id,
        tags=args.tag,
    )
    return {"status": result["status"], "result": result, "result_path": str(result_path)}

def _handle_eval_stress_run(args: argparse.Namespace) -> dict[str, Any]:
    backend = _backend(args)
    from ..evals import run_stress_suite
    result, result_path = run_stress_suite(
        suite_path=args.suite.expanduser().absolute().resolve(),
        runtime_root=args.runtime_root,
        seed_graph=args.graph,
        backend=backend,
        repetitions=args.repetitions,
        stress_id=args.stress_id,
        tags=args.tag,
    )
    return {"status": result["status"], "result": result, "result_path": str(result_path)}

def _handle_eval_assurance_plan(args: argparse.Namespace) -> dict[str, Any]:
    from ..evals import validate_portfolio
    return validate_portfolio(args.portfolio)

def _handle_eval_compare(args: argparse.Namespace) -> dict[str, Any]:
    from ..evals import compare_results
    comparison = compare_results(
        baseline_path=args.baseline,
        candidate_path=args.candidate,
    )
    output = args.output.expanduser().resolve()
    write_json_exclusive(output, comparison)
    return {"status": comparison["status"], "comparison": comparison, "output": str(output)}

def _handle_eval_workflow_run(args: argparse.Namespace) -> dict[str, Any]:
    from ..evals import run_workflow_suite
    result, result_path = run_workflow_suite(
        suite_path=args.suite,
        output_root=args.output_root,
        treatment_id=args.treatment_id,
        evaluation_id=args.evaluation_id,
    )
    return {
        "status": result["status"],
        "result": result,
        "result_path": str(result_path),
    }

def _handle_eval_workflow_compare(args: argparse.Namespace) -> dict[str, Any]:
    from ..evals import compare_workflow_results
    comparison = compare_workflow_results(
        baseline_path=args.baseline,
        candidate_path=args.candidate,
    )
    output = args.output.expanduser().resolve()
    write_json_exclusive(output, comparison)
    return {
        "status": comparison["status"],
        "comparison": comparison,
        "output": str(output),
    }

def _handle_validate_runtime(args: argparse.Namespace) -> dict[str, Any]:
    from ..release import ReleaseManager
    from ..assurance import internal_operational_status
    manager = ReleaseManager(args.runtime_root, args.graph)
    pair = manager.active_pair()
    return {
        "active_pair": pair,
        "validation": manager.validate_pair(pair),
        "assurance": internal_operational_status(
            args.runtime_root,
            pair,
            policy_path=args.internal_operational_policy,
        ),
    }

def _handle_internal_operational_qualify(args: argparse.Namespace) -> dict[str, Any]:
    backend = _backend(args)
    from ..assurance import backend_assurance_tier, INTERNAL_OPERATIONAL
    if backend_assurance_tier(backend) != INTERNAL_OPERATIONAL:
        raise PermissionError(
            "qualification requires --embedding-backend bge-m3-internal "
            "--allow-internal-operational"
        )
    from ..internal_operational import InternalOperationalManager
    from ..credentials import CredentialManager
    reviewer = getattr(args, "reviewer", None) or CredentialManager.get_credential("NEE_REVIEWER") or "human_operator"
    rationale = getattr(args, "rationale", None) or f"Qualify internal operational pair from {args.directive}"
    return InternalOperationalManager(
        args.runtime_root,
        args.graph,
        policy_path=args.internal_operational_policy,
    ).qualify(
        directive_path=args.directive,
        source_evaluation_path=args.source_evaluation,
        suite_path=args.suite,
        backend=backend,
        reviewer=reviewer,
        rationale=rationale,
    )

def _handle_internal_operational_status(args: argparse.Namespace) -> dict[str, Any]:
    from ..internal_operational import InternalOperationalManager
    return InternalOperationalManager(
        args.runtime_root,
        args.graph,
        policy_path=args.internal_operational_policy,
    ).status()

def _handle_internal_operational_revoke(args: argparse.Namespace) -> dict[str, Any]:
    from ..internal_operational import InternalOperationalManager
    from ..credentials import CredentialManager
    reviewer = getattr(args, "reviewer", None) or CredentialManager.get_credential("NEE_REVIEWER") or "human_operator"
    return InternalOperationalManager(
        args.runtime_root,
        args.graph,
        policy_path=args.internal_operational_policy,
    ).revoke(
        reviewer=reviewer,
        reason=args.reason,
    )

def _handle_production_preflight(args: argparse.Namespace) -> dict[str, Any]:
    from ..preflight import PreflightConfig, run_preflight
    return run_preflight(
        PreflightConfig(
            runtime_root=args.runtime_root,
            graph=args.graph,
            model_path=args.model_path,
            model_revision=args.model_revision,
            model_manifest=args.model_manifest,
            report=args.report,
            strict_production=args.strict_production,
            auth_token=args.auth_token,
            auth_public_key=args.auth_public_key,
            auth_issuer=args.auth_issuer,
            auth_audience=args.auth_audience,
            copilot_package=args.copilot_package,
            architecture_package=args.architecture_package,
            pdlc_question_file=args.pdlc_question_file,
            approval_id=args.approval_id,
            copilot_decisions=args.copilot_decisions,
            architecture_decisions=args.architecture_decisions,
        )
    )

def _handle_executive_insights(args: argparse.Namespace) -> dict[str, Any]:
    from ..core import connect_sqlite
    from ..executive_analytics import ExecutiveAnalyticsEngine

    active_graph = args.graph
    if not active_graph.exists():
        active_pair_path = args.runtime_root / "active_pair.json"
        if active_pair_path.exists():
            pair_data = json.loads(active_pair_path.read_text(encoding="utf-8"))
            p = Path(pair_data["graph_path"])
            active_graph = p if p.is_absolute() else args.runtime_root / p

    conn = connect_sqlite(active_graph)
    analytics = ExecutiveAnalyticsEngine(conn)

    if args.pattern == "all":
        payload = analytics.run_all_executive_analyses(tenant_id=args.tenant_id)
        if args.format == "markdown":
            payload["markdown_report"] = analytics.format_executive_brief_markdown(payload)
        return payload
    elif args.pattern == "contradictions":
        items = analytics.detect_cross_silo_contradictions(tenant_id=args.tenant_id)
        return {"pattern": "contradictions", "count": len(items), "results": items}
    elif args.pattern == "duplications":
        items = analytics.harvest_duplicative_platform_spend(tenant_id=args.tenant_id)
        return {"pattern": "duplications", "count": len(items), "results": items}
    elif args.pattern == "blockers":
        items = analytics.trace_hidden_decision_blockers(tenant_id=args.tenant_id)
        return {"pattern": "blockers", "count": len(items), "results": items}
    elif args.pattern == "fragility":
        items = analytics.analyze_key_person_fragility(tenant_id=args.tenant_id)
        return {"pattern": "fragility", "count": len(items), "results": items}
    elif args.pattern == "reuse":
        items = analytics.identify_reusable_ip_assets(tenant_id=args.tenant_id)
        return {"pattern": "reuse", "count": len(items), "results": items}
    return {"status": "PASS"}

def _handle_demo(args: argparse.Namespace) -> dict[str, Any]:
    backend = _backend(args)
    from ..l2 import OpenAIProviderFactory, load_l2_config
    from ..release import ReleaseManager, release_action_resources
    from ..intake_engine import IntakeEngine
    from ..index import HybridRetriever
    from ..query import QueryEngine
    l2_config = load_l2_config()
    authorization = _authorization(
        args, production_required=backend.production_qualified,
        capabilities=("knowledge:intake", "knowledge:query", "knowledge:release"),
    )
    _bind_model_authorization(backend, authorization)
    manager = ReleaseManager(args.runtime_root, args.graph)
    bootstrap = manager.bootstrap(backend, authorization_context=authorization)
    intake = IntakeEngine(
        manager._resolve(bootstrap["graph_path"]), args.runtime_root,
        retriever=HybridRetriever(manager._resolve(bootstrap["sidecar_path"]), backend),
        retrieval_audiences=list(authorization.audiences) if authorization else _audiences(args.audience),
        authorization_context=authorization, active_pair=bootstrap, semantic_cache_mode=args.semantic_cache_mode,
    ).ingest(args.package)
    receipt = manager.prepare(
        run_id=intake["run_id"], approval_id=args.approval_id, backend=backend, authorization_context=authorization,
    )
    receipt_path = args.runtime_root.resolve() / "releases" / f"release-{receipt['next_pair']['generation_id']}.json"
    release_action_resources(receipt, "activate")
    _bind_receipt_model_authorization(receipt, authorization)
    directive_id = getattr(args, "directive_id", None)
    guard = _completion_guard(args) if directive_id else None
    if guard is not None:
        guard.preflight_activation(directive_id, receipt_path)
    activation = manager.activate(receipt_path, authorization_context=authorization)
    if guard is not None:
        try:
            guard.complete(directive_id, receipt_path)
        except Exception as comp_err:
            with contextlib.suppress(Exception):
                manager.rollback(receipt_path, authorization_context=authorization)
            raise RuntimeError(f"Demo release activation failed during completion ({comp_err}); automatic rollback was executed.") from comp_err
    demo_egress = getattr(args, "provider_egress_allowed", None)
    demo_provider_egress = demo_egress if demo_egress is not None else l2_config.provider_egress_allowed
    query = QueryEngine(
        args.runtime_root, args.graph, backend, provider_factory=OpenAIProviderFactory(l2_config), l2_config=l2_config,
        semantic_cache_mode=args.semantic_cache_mode, l1_controller_mode=args.l1_controller_mode,
        l1_control_policy=_l1_control_policy_from_args(args),
    ).ask(
        args.question, query_type=args.query_type, output_dir=args.output_dir, audience=args.audience,
        authorization_context=authorization, materiality=args.materiality, provider_egress_allowed=demo_provider_egress,
        destination=args.destination, delivery_profile=args.delivery_profile, artifact_type=args.artifact_type,
    )
    rollback = manager.rollback(receipt_path, authorization_context=authorization) if args.rollback else None
    ok = query["status"] == "COMPLETED" and activation["status"] == "ACTIVE" and (rollback is None or rollback["status"] == "ROLLED_BACK")
    return {
        "status": "PASS" if ok else "BLOCKED", "bootstrap_generation_id": bootstrap["generation_id"],
        "intake": intake, "release_receipt_id": receipt["receipt_id"], "activation": activation, "query": query, "rollback": rollback,
    }



def _handle_record_meeting_live(recorder: Any, reviewer: str) -> dict[str, Any]:
    recorder.run()
    valid_chunk_count = len(recorder.processed_chunks)
    master_size = recorder.master_wav.stat().st_size if recorder.master_wav.exists() else 0
    if not recorder.mock_mode and (valid_chunk_count == 0 or master_size < 4096):
        err_msg = f"[RECORDING-FAILED-EMPTY] Recording concluded with 0 valid audio chunks (master size: {master_size}B)."
        print(f"❌ {err_msg}", file=sys.stderr, flush=True)
        return {"status": "FAIL", "command": "record-meeting", "error": err_msg, "chunks_processed": valid_chunk_count, "master_wav_bytes": master_size}
    canonical_cmd = getattr(recorder, "canonical_ingest_command", None)
    if not canonical_cmd and recorder.master_wav:
        from dual_pipeline.cli.canonical_invocation import build_canonical_ingest_command

        canonical_cmd = build_canonical_ingest_command(
            input_path=recorder.master_wav, project=recorder.project_name,
            meeting=recorder.meeting_name, date=recorder.meeting_date_display,
            attendees=recorder.attendees, reviewer=reviewer,
            filename_slug=getattr(recorder, "clean_slug", None),
            worktree_dir=getattr(recorder, "worktree_dir", None),
            time=getattr(recorder, "meeting_time_window", None),
        )
    print(
        f"\n[CANONICAL-INGESTION-INVOCATION] Master audio ready for governed ingestion:\n{canonical_cmd}\n",
        flush=True,
    )
    return {
        "status": "PASS",
        "command": "record-meeting",
        "meeting_name": recorder.meeting_name,
        "project": recorder.project_name,
        "output_dir": str(recorder.output_dir),
        "transcript_path": str(recorder.transcript_md),
        "chunks_processed": valid_chunk_count,
        "master_wav_path": str(recorder.master_wav) if recorder.master_wav else None,
        "canonical_ingest_command": canonical_cmd,
    }


def _handle_record_meeting(args: argparse.Namespace) -> dict[str, Any]:
    project_val = getattr(args, "project", None)
    if not project_val and getattr(args, "meeting", None):
        from dual_pipeline.project_aliases import infer_project_from_title
        project_val = infer_project_from_title(args.meeting)
    enable_copilot = bool(getattr(args, "copilot", False) and not getattr(args, "no_copilot", False))
    from dual_pipeline.live_meeting_recorder_core.recording_host_integration import (
        dispatch_record_meeting_cli,
    )
    early_result = dispatch_record_meeting_cli(args, project_val, enable_copilot)
    if early_result is not None:
        return early_result

    from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
    recorder = LiveMeetingRecorder(
        project_name=project_val,
        meeting_name=args.meeting,
        meeting_date=args.date,
        attendees=args.attendees,
        device_index=args.device,
        segment_seconds=args.segment_seconds,
        whisper_prompt=args.whisper_prompt,
        mock_mode=getattr(args, "mock", False),
        backend=getattr(args, "backend", "auto"),
        audio_source=getattr(args, "audio_source", "auto"),
        whisper_model=getattr(args, "whisper_model", None),
        enable_copilot=enable_copilot,
        copilot_prefetch=getattr(args, "copilot_prefetch", True),
        resume=getattr(args, "resume", False),
        immediate_worktree=getattr(args, "immediate_worktree", False),
        dry_run=False,
        fast_start=getattr(args, "fast_start", True),
        instant=getattr(args, "instant", True),
        probation_ms=getattr(args, "probation_ms", 1000),
        inactivity_timeout_seconds=getattr(args, "inactivity_timeout", 0.0),
        **(
            {"session_id": args.session_id}
            if getattr(args, "session_id", None)
            else {}
        ),
    )

    return _handle_record_meeting_live(recorder, getattr(args, "reviewer", "operator") or "operator")


def _init_command_registry() -> CommandRegistry:
    from dual_pipeline.cli.registry import get_default_registry
    return get_default_registry()


_READ_ONLY_ADVISORY_COMMANDS = frozenset({
    "copilot", "session-inspect", "ask", "query", "formats", "active-pair",
    "intake-status", "runtime-evidence", "validate-runtime", "run-history",
    "answer-feedback", "executive-insights",
})


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "command", None) not in _READ_ONLY_ADVISORY_COMMANDS:
        from .ingest_worktree_guard import assert_ingest_worktree_code_freeze
        assert_ingest_worktree_code_freeze(getattr(args, "worktree_dir", None))
    max_cost = getattr(args, "max_cost_usd", None)
    if max_cost is not None:
        cost_val = float(max_cost)
        if cost_val <= 0:
            raise ValueError(f"--max-cost-usd must be > 0, got {max_cost}")
        from dual_pipeline.cost_tracker import get_cost_tracker
        get_cost_tracker(max_cost_usd=cost_val)
    reg = _init_command_registry()
    if reg.is_registered(args.command):
        return reg.dispatch(args)
    raise ValueError(f"unsupported command: {args.command}")


execute_command = execute

def _result_exit_code(args: argparse.Namespace, result: dict[str, Any]) -> int:
    if args.command == "production-preflight" and result.get("status") != "PASS":
        return 1
    if args.command == "ask" and result.get("terminal_state") not in {
        "DECISION_PRODUCED", "EVIDENCE_ANSWER_PRODUCED", "HANDOFF_READY",
    }:
        return 1
    if args.command == "uwg-evaluate" and result["evaluation"].get("status") != "PASS":
        return 1
    if args.command in {"eval-run", "eval-compare", "eval-workflow-run", "eval-workflow-compare"} and result.get("status") != "PASS":
        return 1
    if args.command == "retrieval-release-evaluate" and result["evaluation"].get("status") != "PASS":
        return 1
    if args.command in {"demo", "record-meeting"} and result.get("status") != "PASS":
        return 1
    if result.get("status") == "DECLINED" and not result.get("graph_mutation_committed"):
        return 0
    if result.get("status") not in (None, "PASS", "COMPLETE", "NOOP", "PACKAGE_SEALED", "AWAITING_APPROVAL", "PUBLISHED", "APPROVED"):
        return 1
    return 0


def _parse_cli_args(argv: list[str] | None) -> argparse.Namespace:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="backslashreplace")
    try:
        from dual_pipeline.core import load_ssot_env
        load_ssot_env()
    except Exception:
        pass
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    for tok in raw_argv[:3]:
        if "copilot" in tok.lower():
            from .copilot_handlers import normalize_copilot_argv

            raw_argv = normalize_copilot_argv(raw_argv)
            break
    return build_parser().parse_args(raw_argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_cli_args(argv)

    # Wave 5: Configure structured logging
    try:
        from dual_pipeline.logging_config import configure_logging
        log_fmt = getattr(args, "log_format", "text") or "text"
        log_lvl = (
            "CRITICAL" if args.command == "copilot" and not getattr(args, "json", False)
            else getattr(args, "log_level", "INFO") or "INFO"
        )
        configure_logging(log_format=log_fmt, log_level=log_lvl)
    except Exception:
        pass

    # Wave 5: Distributed traceparent context propagation
    from config.credentials import CredentialManager
    traceparent = getattr(args, "traceparent", None) or CredentialManager.get_secret("TRACEPARENT")
    if traceparent:
        try:
            from opentelemetry.context import attach

            from dual_pipeline.otel_engine import extract_trace_context
            ctx = extract_trace_context({"traceparent": traceparent})
            attach(ctx)
        except Exception:
            pass

    # Wave 6: Graceful signal shutdown
    try:
        import signal
        def _cli_signal_handler(signum: int, _frame: Any) -> None:
            sys.stderr.write(f"\n[CLI] Caught signal {signum}, initiating graceful exit...\n")
            sys.stderr.flush()
            sys.exit(128 + signum)

        signal.signal(signal.SIGINT, _cli_signal_handler)
        signal.signal(signal.SIGTERM, _cli_signal_handler)
    except (ValueError, AttributeError):
        pass

    # Wave 6: Profiling hooks
    profiler = None
    if getattr(args, "profile", False):
        try:
            import cProfile
            import tracemalloc
            tracemalloc.start()
            profiler = cProfile.Profile()
            profiler.enable()
        except Exception as e:
            sys.stderr.write(f"Warning: could not start profiler: {e}\n")

    try:
        try:
            result = execute(args)
        finally:
            if profiler is not None:
                try:
                    import io
                    import pstats
                    import tracemalloc
                    profiler.disable()
                    snapshot = tracemalloc.take_snapshot()
                    tracemalloc.stop()

                    prof_out = getattr(args, "profile_output", None)
                    if prof_out:
                        profiler.dump_stats(prof_out)
                        sys.stderr.write(f"[PROFILER] Saved profile stats to {prof_out}\n")

                    s = io.StringIO()
                    ps = pstats.Stats(profiler, stream=s).sort_stats("cumulative")
                    ps.print_stats(20)
                    sys.stderr.write("\n=== TOP 20 PROFILING HOTSPOTS (cProfile) ===\n")
                    sys.stderr.write(s.getvalue())

                    top_allocs = snapshot.statistics("lineno")[:10]
                    sys.stderr.write("\n=== TOP 10 MEMORY ALLOCATIONS (tracemalloc) ===\n")
                    for stat in top_allocs:
                        sys.stderr.write(f"{stat}\n")
                    sys.stderr.flush()
                except Exception as e:
                    sys.stderr.write(f"Warning: profiler teardown failed: {e}\n")

        if (
            isinstance(result, dict)
            and "mutation_summary" in result
            and isinstance(result["mutation_summary"], dict)
        ):
            banner = result["mutation_summary"].get("human_readable_banner")
            if banner:
                print(banner, file=sys.stderr)
        # Copilot and formats default to human-readable; JSON is opt-in.
        if args.command in {"ask", "query"} and getattr(args, "human", False):
            formatted_text = (
                result.get("formatted_markdown")
                or result.get("markdown")
                or (result.get("result", {}).get("markdown") if isinstance(result.get("result"), dict) else None)
                or json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
            )
            print(formatted_text)
        elif args.command not in {"copilot", "formats"} or getattr(args, "json", False):
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return _result_exit_code(args, result)
    except HITLRequiredError as exc:
        from dual_pipeline.transcript_cli import HITL_EXIT_CODE, _emit_hitl_json
        _emit_hitl_json(exc)
        return HITL_EXIT_CODE
    except Exception as exc:
        return _handle_main_exception(args, exc)


def _handle_main_exception(args: argparse.Namespace, exc: Exception) -> int:
    if args.command == "copilot" and not getattr(args, "json", False):
        print(f"```text\n[Copilot Error] Unexpected failure ({type(exc).__name__}): {exc}\n```")
        return 1
    exc_type_name = type(exc).__name__
    if exc_type_name == "AudioCaptureUnprovenError":
        from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
            EXIT_AUDIO_UNPROVEN,
        )
        try:
            sys.stderr.write("\n")
            sys.stderr.flush()
        except Exception:
            pass
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "error_type": "AudioCaptureUnprovenError",
                    "reason_code": getattr(exc, "reason_code", "UNKNOWN"),
                    "stage": getattr(exc, "stage", "UNKNOWN"),
                    "error": str(exc),
                    "remediation": getattr(exc, "remediation", None),
                },
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return EXIT_AUDIO_UNPROVEN
    if exc_type_name in ("MLXInitError", "MLXRuntimeLost") or (
        isinstance(exc, TimeoutError) and "MLX_READY_DEADLINE_EXCEEDED" in str(exc)
    ):
        from dual_pipeline.live_meeting_recorder_core.mlx_worker import EXIT_MLX_UNREADY
        try:
            sys.stderr.write("\n")
            sys.stderr.flush()
        except Exception:
            pass
        reason_cd = getattr(exc, "reason_code", "MLX_READY_DEADLINE_EXCEEDED" if isinstance(exc, TimeoutError) else "UNKNOWN")
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "error_type": exc_type_name,
                    "reason_code": reason_cd,
                    "stage": getattr(exc, "stage", "WAIT_READY" if isinstance(exc, TimeoutError) else "UNKNOWN"),
                    "error": str(exc),
                },
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return EXIT_MLX_UNREADY
    try:
        sys.stderr.write("\n")
        sys.stderr.flush()
    except Exception:
        pass
    print(
        json.dumps(
            {"status": "FAIL", "error_type": type(exc).__name__, "error": str(exc)},
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        ),
        file=sys.stderr,
    )
    return 1

#!/usr/bin/env python3
"""Run the fail-closed production qualification for NEE-KG-DUAL-PIPELINE-01."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.authorization import (  # noqa: E402
    load_production_authorization_context,
)
from dual_pipeline.core import (  # noqa: E402
    digest,
    exact_file_copy,
    normalize_space,
    read_json,
    sha256_bytes,
    sha256_file,
    utc_now,
    write_json_exclusive,
)
from dual_pipeline.index import HybridRetriever, get_backend  # noqa: E402
from dual_pipeline.intake import INTAKE_STAGES, IntakeEngine  # noqa: E402
from dual_pipeline.l2 import OpenAIProviderFactory, load_l2_config  # noqa: E402
from dual_pipeline.preflight import (  # noqa: E402
    PreflightConfig,
    check_model_manifest,
    check_path_separation,
    run_preflight,
)
from dual_pipeline.query import QUERY_STAGES, QueryEngine  # noqa: E402
from dual_pipeline.release import ReleaseManager  # noqa: E402
from dual_pipeline.semantic_cache import configure_semantic_cache  # noqa: E402
from dual_pipeline.telemetry import RunRecorder  # noqa: E402


def _audit(runtime: Path, run_id: str, required_stages: list[str]) -> dict[str, Any]:
    paths = sorted((runtime / "runs" / run_id / "learning_events").glob("*.json"))
    if not paths:
        raise RuntimeError(f"no learning events found for {run_id}")
    first = read_json(paths[0])
    return RunRecorder(
        runtime,
        run_id=run_id,
        trace_id=first["trace_id"],
        use_case=first["use_case"],
        graph_snapshot=first.get("graph_snapshot"),
        model_or_index_generation=first.get("model_or_index_generation"),
    ).audit(required_stages)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--copilot-package", type=Path, required=True)
    parser.add_argument("--architecture-package", type=Path, required=True)
    parser.add_argument("--pdlc-question-file", type=Path, required=True)
    parser.add_argument("--copilot-decisions", type=Path)
    parser.add_argument("--architecture-decisions", type=Path)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--graph", type=Path, required=True)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--model-revision", required=True)
    parser.add_argument("--model-manifest", required=True)
    parser.add_argument("--auth-token", type=Path, required=True)
    parser.add_argument("--auth-public-key", type=Path, required=True)
    parser.add_argument("--auth-issuer", required=True)
    parser.add_argument("--auth-audience", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    supplied_runtime = args.runtime_root.expanduser().absolute()
    graph = args.graph.resolve()
    report_path = args.report.resolve()
    path_separation = check_path_separation(
        PreflightConfig(
            runtime_root=supplied_runtime,
            graph=graph,
            model_path=Path(args.model_path),
            model_revision=args.model_revision,
            model_manifest=Path(args.model_manifest),
            report=report_path,
        )
    )
    if path_separation["status"] != "PASS":
        parser.error(f"unsafe qualification path layout: {path_separation['error']}")
    runtime = supplied_runtime.resolve()
    if report_path.exists():
        parser.error("--report already exists; production qualification evidence is immutable")
    if runtime.exists() and any(runtime.iterdir()):
        parser.error("--runtime-root must be new or empty so qualification cannot reuse prior evidence")
    runtime.mkdir(parents=True, exist_ok=True)
    configure_semantic_cache(runtime, "write")
    staging = tempfile.TemporaryDirectory(prefix="nee-production-qualification-inputs-")
    staging_root = Path(staging.name)

    def stage(source: Path, name: str) -> Path:
        resolved = source.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"qualification input is not a file: {name}")
        destination = staging_root / name
        exact_file_copy(resolved, destination)
        return destination

    staged_copilot = stage(args.copilot_package, "copilot-package.json")
    staged_architecture = stage(args.architecture_package, "architecture-package.json")
    staged_question = stage(args.pdlc_question_file, "pdlc-question.txt")
    staged_token = stage(args.auth_token, "authorization.jwt")
    staged_public_key = stage(args.auth_public_key, "authorization-public.pem")
    staged_copilot_decisions = (
        stage(args.copilot_decisions, "copilot-decisions.json")
        if args.copilot_decisions
        else None
    )
    staged_architecture_decisions = (
        stage(args.architecture_decisions, "architecture-decisions.json")
        if args.architecture_decisions
        else None
    )
    question = normalize_space(staged_question.read_text(encoding="utf-8"))
    resources = {
        "copilot_package": sha256_file(staged_copilot),
        "architecture_package": sha256_file(staged_architecture),
        "pdlc_question": sha256_bytes(question.encode("utf-8")),
        "release_approval": sha256_bytes(args.approval_id.encode("utf-8")),
    }
    if staged_copilot_decisions:
        resources["copilot_decisions"] = sha256_file(staged_copilot_decisions)
    if staged_architecture_decisions:
        resources["architecture_decisions"] = sha256_file(staged_architecture_decisions)
    graph_hash_before = sha256_file(graph)
    report: dict[str, Any] = {
        "initiative_id": "NEE-KG-DUAL-PIPELINE-01",
        "schema_version": "1.0.0",
        "status": "FAIL",
        "generated_at": utc_now(),
        "production_bge_m3_qualified": False,
        "authorized_supplied_operational_inputs": False,
        "resource_digests": resources,
        "seed_graph_sha256_before": graph_hash_before,
        "offline_controls": {
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "HF_DATASETS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
        },
    }
    os.environ.update(report["offline_controls"])
    manager: ReleaseManager | None = None
    receipt_path: Path | None = None
    authorization = None
    interrupted: BaseException | None = None
    try:
        preflight_path = report_path.with_name(
            f"{report_path.stem}.preflight{report_path.suffix or '.json'}"
        )
        preflight = run_preflight(
            PreflightConfig(
                runtime_root=runtime,
                graph=graph,
                model_path=Path(args.model_path),
                model_revision=args.model_revision,
                model_manifest=Path(args.model_manifest),
                report=preflight_path,
                strict_production=True,
                auth_token=staged_token,
                auth_public_key=staged_public_key,
                auth_issuer=args.auth_issuer,
                auth_audience=args.auth_audience,
                copilot_package=staged_copilot,
                architecture_package=staged_architecture,
                pdlc_question_file=staged_question,
                approval_id=args.approval_id,
                copilot_decisions=staged_copilot_decisions,
                architecture_decisions=staged_architecture_decisions,
            )
        )
        report["preflight_receipt_path"] = str(preflight_path)
        report["preflight_receipt_digest"] = preflight["receipt_digest"]
        if preflight["status"] != "PASS" or not preflight["production_execution_ready"]:
            raise RuntimeError(
                f"strict production preflight failed: {preflight['failed_checks']}"
            )
        authorization = load_production_authorization_context(
            staged_token,
            staged_public_key,
            supplied_issuer=args.auth_issuer,
            supplied_audience=args.auth_audience,
            required_capabilities=(
                "knowledge:intake",
                "knowledge:query",
                "knowledge:release",
                "knowledge:production-qualify",
            ),
        )
        authorization.require_resources(resources)
        backend = get_backend(
            "bge-m3",
            model_path=args.model_path,
            model_revision=args.model_revision,
            model_manifest=args.model_manifest,
        )
        if authorization.approved_model_manifest_digest != backend.model_manifest_digest:
            raise PermissionError(
                "qualification authorization is not bound to the verified model manifest"
            )
        if sha256_file(graph) != graph_hash_before:
            raise RuntimeError("approved seed graph changed after strict preflight")
        manager = ReleaseManager(runtime, graph)
        bootstrap = manager.bootstrap(
            backend, authorization_context=authorization
        )
        retriever = HybridRetriever(manager._resolve(bootstrap["sidecar_path"]), backend)

        def ingest(package: Path, decisions: Path | None) -> dict[str, Any]:
            active_pair = manager.active_pair()
            engine = IntakeEngine(
                manager._resolve(active_pair["graph_path"]),
                runtime,
                retriever=retriever,
                retrieval_audiences=list(authorization.audiences),
                authorization_context=authorization,
                active_pair=active_pair,
            )
            summary = engine.ingest(package.resolve())
            if decisions:
                summary = engine.review(summary["run_id"], decisions.resolve())
            return summary

        copilot = ingest(staged_copilot, staged_copilot_decisions)
        architecture = ingest(staged_architecture, staged_architecture_decisions)
        receipt = manager.prepare_many(
            run_ids=[copilot["run_id"], architecture["run_id"]],
            approval_id=args.approval_id,
            backend=backend,
            authorization_context=authorization,
        )
        receipt_path = runtime / "releases" / f"release-{receipt['next_pair']['generation_id']}.json"
        activation = manager._activate_for_qualification(
            receipt_path, authorization_context=authorization
        )
        l2_config = load_l2_config()
        query = QueryEngine(
            runtime,
            graph,
            backend,
            provider_factory=OpenAIProviderFactory(l2_config),
            l2_config=l2_config,
        ).ask(
            question,
            query_type="opportunity_nba",
            authorization_context=authorization,
        )
        intake_audit = _audit(
            runtime,
            copilot["run_id"],
            INTAKE_STAGES + ["W0", "E0"],
        )
        architecture_intake_audit = _audit(
            runtime,
            architecture["run_id"],
            INTAKE_STAGES + ["W0", "E0"],
        )
        query_audit = _audit(runtime, query["run_id"], QUERY_STAGES)
        rollback = manager._rollback_for_qualification(
            receipt_path, authorization_context=authorization
        )
        final_model_manifest = check_model_manifest(
            Path(args.model_path),
            Path(args.model_manifest),
            args.model_revision,
        )
        checks = {
            "authorization_signature_and_claims": True,
            "authorization_binds_all_inputs": True,
            "authorization_binds_model_manifest": True,
            "strict_production_preflight": preflight["status"] == "PASS"
            and preflight["production_execution_ready"] is True,
            "copilot_intake_complete": copilot["status"] == "COMPLETED"
            and copilot["disposition_count"] == copilot["candidate_count"],
            "architecture_intake_complete": architecture["status"] == "COMPLETED"
            and architecture["disposition_count"] == architecture["candidate_count"],
            "both_intakes_bound_to_release": {
                component["run_id"] for component in receipt["component_runs"]
            }
            == {copilot["run_id"], architecture["run_id"]},
            "production_pair_activated": activation["status"] == "ACTIVE"
            and receipt["production_qualified"] is True,
            "production_query_completed": query["status"] == "COMPLETED"
            and query["production_qualified"] is True,
            "semantic_cache_write_rollout_active": query["semantic_cache"]["mode"]
            == "write",
            "intake_semantic_cache_populated": copilot["semantic_cache"][
                "writeback_count"
            ]
            > 0
            and architecture["semantic_cache"]["writeback_count"] > 0,
            "query_semantic_cache_populated": query["semantic_cache"]["writeback"][
                "entry_id"
            ]
            is not None,
            "query_x3_allowed": query["x3_outcome"] == "X3D_ALLOW_FINISH",
            "intake_audit_complete": intake_audit["status"] == "PASS",
            "architecture_intake_audit_complete": architecture_intake_audit["status"]
            == "PASS",
            "query_audit_complete": query_audit["status"] == "PASS",
            "rollback_complete": rollback["status"] == "ROLLED_BACK",
            "model_snapshot_unchanged": final_model_manifest["status"] == "PASS"
            and final_model_manifest["model_manifest_digest"]
            == backend.model_manifest_digest,
            "seed_graph_unchanged": sha256_file(graph) == graph_hash_before,
        }
        report.update(
            {
                "status": "PASS" if all(checks.values()) else "FAIL",
                "production_bge_m3_qualified": all(checks.values()),
                "authorized_supplied_operational_inputs": True,
                "model_revision": backend.model_revision,
                "model_manifest_digest": backend.model_manifest_digest,
                "final_model_manifest": final_model_manifest,
                "authorization": authorization.audit_record(),
                "checks": checks,
                "copilot_run_id": copilot["run_id"],
                "architecture_run_id": architecture["run_id"],
                "release_receipt_id": receipt["receipt_id"],
                "release_component_run_ids": [
                    component["run_id"] for component in receipt["component_runs"]
                ],
                "query_run_id": query["run_id"],
                "intake_event_chain_digest": intake_audit["event_chain_digest"],
                "copilot_intake_event_chain_digest": intake_audit["event_chain_digest"],
                "architecture_intake_event_chain_digest": architecture_intake_audit[
                    "event_chain_digest"
                ],
                "query_event_chain_digest": query_audit["event_chain_digest"],
                "rollback_id": rollback["rollback_id"],
            }
        )
    except BaseException as exc:
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)
        if not isinstance(exc, Exception):
            interrupted = exc
        if (
            receipt_path is not None
            and authorization is not None
            and manager is not None
        ):
            try:
                failed_receipt = read_json(receipt_path)
                current = manager.active_pair()
                if (
                    current["generation_id"]
                    == failed_receipt["next_pair"]["generation_id"]
                ):
                    rollback = manager._compensate_failed_qualification(
                        receipt_path, authorization_context=authorization
                    )
                    report["failure_rollback"] = rollback["status"]
            except BaseException as rollback_exc:
                report["failure_rollback"] = "FAIL"
                report["failure_rollback_error"] = str(rollback_exc)
    report["seed_graph_sha256_after"] = sha256_file(graph)
    report["report_digest"] = digest(report)
    write_json_exclusive(report_path, report)
    staging.cleanup()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if interrupted is not None:
        raise interrupted
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

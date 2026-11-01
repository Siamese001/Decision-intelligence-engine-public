"""Ingestion execution flow for IntakeEngine."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from dual_pipeline.a2a_finalization import (
    VerifiedA2AFinalization,
    reverify_ingress_finalization_evidence,
    stage_ingress_finalization_evidence,
    verified_finalization_binding,
    verify_finalized_a2a_package_bytes,
)
from dual_pipeline.batch_admission import (
    BATCH_MANIFEST_RESOURCE,
    BatchAdmissionBinding,
    source_kind_for_contract,
    verify_candidate_scope,
    verify_manifest_package,
)
from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import (
    assert_safe_mutable_path,
    digest,
    read_json,
    sha256_bytes,
    sha256_file,
    stable_id,
    write_json,
)
from dual_pipeline.identity import resolve_bundle
from dual_pipeline.intake_core.comparison import (
    compare_candidates,
    hydrate_comparison_context,
)
from dual_pipeline.intake_core.planning import build_ingestion_plan
from dual_pipeline.intake_core.proposals import _correlation
from dual_pipeline.intake_core.routing import build_routes
from dual_pipeline.normalization import normalize_evidence_envelope
from dual_pipeline.source_spans import verify_runtime_a2a_capture
from dual_pipeline.telemetry import RunRecorder


def execute_ingest(
    engine: Any,
    package_path: Path,
    *,
    a2a_finalization_receipt: Path | None = None,
) -> dict[str, Any]:
    production_intake = (
        engine.retriever is not None and engine.retriever.backend.production_qualified
    )
    if production_intake and engine.authorization_context is None:
        raise PermissionError(
            "production intake requires an authenticated authorization context"
        )
    package_bytes = package_path.resolve().read_bytes()
    package = json.loads(package_bytes)
    validate_artifact(package)
    graph_hash_before = sha256_file(engine.graph_path)
    source_kind = source_kind_for_contract(package["contract_id"])
    batch_admission: BatchAdmissionBinding | None = None
    if engine.batch_admission_manifest is not None:
        batch_admission = verify_manifest_package(
            engine.batch_admission_manifest,
            package_path,
            source_kind=source_kind,
            package=package,
        )
    production_binding: dict[str, Any] | None = None
    if production_intake:
        assert engine.authorization_context is not None
        engine.authorization_context.require("knowledge:intake")
        if batch_admission is not None:
            engine.authorization_context.require_resources(
                {BATCH_MANIFEST_RESOURCE: batch_admission.manifest_file_sha256}
            )
        else:
            engine.authorization_context.require_resources(
                {f"{source_kind}_package": sha256_bytes(package_bytes)}
            )
        production_binding = engine._production_binding(graph_hash_before)
        verifier = getattr(engine.retriever.backend, "verify_snapshot", None)
        if callable(verifier):
            verifier()
        run_context = digest({"graph_sha256": graph_hash_before, **production_binding})
    else:
        run_context = graph_hash_before
    a2a_capture_verification: dict[str, Any] | None = None
    a2a_finalization_binding: dict[str, Any] | None = None
    a2a_finalization_proof: VerifiedA2AFinalization | None = None
    a2a_finalization_receipt_bytes: bytes | None = None
    extensions = package.get("extensions", {})
    source_ext = extensions.get("com.partner_advisory.nee.source", {}) if isinstance(extensions, dict) else {}
    context_ext = extensions.get("com.partner_advisory.nee.context", {}) if isinstance(extensions, dict) else {}
    _a2a_ext_keys = ("com.partner_advisory.nee.a2a-evidence-spans", "com.partner_advisory.nee.a2a-source-span-selection", "com.partner_advisory.nee.a2a-packet-candidate-governance")
    _a2a_src_keys = ("a2a_evidence_spans", "a2a_source_span_selection", "source_zip_sha256", "packet_manifest_sha256")
    has_a2a_provenance = (
        source_kind == "a2a_fact_vector_packet"
        or (isinstance(context_ext, dict) and context_ext.get("source_kind") == "a2a_fact_vector_packet")
        or (isinstance(extensions, dict) and any(k in extensions for k in _a2a_ext_keys))
        or (isinstance(source_ext, dict) and any(k in source_ext for k in _a2a_src_keys))
    )
    if source_kind == "a2a_fact_vector_packet" or (source_kind == "evidence_envelope" and has_a2a_provenance):
        selected_span_packet = isinstance(extensions, dict) and (
            extensions.get("com.partner_advisory.nee.a2a-evidence-spans") is not None
            or extensions.get("com.partner_advisory.nee.a2a-source-span-selection") is not None
            or (isinstance(source_ext, dict) and (source_ext.get("a2a_evidence_spans") is not None or source_ext.get("a2a_source_span_selection") is not None))
        )
        if selected_span_packet:
            if a2a_finalization_receipt is None:
                raise ValueError("selected-span A2A intake requires an A2A finalization receipt")
            try:
                a2a_finalization_receipt_bytes = Path(a2a_finalization_receipt).resolve().read_bytes()
            except OSError as exc:
                raise ValueError("A2A finalization receipt is unavailable") from exc
            a2a_finalization_proof = verify_finalized_a2a_package_bytes(package_bytes, a2a_finalization_receipt_bytes, runtime_root=engine.runtime_root)
            a2a_finalization_binding = verified_finalization_binding(a2a_finalization_proof, package=package)
            if a2a_finalization_binding["package_file_sha256"] != sha256_bytes(package_bytes):
                raise RuntimeError("A2A finalization replay is not bound to the U0 package bytes")
            if production_intake:
                assert engine.authorization_context is not None
                engine.authorization_context.require("knowledge:source-attest")
                engine.authorization_context.require_resources({
                    "a2a_finalization_receipt": a2a_finalization_binding["receipt_file_sha256"],
                    "a2a_finalization_archive": a2a_finalization_binding["archive_binding_digest"],
                })
            a2a_capture_verification = verify_runtime_a2a_capture(
                package, capture_root=engine.runtime_root / "source_captures" / a2a_finalization_binding["archive_root_id"]
            )
        else:
            if a2a_finalization_receipt is not None:
                raise ValueError("A2A finalization receipts apply only to selected-span packets")
            a2a_capture_verification = verify_runtime_a2a_capture(package, capture_root=engine.runtime_root / "source_captures")
    elif a2a_finalization_receipt is not None:
        raise ValueError("A2A finalization receipts cannot accompany a non-A2A intake")
    if source_kind not in {"evidence_envelope", "a2a_fact_vector_packet"}:
        raise ValueError(
            f"unsupported source kind {source_kind!r}. All legacy intake mechanisms have been "
            "deleted; the only intake mechanism is the Evidence Envelope JSON format "
            "(urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0)."
        )
    bundle = normalize_evidence_envelope(package, run_context=run_context)
    if not bundle.get("candidates"):
        from dual_pipeline.exceptions import IdempotentNoOpIntakeError
        raise IdempotentNoOpIntakeError("C00 Substantive Content Gate: Source lacks substantive content (0 actionable candidates). Aborting pipeline gracefully to prevent empty graph deltas.")

    if production_intake:
        engine._require_bundle_access(bundle)
    if engine.batch_admission_manifest is not None:
        verify_candidate_scope(engine.batch_admission_manifest, bundle)
    # The audit record is deliberately captured before U0 and then copied
    # into the human-readable summary only after the ledger is sealed.
    intake_authorization = (
        engine.authorization_context.audit_record() if engine.authorization_context else None
    )
    run_dir = engine.runtime_root / "runs" / bundle["run_id"]
    assert_safe_mutable_path(engine.runtime_root, run_dir, label="intake run directory")
    a2a_finalization_reference: dict[str, Any] | None = None
    if a2a_finalization_proof is not None:
        assert a2a_finalization_receipt_bytes is not None
        a2a_finalization_reference = stage_ingress_finalization_evidence(
            runtime_root=engine.runtime_root,
            run_id=bundle["run_id"],
            package_bytes=package_bytes,
            receipt_bytes=a2a_finalization_receipt_bytes,
            verification=a2a_finalization_proof,
        )
    existing_summary = run_dir / "intake_summary.json"
    if existing_summary.is_file():
        result = read_json(existing_summary)
        if result.get("a2a_finalization_binding") != a2a_finalization_binding:
            raise RuntimeError("existing intake finalization receipt binding does not match")
        if result.get("a2a_finalization_reference") != a2a_finalization_reference:
            raise RuntimeError("existing intake finalization evidence reference does not match")
        if a2a_finalization_reference is not None:
            reverified = reverify_ingress_finalization_evidence(
                runtime_root=engine.runtime_root,
                reference=a2a_finalization_reference,
                expected_binding=a2a_finalization_binding,
            )
            a2a_finalization_proof = reverified
        if production_binding is not None:
            payload = engine._validate_production_run(run_dir, result, production_binding)
            if (
                payload.get("package_file_sha256") != sha256_bytes(package_bytes)
                or payload.get("source_kind") != source_kind
            ):
                raise RuntimeError("existing production intake source bytes do not match")
        if result.get("status") == "COMPLETED" and result.get("graph_sha256_before") == graph_hash_before:
            result["idempotent_replay"] = True
            return result
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    assert_safe_mutable_path(engine.runtime_root, run_dir, label="intake run directory")
    write_json(run_dir / "input_package.json", package)
    write_json(run_dir / "candidate_bundle.json", bundle)
    staged_src = package_path.parent / "vector_staging.sqlite"
    if staged_src.is_file():
        shutil.copy2(staged_src, run_dir / "vector_staging.sqlite")
    if engine.active_pair_binding and engine.active_pair_binding.get("graph_sha256") == graph_hash_before:
        graph_snapshot = engine.active_pair_binding["graph_snapshot"]
    else:
        graph_snapshot = stable_id("graph-snapshot", graph_hash_before, length=24)
    recorder = RunRecorder(
        engine.runtime_root, run_id=bundle["run_id"], trace_id=bundle["trace_id"], use_case="intake",
        graph_snapshot=graph_snapshot,
        model_or_index_generation=production_binding["model_or_index_generation"] if production_binding else None,
    )
    recorder.record(
        stage="U0",
        event_type="intake_run",
        payload={
            "intake_id": bundle["intake_id"],
            "source_kind": source_kind,
            "package_digest": package["canonical_digest"]["value"],
            "package_file_sha256": sha256_bytes(package_bytes),
            "batch_admission": (
                batch_admission.audit_record() if batch_admission else None
            ),
            "classification": (
                package.get("meeting", {}).get("classification")
                or package.get("source", {}).get("classification")
            ),
            "validation": "PASS",
            "a2a_runtime_capture_verification": a2a_capture_verification,
            "a2a_finalization_binding": a2a_finalization_binding,
            "a2a_finalization_reference": a2a_finalization_reference,
            "authorization_context_digest": (
                engine.authorization_context.context_digest
                if engine.authorization_context
                else None
            ),
            "intake_authorization": intake_authorization,
            **(
                production_binding
                if production_binding
                else {
                    "production_qualified": False,
                    "embedding_backend": None,
                    "model_id": None,
                    "model_revision": None,
                    "model_manifest_digest": None,
                    "model_or_index_generation": None,
                }
            ),
        },
        correlation=_correlation(source_id=bundle["source_id"]),
        input_digest=package["canonical_digest"]["value"],
    )
    recorder.record(
        stage="U0",
        event_type="source_record",
        payload={
            "source_id": bundle["source_id"],
            "source_version_id": bundle["source_version_id"],
            "evidence_count": len(bundle["evidence"]),
            "candidate_count": len(bundle["candidates"]),
        },
        correlation=_correlation(source_id=bundle["source_id"]),
        record_count=len(bundle["evidence"]),
    )
    for candidate in bundle["candidates"]:
        recorder.record(
            stage="U0",
            event_type="candidate_record",
            payload=candidate,
            correlation=_correlation(
                source_id=bundle["source_id"], candidate_id=candidate["candidate_id"]
            ),
        )

    plan = build_ingestion_plan(bundle)
    write_json(run_dir / "ingestion_plan.json", plan)
    recorder.record(
        stage="L1",
        event_type="pipeline_event",
        payload=plan,
        correlation=_correlation(source_id=bundle["source_id"]),
        record_count=len(plan["candidates"]),
    )

    macro_routes: list[dict[str, Any]] = []
    for candidate in bundle["candidates"]:
        candidate_id = candidate["candidate_id"]
        candidate_type = candidate["candidate_type"]
        macro_route = {
            "candidate_id": candidate_id,
            "candidate_type": candidate_type,
            "source_kind": source_kind,
            "materiality": candidate["materiality"],
            "macro_route": (
                "direct_entity"
                if candidate_type == "architecture_component"
                else "relationship"
                if candidate_type == "architecture_relationship"
                else "action_item"
                if candidate_type == "action"
                else "mention_only"
                if candidate_type == "entity_mention"
                else "open_question"
                if candidate_type == "open_question"
                else "assertion_candidate"
            ),
            "intake_route": "CANDIDATE_ADMISSION",
        }
        macro_routes.append(macro_route)
        recorder.record(
            stage="L0",
            event_type="macro_route_decision",
            payload=macro_route,
            correlation=_correlation(
                source_id=bundle["source_id"], candidate_id=candidate_id
            ),
            route=macro_route["macro_route"],
        )
    write_json(run_dir / "macro_routes.json", macro_routes)

    try:
        from dual_pipeline.ingestion_planning import (
            compile_ingestion_plan_v2,
            load_ingestion_l1_policy,
        )
        policy = load_ingestion_l1_policy({
            "policy_id": "ingestion-l1-control-v1",
            "schema_version": "1.0.0",
            "mode": "off",
            "runtime_default": "off",
            "proposal_only": True,
            "provider_calls_before_l2d": 0,
            "graph_mutations_before_l2d": 0,
            "extensions": {},
        })
        directive_payload = {
            "directive_id": bundle["run_id"],
            "package_hashes": [package["canonical_digest"]["value"]],
        }
        active_pair_payload = engine.active_pair_binding or {
            "generation_id": stable_id("gen", graph_hash_before, length=24),
            "graph_sha256": graph_hash_before,
            "embedding_backend": "bge-m3-dev",
            "model_id": "BAAI/bge-m3",  # noqa: SSOT_EXEMPT
            "model_revision": "5617a9f61b028005a4858fdac845db406aefb181",
        }
        plan_v2 = compile_ingestion_plan_v2(
            bundle=bundle,
            plan_v1=plan,
            directive=directive_payload,
            active_pair=active_pair_payload,
            authorization_scope_digest=digest(intake_authorization or {"status": "authorized"}),
            policy=policy,
        )
        write_json(run_dir / "ingestion_plan_v2.json", plan_v2)
    except Exception:
        pass

    resolutions = resolve_bundle(
        bundle,
        engine.graph_path,
        authorized_audiences=(
            list(engine.authorization_context.audiences)
            if production_intake and engine.authorization_context
            else None
        ),
    )
    write_json(run_dir / "entity_resolutions.json", resolutions)
    candidate_for_mention = {
        mention["mention_id"]: candidate["candidate_id"]
        for candidate in bundle["candidates"]
        for mention in candidate["entity_mentions"]
    }
    for resolution in resolutions:
        recorder.record(
            stage="C0.1",
            event_type="entity_resolution_decision",
            payload=resolution,
            correlation=_correlation(
                source_id=bundle["source_id"],
                candidate_id=candidate_for_mention[resolution["mention_id"]],
            ),
        )

    semantic_cache, cache_bindings, cached_comparisons, cache_observations = (
        engine._prepare_semantic_cache(bundle)
    )
    for observation in cache_observations:
        recorder.record(
            stage="L0",
            event_type="route_decision",
            payload={"semantic_cache": observation},
            correlation=_correlation(
                source_id=bundle["source_id"],
                candidate_id=observation["candidate_id"],
            ),
            route="candidate_comparison",
        )
    comparison = compare_candidates(
        bundle,
        engine.graph_path,
        retriever=engine.retriever,
        retrieval_audiences=engine.retrieval_audiences,
        cached_comparisons=cached_comparisons,
        encoded_queries={
            candidate_id: binding["encoded_query"]
            for candidate_id, binding in cache_bindings.items()
        },
    )
    if production_intake:
        verifier = getattr(engine.retriever.backend, "verify_snapshot", None)
        if callable(verifier):
            verifier()
    write_json(run_dir / "candidate_comparison.json", comparison)
    for candidate_id, value in comparison["comparisons"].items():
        recorder.record(
            stage="C0.2",
            event_type="retrieval_event",
            payload=value,
            correlation=_correlation(source_id=bundle["source_id"], candidate_id=candidate_id),
            model_id=value["model_id"],
            record_count=len(value["semantic_matches"]),
        )
    hydrated = hydrate_comparison_context(comparison, engine.graph_path)
    write_json(run_dir / "governed_context.json", hydrated)
    recorder.record(
        stage="C0.3",
        event_type="pipeline_event",
        payload=hydrated,
        correlation=_correlation(source_id=bundle["source_id"]),
        record_count=hydrated["record_count"],
    )

    routes = build_routes(bundle, resolutions, comparison)
    write_json(run_dir / "route_decisions.json", routes)
    for route in routes:
        recorder.record(
            stage="L0", event_type="route_decision", payload=route,
            correlation=_correlation(source_id=bundle["source_id"], candidate_id=route["subject_id"]),
            route=route["intake_route"],
        )
    ingestion_reasoning_result = None
    if engine.ingestion_reasoning is not None:
        ingestion_reasoning_result = engine.ingestion_reasoning.execute(
            run_dir=run_dir, recorder=recorder, bundle=bundle, plan=plan,
            resolutions=resolutions, comparison=comparison, governed_context=hydrated, routes=routes,
        )
    return engine._finish_dispositions(
        bundle=bundle, routes=routes, resolutions=resolutions, recorder=recorder, decisions=[],
        graph_hash_before=graph_hash_before, source_kind=source_kind, package_file_sha256=sha256_bytes(package_bytes),
        a2a_finalization_binding=a2a_finalization_binding, a2a_finalization_proof=a2a_finalization_proof,
        a2a_finalization_reference=a2a_finalization_reference, production_binding=production_binding,
        batch_admission=(batch_admission.audit_record() if batch_admission else None),
        intake_authorization=intake_authorization, review_authorization=None, review_applied=False,
        ingestion_reasoning=ingestion_reasoning_result, semantic_cache=semantic_cache,
        cache_bindings=cache_bindings, cache_observations=cache_observations, comparison=comparison,
    )

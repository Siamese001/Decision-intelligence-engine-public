"""ReleaseManager implementation."""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path
from typing import Any, Sequence, Union

from ..a2a_finalization import (
    a2a_normalized_content_projection,
    reverify_ingress_finalization_normalized_bundle,
    verified_finalization_binding,
)
from ..assurance import (
    INTERNAL_OPERATIONAL,
    backend_assurance_tier,
    pair_assurance_tier,
)
from ..authorization import AuthorizationContext
from ..batch_admission import BATCH_MANIFEST_RESOURCE, binding_from_audit_record
from ..contracts import validate_artifact
from ..core import (
    ROOT,
    assert_safe_mutable_path,
    digest,
    exact_file_copy,
    file_lock,
    is_link_or_reparse,
    read_json,
    seal_artifact,
    sha256_bytes,
    sha256_file,
    sqlite_integrity,
    stable_id,
    utc_now,
    write_json,
    write_json_exclusive,
)
from ..hash_cache import cached_sha256_file
from ..graph_delta import (
    build_actual_graph_delta,
    build_graph_mutation_summary,
    validate_planned_graph_delta,
)
from ..index import (
    CLASSIFICATION_RANK,
    EmbeddingBackend,
    build_sidecar,
    validate_sidecar,
)
from ..intake import (
    INGESTION_DECISION_LEDGER_ENGLISH_JSON,
    INGESTION_DECISION_LEDGER_JSON,
    INGESTION_DECISION_LEDGER_MARKDOWN,
    render_ingestion_decision_ledger_markdown,
)
from ..label_quality import (
    assert_label_quality_no_regression,
    audit_graph_labels,
)
from ..telemetry import RunRecorder
from .constants import (
    _QUALIFICATION_TRANSITION_MARKER,
    INTAKE_RELEASE_STAGES,
    RUN_ID_PATTERN,
    require_approved_bge_m3_pair,
)
from .helpers import (
    _a2a_intake_authorization_attests_source,
    _correlation,
    _replayed_a2a_payload_governance,
    _seal_receipt,
    _verify_activation_binding,
    _verify_receipt,
    _verify_rollback_binding,
    evaluate_gate30_non_degradation,
    release_action_resources,
)
from .delta_rebase import rebase_component_run
from .models import _PrepareManyState


def _dispatch(name: str, fallback: Any) -> Any:
    mod = sys.modules.get("dual_pipeline.release")
    if mod is not None and hasattr(mod, name):
        return getattr(mod, name)
    return fallback

class ReleaseManager:
    def __init__(self, runtime_root: Path, seed_graph: Path | None = None):
        supplied_runtime_root = Path(runtime_root).expanduser().absolute()
        if is_link_or_reparse(supplied_runtime_root):
            raise RuntimeError("runtime root cannot be a link or reparse point")
        self.runtime_root = supplied_runtime_root.resolve()
        self.effective_runtime_root = self.runtime_root
        if seed_graph is not None:
            self.seed_graph = Path(seed_graph).resolve()
        elif (self.runtime_root / "active_pair.json").is_file():
            from dual_pipeline.runtime_root import resolve_active_pair
            active, active_file = resolve_active_pair(self.runtime_root, enforce_integrity=False)
            self.effective_runtime_root = active_file.parent
            gp = Path(active["graph_path"])
            self.seed_graph = gp if gp.is_absolute() else (active_file.parent / gp).resolve()
        else:
            self.seed_graph = (ROOT / "graph" / "nee_graph.sqlite").resolve()
        self.generations_dir = self.runtime_root / "generations"
        self.releases_dir = self.runtime_root / "releases"
        self.active_path = self.runtime_root / "active_pair.json"
        self.lock_path = self.runtime_root / "active_pair.lock"
        self._validated_pairs: dict[tuple, dict[str, Any]] = {}

    def _assert_mutable_layout(self, *targets: Path) -> None:
        for name in ("generations", "releases", "runs", "ledger", "telemetry"):
            assert_safe_mutable_path(self.runtime_root, self.runtime_root / name, label=f"runtime {name} path")
        for target in targets:
            assert_safe_mutable_path(self.runtime_root, target, label="runtime point-of-use path")

    @staticmethod
    def _verify_backend_snapshot(backend: EmbeddingBackend) -> None:
        if getattr(backend, "retrieval_encoder", None) != "real-bge-m3":
            raise RuntimeError(
                "release/index activation requires the real BGE-M3 retrieval encoder"
            )
        verifier = getattr(backend, "verify_snapshot", None)
        if callable(verifier):
            verifier()
        elif backend.production_qualified and getattr(
            backend, "backend_id", None
        ) == "flagembedding-bge-m3":
            raise RuntimeError(
                "production BGE-M3 backend cannot reverify its immutable snapshot"
            )

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.runtime_root).as_posix()

    def _resolve(self, value: str) -> Path:
        base = getattr(self, "effective_runtime_root", self.runtime_root)
        path = (base / value).resolve()
        if base not in path.parents and path != base:
            raise ValueError("runtime manifest path escapes the runtime root")
        return path

    def _reverify_a2a_component_finalization(
        self,
        *,
        bundle: dict[str, Any],
        summary: dict[str, Any],
        proposed: dict[str, Any] | None,
    ) -> None:
        """Do not let a graph-persisted receipt-shaped dictionary cross release.

        A normal engine review has already replayed the archive, but release
        preparation is the durable graph/sidecar write boundary.  Replay the
        run-retained exact packet and receipt here as well so a direct Python
        caller cannot substitute a constructed ``VerifiedA2AFinalization`` or
        a shape-valid metadata dictionary for retained source evidence.
        """

        source_kind = summary.get("source_kind")
        binding = summary.get("a2a_finalization_binding")
        reference = summary.get("a2a_finalization_reference")
        if binding is None:
            if reference is not None:
                raise RuntimeError("A2A finalization reference has no receipt binding")
            return
        if source_kind != "a2a_fact_vector_packet":
            raise RuntimeError("non-A2A release component has a finalization receipt binding")
        try:
            proof, replayed_bundle = _dispatch("reverify_ingress_finalization_normalized_bundle", reverify_ingress_finalization_normalized_bundle)(
                runtime_root=self.runtime_root,
                reference=reference,
                expected_binding=binding,
            )
            verified_binding = _dispatch("verified_finalization_binding", verified_finalization_binding)(proof)
        except ValueError as exc:
            raise RuntimeError("release cannot reverify A2A finalization evidence") from exc
        if (
            verified_binding != binding
            or verified_binding["package_file_sha256"]
            != summary.get("package_file_sha256")
        ):
            raise RuntimeError("release A2A finalization evidence differs from the intake package")
        try:
            replayed_projection = a2a_normalized_content_projection(replayed_bundle)
            stored_projection = a2a_normalized_content_projection(bundle)
        except ValueError as exc:
            raise RuntimeError("release A2A normalized source content is invalid") from exc
        if stored_projection != replayed_projection:
            raise RuntimeError(
                "release A2A normalized source content differs from the replayed packet"
            )
        if proposed is None:
            return
        payloads = proposed.get("extensions", {}).get(
            "com.partner_advisory.nee.operation-payloads", {}
        )
        if not isinstance(payloads, dict):
            raise RuntimeError("A2A proposed change set has no operation payloads")
        expected_candidates = {
            candidate["candidate_id"]: candidate
            for candidate in replayed_projection["candidates"]
            if isinstance(candidate, dict) and isinstance(candidate.get("candidate_id"), str)
        }
        if len(expected_candidates) != len(replayed_projection["candidates"]):
            raise RuntimeError("replayed A2A packet has invalid candidate identities")
        expected_source = {
            **replayed_projection["source"],
            "source_id": replayed_projection["source_id"],
            "source_version_id": replayed_projection["source_version_id"],
            "source_posture": replayed_projection["context"].get("source_posture"),
            "authority_rank": int(replayed_projection["context"].get("authority_rank", 0)),
            "evidence": replayed_projection["evidence"],
        }
        actual_source = proposed.get("extensions", {}).get("com.partner_advisory.nee.source")
        if actual_source != expected_source:
            raise RuntimeError(
                "A2A proposed change set source and evidence differ from the replayed packet"
            )
        for operation in proposed.get("operations", []):
            payload = payloads.get(operation.get("operation_id"))
            if not isinstance(payload, dict):
                raise RuntimeError("A2A proposed change set has an invalid operation payload")
            candidate = payload.get("candidate")
            candidate_id = operation.get("candidate_id")
            expected_candidate = expected_candidates.get(candidate_id)
            if candidate != expected_candidate:
                raise RuntimeError(
                    "A2A proposed change set candidate content differs from the replayed packet"
                )
            # A source-scoped external-concept entity is a separate graph
            # operation that carries the already checked candidate but no
            # retrieval projection. Its human identity decision is validated
            # by the formal HITL/graph-delta path; source replay governs the
            # assertion operation that can reach retrieval or UWG.
            if operation.get("operation") == "create_entity":
                continue
            governance = payload.get("retrieval_governance")
            if not isinstance(governance, dict) or governance.get("source_kind") != (
                "a2a_fact_vector_packet"
            ):
                raise RuntimeError("A2A proposed change set lost its source-kind governance")
            if governance.get("a2a_finalization_binding") != verified_binding or governance.get(
                "a2a_finalization_reference"
            ) != reference:
                raise RuntimeError(
                    "A2A proposed change set finalization evidence differs from the replayed source"
                )
            expected_governance = _replayed_a2a_payload_governance(
                replayed_projection["candidate_governance"].get(candidate_id),
                reference,
            )
            if governance != expected_governance:
                raise RuntimeError(
                    "A2A proposed change set governance differs from the replayed packet"
                )

    def active_pair(self) -> dict[str, Any]:
        if not self.active_path.is_file():
            raise ValueError("no active graph/index pair; run bootstrap-runtime first")
        from dual_pipeline.runtime_root import resolve_active_pair
        value, active_file = resolve_active_pair(self.runtime_root, enforce_integrity=False)
        self.effective_runtime_root = active_file.parent
        self.validate_pair(value)
        return value

    def validate_pair(self, pair: dict[str, Any]) -> dict[str, Any]:
        require_approved_bge_m3_pair(pair)
        graph_path = self._resolve(pair["graph_path"])
        sidecar_path = self._resolve(pair["sidecar_path"])
        cache_key = (str(graph_path), str(pair.get("graph_sha256")), str(sidecar_path), str(pair.get("sidecar_sha256")), str(pair.get("generation_id")))
        if hasattr(self, "_validated_pairs") and cache_key in self._validated_pairs and graph_path.is_file() and sidecar_path.is_file():
            return dict(self._validated_pairs[cache_key])
        if cached_sha256_file(graph_path) != pair["graph_sha256"]:
            raise RuntimeError("active graph hash mismatch")
        if cached_sha256_file(sidecar_path) != pair["sidecar_sha256"]:
            raise RuntimeError("active sidecar hash mismatch")
        graph_integrity = sqlite_integrity(graph_path)
        sidecar_validation = validate_sidecar(
            sidecar_path,
            expected_generation_id=pair["generation_id"],
            expected_graph_snapshot=pair["graph_snapshot"],
            expected_graph_sha256=pair["graph_sha256"],
        )
        metadata = sidecar_validation["metadata"]
        binding_checks = {
            "embedding_backend": pair["embedding_backend"],
            "model_id": pair["model_id"],
            "model_revision": pair["model_revision"],
            "model_manifest_digest": pair.get("model_manifest_digest") or "not-applicable",
            "production_qualified": "true" if pair["production_qualified"] else "false",
        }
        tier = pair_assurance_tier(pair)
        if tier == INTERNAL_OPERATIONAL or metadata.get("assurance_tier"):
            binding_checks["assurance_tier"] = tier
        if tier == INTERNAL_OPERATIONAL:
            binding_checks["internal_operational_policy_digest"] = pair.get("internal_operational_policy_digest")
        mismatches = {
            key: {"pair": expected, "sidecar": metadata.get(key)}
            for key, expected in binding_checks.items()
            if metadata.get(key) != expected
        }
        if mismatches:
            raise RuntimeError(f"active pair model/backend binding mismatch: {mismatches}")
        if graph_integrity["integrity_check"] != "ok" or graph_integrity["foreign_key_violation_count"]:
            raise RuntimeError("active graph integrity failed")
        label_quality = audit_graph_labels(graph_path)
        if label_quality["status"] == "FAIL":
            raise RuntimeError(f"active graph fails enforced label policy: {label_quality['blocking_codes']}")
        res = {
            "status": "PASS", "graph_integrity": graph_integrity,
            "sidecar_validation": sidecar_validation, "pair_binding_match": True,
            "label_quality": label_quality,
        }
        if hasattr(self, "_validated_pairs"):
            self._validated_pairs[cache_key] = res
        return res

    @staticmethod
    def _require_release_authorization(
        production_qualified: bool,
        authorization_context: AuthorizationContext | None,
        *,
        model_manifest_digest: str | None = None,
        resources: dict[str, str] | None = None,
    ) -> None:
        if not production_qualified:
            return
        if authorization_context is None:
            raise PermissionError("production release requires an authenticated authorization context")
        authorization_context.require("knowledge:release")
        if (
            model_manifest_digest is None
            or authorization_context.approved_model_manifest_digest
            != model_manifest_digest
        ):
            raise PermissionError(
                "production release authorization is not bound to the model manifest"
            )
        if resources:
            authorization_context.require_resources(resources)

    @classmethod
    def _require_receipt_action_authorization(
        cls,
        receipt: dict[str, Any],
        authorization_context: AuthorizationContext | None,
        *,
        action: str,
        qualification_transition_marker: object | None = None,
        additional_resources: dict[str, str] | None = None,
    ) -> None:
        """Authorize an action-bound transition or a private qualification transition.

        Public transitions always require a freshly issued token bound to the
        exact receipt, target generation, and action.  Only the private
        qualification-runner methods can supply the unexported marker that
        permits their already input-bound, still-fresh preparation context.
        """

        production_qualified = receipt["next_pair"]["production_qualified"]
        cls._require_release_authorization(
            production_qualified,
            authorization_context,
            model_manifest_digest=receipt["next_pair"].get(
                "model_manifest_digest"
            ),
        )
        if not production_qualified:
            return
        assert authorization_context is not None
        if qualification_transition_marker is not None and (
            qualification_transition_marker is not _QUALIFICATION_TRANSITION_MARKER
        ):
            raise PermissionError("invalid internal qualification transition marker")
        if qualification_transition_marker is None:
            resources = release_action_resources(receipt, action)
            if additional_resources:
                overlap = set(resources) & set(additional_resources)
                if overlap:
                    raise ValueError(
                        "additional release resources collide with reserved names: "
                        f"{sorted(overlap)}"
                    )
                resources.update(additional_resources)
            authorization_context.require_resources(resources)
            return
        authorization_context.require("knowledge:production-qualify")
        preparation = receipt.get("authorization")
        if not (
            isinstance(preparation, dict)
            and preparation.get("context_digest")
            == authorization_context.context_digest
        ):
            raise PermissionError(
                "qualification transition is not bound to the prepared release context"
            )

    def bootstrap(
        self,
        backend: EmbeddingBackend,
        *,
        authorization_context: AuthorizationContext | None = None,
    ) -> dict[str, Any]:
        self._assert_mutable_layout()
        self._require_release_authorization(
            backend.production_qualified,
            authorization_context,
            model_manifest_digest=backend.model_manifest_digest,
        )
        if backend.production_qualified:
            model_path_value = getattr(backend, "model_path", None)
            if model_path_value:
                model_root = Path(str(model_path_value)).resolve()
                try:
                    self.runtime_root.relative_to(model_root)
                    overlaps = True
                except ValueError:
                    try:
                        model_root.relative_to(self.runtime_root)
                        overlaps = True
                    except ValueError:
                        overlaps = False
                if overlaps:
                    raise RuntimeError(
                        "production runtime root overlaps the immutable BGE-M3 snapshot"
                    )
            release_state = read_json(ROOT / "release_state.json")
            package_manifest = read_json(ROOT / "manifest.json")
            expected_seed_hash = release_state.get("graph_sha256")
            if (
                not isinstance(expected_seed_hash, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_seed_hash)
                or package_manifest.get("baseline", {}).get("database_sha256")
                != expected_seed_hash
                or sha256_file(self.seed_graph) != expected_seed_hash
            ):
                raise RuntimeError(
                    "production bootstrap seed graph does not match the approved repository contract"
                )
        if self.active_path.is_file():
            return self.active_pair()
        self._verify_backend_snapshot(backend)
        seed_hash = sha256_file(self.seed_graph)
        generation_id = stable_id(
            "generation-bootstrap",
            seed_hash,
            backend.backend_id,
            backend.model_revision,
            backend.model_manifest_digest,
            length=28,
        )
        generation_dir = self.generations_dir / generation_id
        graph_path = generation_dir / "graph.sqlite"
        sidecar_path = generation_dir / "sidecar.sqlite"
        if generation_dir.exists():
            raise FileExistsError(f"incomplete immutable bootstrap generation exists: {generation_dir}")
        self._assert_mutable_layout(generation_dir)
        generation_dir.mkdir(parents=True)
        self._assert_mutable_layout(generation_dir)
        exact_file_copy(self.seed_graph, graph_path)
        graph_hash = sha256_file(graph_path)
        graph_snapshot = stable_id("graph-snapshot", graph_hash, length=24)
        index_receipt = build_sidecar(
            graph_path=graph_path,
            sidecar_path=sidecar_path,
            generation_id=generation_id,
            graph_snapshot=graph_snapshot,
            backend=backend,
        )
        self._verify_backend_snapshot(backend)
        pair = {
            "schema_version": "1.0.0",
            "generation_id": generation_id,
            "graph_snapshot": graph_snapshot,
            "graph_sha256": graph_hash,
            "sidecar_sha256": index_receipt["sidecar_sha256"],
            "graph_path": self._relative(graph_path),
            "sidecar_path": self._relative(sidecar_path),
            "embedding_backend": backend.backend_id,
            "model_id": backend.model_id,
            "model_revision": backend.model_revision,
            "model_manifest_digest": backend.model_manifest_digest,
            "production_qualified": backend.production_qualified,
            "assurance_tier": backend_assurance_tier(backend),
            "internal_operational_policy_digest": getattr(
                backend, "internal_operational_policy_digest", None
            ),
            "previous_generation_id": None,
            "activated_at": utc_now(),
            "activation_authorization_context_digest": (
                authorization_context.context_digest
                if authorization_context
                else None
            ),
        }
        self.validate_pair(pair)
        write_json(generation_dir / "pair_manifest.json", pair)
        with file_lock(self.lock_path):
            if self.active_path.exists():
                return self.active_pair()
            write_json(self.active_path, pair)
        return pair

    def prepare(
        self,
        *,
        run_id: str,
        approval_id: str,
        backend: EmbeddingBackend,
        fail_after_operations: int | None = None,
        authorization_context: AuthorizationContext | None = None,
        staged_vector_paths: Sequence[Union[str, Path]] | None = None,
    ) -> dict[str, Any]:
        return self.prepare_many(
            run_ids=[run_id], approval_id=approval_id, backend=backend,
            fail_after_operations=fail_after_operations,
            authorization_context=authorization_context,
            staged_vector_paths=staged_vector_paths,
        )

    def _ensure_preparation_ledger(
        self,
        receipt: dict[str, Any],
        components: list[dict[str, Any]],
    ) -> None:
        """Publish or recover every W0/E0 event bound to a prepared receipt."""

        graph_receipt = receipt["graph_write_receipt"]
        index_receipt = receipt["embedding_generation_receipt"]
        graph_receipt_by_operation = {
            operation["operation_id"]: operation
            for operation in graph_receipt["operations"]
        }
        receipt_components = {
            component["run_id"]: component
            for component in receipt.get("component_runs", [])
        }
        for component in components:
            binding = receipt_components.get(component["run_id"])
            if binding is None:
                raise RuntimeError("prepared receipt omits an intake component")
            bundle = component["bundle"]
            change_set = component.get("approved") or component.get("proposed")
            operations = change_set["operations"] if change_set is not None else []
            if [operation["operation_id"] for operation in operations] != binding[
                "operation_ids"
            ]:
                raise RuntimeError(
                    "prepared receipt operation binding differs from the intake change set"
                )
            recorder = RunRecorder(
                self.runtime_root,
                run_id=component["run_id"],
                trace_id=bundle["trace_id"],
                use_case="intake",
                graph_snapshot=receipt["next_pair"]["graph_snapshot"],
                model_or_index_generation=receipt["next_pair"]["generation_id"],
            )

            existing_events: dict[tuple[str | None, str | None, str | None], list[dict[str, Any]]] = {}
            for event_path in recorder._sorted_event_paths():
                event = read_json(event_path)
                key = (event.get("stage"), event.get("event_type"), event.get("payload_digest"))
                existing_events.setdefault(key, []).append(event)

            def ensure_event(
                *,
                stage: str,
                event_type: str,
                payload: dict[str, Any],
                correlation: dict[str, str | None],
                record_count: int = 1,
                generation_id: str | None = None,
                model_id: str | None = None,
            ) -> None:
                payload_digest = digest(payload)
                key = (stage, event_type, payload_digest)
                matches = existing_events.get(key, [])
                if len(matches) > 1:
                    raise RuntimeError(
                        "prepared release ledger contains duplicate bound events"
                    )
                if not matches:
                    recorded = recorder.record(
                        stage=stage,
                        event_type=event_type,
                        payload=payload,
                        correlation=correlation,
                        graph_snapshot=receipt["next_pair"]["graph_snapshot"],
                        generation_id=generation_id,
                        model_id=model_id,
                        record_count=record_count,
                    )
                    existing_events.setdefault(key, []).append(recorded)

            for operation in operations:
                ensure_event(
                    stage="W0",
                    event_type="graph_change_operation",
                    payload=operation,
                    correlation=_correlation(
                        source_id=bundle["source_id"],
                        candidate_id=operation["candidate_id"],
                        graph_operation_id=operation["operation_id"],
                    ),
                )
            operation_ids = set(binding["operation_ids"])
            component_graph_receipt = dict(graph_receipt)
            component_graph_receipt["change_set_id"] = binding["change_set_id"]
            component_graph_receipt["operation_count"] = len(operation_ids)
            component_graph_receipt["operations"] = [
                graph_receipt_by_operation[operation_id]
                for operation_id in sorted(operation_ids)
            ]
            ensure_event(
                stage="W0",
                event_type="graph_write_receipt",
                payload=component_graph_receipt,
                correlation=_correlation(source_id=bundle["source_id"]),
                record_count=len(operation_ids),
            )
            ensure_event(
                stage="E0",
                event_type="embedding_generation_receipt",
                payload=index_receipt,
                correlation=_correlation(source_id=bundle["source_id"]),
                generation_id=receipt["next_pair"]["generation_id"],
                model_id=receipt["next_pair"]["model_id"],
                record_count=index_receipt["record_count"],
            )
            audit = recorder.audit(INTAKE_RELEASE_STAGES + ["W0", "E0"])
            if audit["status"] != "PASS":
                raise RuntimeError("prepared release ledger recovery did not pass audit")

    def _prepare_stage_validate_inputs(self, state: _PrepareManyState) -> None:
        run_ids = state.run_ids
        approval_id = state.approval_id
        backend = state.backend
        authorization_context = state.authorization_context
        self._assert_mutable_layout()
        from ..retrieval_release_core.preflight import preflight_retrieval_release_schema
        preflight_retrieval_release_schema()
        self._require_release_authorization(
            backend.production_qualified,
            authorization_context,
            model_manifest_digest=backend.model_manifest_digest,
            resources={
                "release_approval": sha256_bytes(approval_id.encode("utf-8"))
            },
        )
        if not run_ids:
            raise ValueError("at least one intake run is required for release preparation")
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("batch release run IDs must be unique")
        runs_root = self.runtime_root / "runs"
        assert_safe_mutable_path(
            self.runtime_root, runs_root, label="runtime runs path"
        )
        components: list[dict[str, Any]] = []

        state.runs_root = runs_root
        state.components = components

    def _prepare_stage_audit_components(self, state: _PrepareManyState) -> None:
        run_ids = state.run_ids
        approval_id = state.approval_id
        backend = state.backend
        authorization_context = state.authorization_context
        runs_root = state.runs_root
        components = state.components
        for run_id in run_ids:
            if not RUN_ID_PATTERN.fullmatch(run_id):
                raise ValueError(f"unsafe intake run ID: {run_id!r}")
            run_dir = runs_root / run_id
            assert_safe_mutable_path(
                self.runtime_root, run_dir, label="intake run directory"
            )
            bundle = read_json(run_dir / "candidate_bundle.json")
            validate_artifact(bundle)
            summary = read_json(run_dir / "intake_summary.json")
            if (
                summary["status"] != "COMPLETED"
                or summary["run_id"] != run_id
                or bundle["run_id"] != run_id
                or summary["trace_id"] != bundle["trace_id"]
                or summary["source_id"] != bundle["source_id"]
                or summary["candidate_count"] != len(bundle["candidates"])
                or summary["candidate_count"] != summary["disposition_count"]
                or summary["graph_sha256_before"] != summary["graph_sha256_after"]
            ):
                raise RuntimeError("release preparation requires a completed intake run")
            # Reject a plainly non-production run before it can cause release
            # preparation to dereference the active graph/index pair.  The
            # sealed U0 payload is checked in full below once the ledger is
            # loaded; this early boundary keeps enterprise-production authorization ahead
            # of graph-dependent work and preserves fail-closed behavior.
            if backend.production_qualified:
                summary_authorization = summary.get("intake_authorization") or summary.get(
                    "authorization"
                )
                required_production_binding = {
                    "production_qualified": True,
                    "embedding_backend": backend.backend_id,
                    "model_id": backend.model_id,
                    "model_revision": backend.model_revision,
                    "model_manifest_digest": backend.model_manifest_digest,
                }
                if (
                    any(
                        summary.get(key) != value
                        for key, value in required_production_binding.items()
                    )
                    or not isinstance(summary_authorization, dict)
                    or not summary_authorization.get("context_digest")
                ):
                    raise PermissionError(
                        "production release requires a production-authenticated intake bound to the BGE-M3 generation"
                    )
            recorder = RunRecorder(
                self.runtime_root,
                run_id=run_id,
                trace_id=bundle["trace_id"],
                use_case="intake",
                graph_snapshot=None,
                model_or_index_generation=None,
            )
            audit = recorder.audit(INTAKE_RELEASE_STAGES)
            intake_prefix = recorder.audit(
                INTAKE_RELEASE_STAGES,
                event_limit=summary["learning_event_count"],
            )
            if (
                audit["status"] != "PASS"
                or intake_prefix["event_chain_digest"]
                != summary["learning_event_chain_digest"]
                or intake_prefix["event_count"] != summary["learning_event_count"]
            ):
                raise RuntimeError("intake learning ledger does not match its completion summary")
            proposed_path = run_dir / "graph_change_set.json"
            proposed = read_json(proposed_path) if proposed_path.is_file() else None
            graph_delta_path = run_dir / "graph_delta_summary.json"
            if not graph_delta_path.is_file():
                raise RuntimeError(
                    "completed intake run lacks the required graph delta exit summary"
                )
            planned_graph_delta = read_json(graph_delta_path)
            if (
                summary.get("graph_delta_summary_path") != graph_delta_path.name
                or summary.get("graph_delta_summary_digest") != digest(planned_graph_delta)
                or summary.get("graph_change_table") != planned_graph_delta.get("table")
            ):
                raise RuntimeError("intake summary does not bind its graph delta exit summary")
            decision_ledger_path = run_dir / INGESTION_DECISION_LEDGER_JSON
            decision_english_path = (
                run_dir / INGESTION_DECISION_LEDGER_ENGLISH_JSON
            )
            decision_table_path = run_dir / INGESTION_DECISION_LEDGER_MARKDOWN
            if (
                summary.get("ingestion_decision_ledger_path")
                != INGESTION_DECISION_LEDGER_JSON
                or summary.get("ingestion_decision_english_path")
                != INGESTION_DECISION_LEDGER_ENGLISH_JSON
                or summary.get("ingestion_decision_table_path")
                != INGESTION_DECISION_LEDGER_MARKDOWN
                or not decision_ledger_path.is_file()
                or not decision_english_path.is_file()
                or not decision_table_path.is_file()
            ):
                raise RuntimeError(
                    "completed intake run lacks the required ingestion decision ledger"
                )
            decision_ledger = read_json(decision_ledger_path)
            decision_explanation = read_json(decision_english_path)
            validate_artifact(decision_explanation)
            if (
                summary.get("ingestion_decision_ledger_sha256")
                != sha256_file(decision_ledger_path)
                or summary.get("ingestion_decision_english_sha256")
                != sha256_file(decision_english_path)
                or summary.get("ingestion_decision_english_canonical_digest")
                != decision_explanation.get("canonical_digest", {}).get("value")
                or summary.get("ingestion_decision_table_sha256")
                != sha256_file(decision_table_path)
                or decision_ledger.get("ledger_type") != "ingestion_decision_ledger"
                or decision_ledger.get("schema_version") != "1.0.0"
                or decision_ledger.get("run_id") != run_id
                or decision_ledger.get("trace_id") != bundle["trace_id"]
                or decision_ledger.get("source_id") != bundle["source_id"]
                or decision_ledger.get("source_version_id") != bundle["source_version_id"]
                or decision_ledger.get("candidate_count") != len(bundle["candidates"])
                or decision_ledger.get("planned_graph_delta_digest")
                != digest(planned_graph_delta)
                or decision_ledger.get("planned_graph_operation_count")
                != planned_graph_delta.get("operation_count")
            ):
                raise RuntimeError(
                    "ingestion decision ledger does not match its completed intake run"
                )
            explanation_bindings = decision_explanation.get("bindings", {})
            explanation_summary = decision_explanation.get("summary", {})
            decision_groups = decision_explanation.get("decision_groups")
            if (
                decision_explanation.get("authority")
                != "derived_explanation_only"
                or decision_explanation.get("mutation_authority") is not False
                or decision_explanation.get("run_id") != run_id
                or decision_explanation.get("trace_id") != bundle["trace_id"]
                or decision_explanation.get("source_id") != bundle["source_id"]
                or decision_explanation.get("source_version_id")
                != bundle["source_version_id"]
                or explanation_bindings.get("technical_ledger_sha256")
                != sha256_file(decision_ledger_path)
                or explanation_bindings.get("technical_ledger_digest")
                != digest(decision_ledger)
                or explanation_bindings.get("graph_delta_digest")
                != digest(planned_graph_delta)
                or explanation_bindings.get("graph_sha256_before")
                != planned_graph_delta.get("graph_sha256_before")
                or explanation_summary.get("candidate_count")
                != len(bundle["candidates"])
                or explanation_summary.get("proposed_graph_operation_count")
                != planned_graph_delta.get("operation_count")
                or not isinstance(decision_groups, list)
                or {
                    candidate_id
                    for group in decision_groups
                    if isinstance(group, dict)
                    for candidate_id in group.get("candidate_ids", [])
                }
                != {candidate["candidate_id"] for candidate in bundle["candidates"]}
                or decision_table_path.read_text(encoding="utf-8")
                != render_ingestion_decision_ledger_markdown(decision_explanation)
            ):
                raise RuntimeError(
                    "plain-English ingestion explanation is incomplete, altered, or unbound"
                )
            decision_rows = decision_ledger.get("rows")
            if (
                not isinstance(decision_rows, list)
                or {
                    row.get("candidate_id")
                    for row in decision_rows
                    if isinstance(row, dict)
                }
                != {candidate["candidate_id"] for candidate in bundle["candidates"]}
                or len(decision_rows) != len(bundle["candidates"])
            ):
                raise RuntimeError(
                    "ingestion decision ledger does not account for every intake candidate"
                )
            ledger_events = [
                read_json(path)
                for path in recorder._sorted_event_paths()
            ]
            intake_run_events = [
                event
                for event in ledger_events
                if event.get("event_type") == "intake_run"
            ]
            if len(intake_run_events) != 1:
                raise RuntimeError("intake ledger has no unique source-package event")
            decision_ledger_events = []
            for event in ledger_events:
                if event.get("event_type") != "pipeline_event" or event.get("stage") != "L4":
                    continue
                payload = read_json(
                    recorder.payload_dir
                    / event["payload_reference"].rsplit("/", 1)[-1]
                )
                if payload.get("review_event_type") == "ingestion_decision_ledger":
                    decision_ledger_events.append(payload)
            expected_decision_ledger_event = {
                "review_event_type": "ingestion_decision_ledger",
                "ledger_type": "ingestion_decision_ledger",
                "ledger_schema_version": "1.0.0",
                "ledger_path": INGESTION_DECISION_LEDGER_JSON,
                "ledger_sha256": summary["ingestion_decision_ledger_sha256"],
                "english_path": INGESTION_DECISION_LEDGER_ENGLISH_JSON,
                "english_sha256": summary["ingestion_decision_english_sha256"],
                "english_canonical_digest": summary[
                    "ingestion_decision_english_canonical_digest"
                ],
                "table_path": INGESTION_DECISION_LEDGER_MARKDOWN,
                "table_sha256": summary["ingestion_decision_table_sha256"],
                "candidate_count": len(bundle["candidates"]),
                "planned_graph_delta_digest": digest(planned_graph_delta),
            }
            if (
                not decision_ledger_events
                or decision_ledger_events[-1] != expected_decision_ledger_event
            ):
                raise RuntimeError(
                    "intake learning ledger does not bind the required ingestion decision ledger"
                )
            intake_payload = read_json(
                recorder.payload_dir
                / intake_run_events[0]["payload_reference"].rsplit("/", 1)[-1]
            )
            if (
                summary.get("package_file_sha256")
                != intake_payload.get("package_file_sha256")
                or summary.get("source_kind") != intake_payload.get("source_kind")
                or summary.get("a2a_finalization_binding")
                != intake_payload.get("a2a_finalization_binding")
                or summary.get("a2a_finalization_reference")
                != intake_payload.get("a2a_finalization_reference")
                or summary.get("batch_admission")
                != intake_payload.get("batch_admission")
                or summary.get("intake_authorization")
                != intake_payload.get("intake_authorization")
                or summary.get("authorization")
                != intake_payload.get("intake_authorization")
            ):
                raise RuntimeError(
                    "intake summary package binding differs from the sealed ledger"
                )
            review_payloads: list[dict[str, Any]] = []
            for event in ledger_events:
                if event.get("event_type") not in {"hitl_decision", "pipeline_event"}:
                    continue
                review_payload = read_json(
                    recorder.payload_dir
                    / event["payload_reference"].rsplit("/", 1)[-1]
                )
                if (
                    event.get("event_type") == "hitl_decision"
                    or (
                        isinstance(review_payload, dict)
                        and review_payload.get("review_event_type")
                        == "hitl_decision_set"
                    )
                ):
                    review_payloads.append(review_payload)
            decisions_path = run_dir / "hitl_decisions.json"
            current_decisions = (
                read_json(decisions_path) if decisions_path.is_file() else []
            )
            change_set_events = [
                event
                for event in ledger_events
                if event.get("event_type") == "graph_change_set"
            ]
            graph_delta_events = []
            for event in ledger_events:
                if event.get("event_type") != "exit_gate_event":
                    continue
                payload = read_json(
                    recorder.payload_dir / event["payload_reference"].rsplit("/", 1)[-1]
                )
                if payload.get("exit_gate") == "graph_delta_summary":
                    graph_delta_events.append((event, payload))
            latest_graph_delta_events = {
                event["stage"]: (event, payload)
                for event, payload in graph_delta_events
            }
            if set(latest_graph_delta_events) != {"X1", "X2", "X3"}:
                raise RuntimeError("intake ledger is missing X1--X3 graph delta exit receipts")
            for _event, payload in latest_graph_delta_events.values():
                if payload.get("graph_delta_summary_digest") != digest(planned_graph_delta):
                    raise RuntimeError("sealed graph delta exit receipt differs from summary")
            x1_event, x1_payload = latest_graph_delta_events["X1"]
            x2_event, x2_payload = latest_graph_delta_events["X2"]
            x3_event, x3_payload = latest_graph_delta_events["X3"]
            if (
                x1_event.get("status") != "PASS"
                or x1_payload.get("schema_and_lineage_valid") is not True
                or x1_payload.get("unauthorized_side_effect_count") != 0
            ):
                raise RuntimeError("intake X1 graph delta exit gate did not pass")
            if (
                x2_event.get("status") != "PASS"
                or x2_payload.get("lane_separation_valid") is not True
            ):
                raise RuntimeError("intake X2 graph delta exit gate did not pass")
            if (
                x3_event.get("status") != "PASS"
                or x3_payload.get("all_operations_accounted_for") is not True
                or x3_payload.get("operation_count")
                != planned_graph_delta.get("operation_count")
            ):
                raise RuntimeError("intake X3 graph delta exit gate did not pass")
            latest_change_set_payload = (
                read_json(
                    recorder.payload_dir
                    / change_set_events[-1]["payload_reference"].rsplit("/", 1)[-1]
                )
                if change_set_events
                else None
            )
            if proposed is not None:
                validate_artifact(proposed)
                if (
                    not change_set_events
                    or change_set_events[-1].get("payload_digest") != digest(proposed)
                    or digest(latest_change_set_payload) != digest(proposed)
                ):
                    raise RuntimeError(
                        "proposed change set does not match the sealed intake ledger"
                    )
                if proposed["run_id"] != run_id or proposed["trace_id"] != bundle["trace_id"]:
                    raise RuntimeError("change set is not bound to its intake run and trace")
                source = proposed["extensions"].get("com.partner_advisory.nee.source")
                if (
                    summary["graph_change_set_id"] != proposed["change_set_id"]
                    or summary["graph_operation_count"] != len(proposed["operations"])
                    or not isinstance(source, dict)
                    or source.get("source_id") != bundle["source_id"]
                    or source.get("source_version_id") != bundle["source_version_id"]
                ):
                    raise RuntimeError(
                        "intake summary, candidate bundle, and change set provenance differ"
                    )
                if proposed["status"] != "proposed" or proposed["approval_id"] is not None:
                    raise ValueError(
                        "release preparation requires an unapproved proposed change set"
                    )
            elif (
                summary["graph_change_set_id"] is not None
                or summary["graph_operation_count"] != 0
                or (
                    change_set_events
                    and (
                        not isinstance(latest_change_set_payload, dict)
                        or latest_change_set_payload.get("status")
                        != "SUPERSEDED_NO_OPERATIONS"
                        or latest_change_set_payload.get("current_change_set_digest")
                        is not None
                    )
                )
            ):
                raise RuntimeError("intake summary declares a missing graph change set")
            self._reverify_a2a_component_finalization(
                bundle=bundle,
                summary=summary,
                proposed=proposed,
            )
            components.append(
                {
                    "run_id": run_id,
                    "run_dir": run_dir,
                    "bundle": bundle,
                    "summary": summary,
                    "intake_payload": intake_payload,
                    "intake_event": intake_run_events[0],
                    "review_payloads": review_payloads,
                    "current_decisions": current_decisions,
                    "proposed": proposed,
                    "planned_graph_delta": planned_graph_delta,
                }
            )
        if backend.production_qualified:
            assert authorization_context is not None
            package_resources: dict[str, str] = {}
            batch_bindings = []
            base_binding = {
                "production_qualified": True,
                "embedding_backend": backend.backend_id,
                "model_id": backend.model_id,
                "model_revision": backend.model_revision,
                "model_manifest_digest": backend.model_manifest_digest,
            }
            for component in components:
                summary = component["summary"]
                intake_payload = component["intake_payload"]
                # The mutable summary must match the U0-sealed copy above;
                # use the sealed payload as the source of authorization facts.
                summary_authorization = intake_payload.get("intake_authorization")
                review_authorization = summary.get("review_authorization")
                expected_decision_count = int(
                    summary.get("hitl", {}).get("decision_count", 0)
                )
                expected_review_event_count = int(
                    summary.get("hitl", {}).get("review_event_count", 0)
                )
                current_review_payloads = (
                    component["review_payloads"][-expected_review_event_count:]
                    if expected_review_event_count
                    else []
                )
                if (
                    any(summary.get(key) != value for key, value in base_binding.items())
                    or any(
                        intake_payload.get(key) != value
                        for key, value in base_binding.items()
                    )
                    or not isinstance(summary_authorization, dict)
                    or not summary_authorization.get("context_digest")
                    or intake_payload.get("authorization_context_digest")
                    != summary_authorization.get("context_digest")
                    or (
                        summary.get("a2a_finalization_binding") is not None
                        and not _a2a_intake_authorization_attests_source(
                            summary_authorization
                        )
                    )
                    or component["intake_event"].get(
                        "model_or_index_generation"
                    )
                    != summary.get("model_or_index_generation")
                    or len(component["review_payloads"])
                    < expected_review_event_count
                    or digest(component["current_decisions"])
                    != summary.get("hitl", {}).get("decisions_digest")
                    or (
                        expected_review_event_count > 0
                        and (
                            not isinstance(review_authorization, dict)
                            or not review_authorization.get("context_digest")
                            or any(
                                payload.get("authorization_context_digest")
                                != review_authorization.get("context_digest")
                                for payload in current_review_payloads
                            )
                            or digest(
                                [
                                    payload.get("decision")
                                    for payload in current_review_payloads
                                    if payload.get("decision") is not None
                                ]
                            )
                            != summary.get("hitl", {}).get("decisions_digest")
                            or len(
                                [
                                    payload
                                    for payload in current_review_payloads
                                    if payload.get("decision") is not None
                                ]
                            )
                            != expected_decision_count
                        )
                    )
                ):
                    raise PermissionError(
                        "production release requires a production-authenticated intake bound to the BGE-M3 generation"
                    )
                batch_value = intake_payload.get("batch_admission")
                if summary.get("batch_admission") != batch_value:
                    raise RuntimeError(
                        "intake summary batch admission differs from sealed intake ledger"
                    )
                if batch_value is not None:
                    batch_bindings.append(binding_from_audit_record(batch_value))
                denied_candidates = [
                    candidate["candidate_id"]
                    for candidate in component["bundle"]["candidates"]
                    if (
                        candidate["classification"] not in CLASSIFICATION_RANK
                        or CLASSIFICATION_RANK[candidate["classification"]]
                        > CLASSIFICATION_RANK[
                            authorization_context.classification_clearance
                        ]
                        or not (
                            set(candidate["audiences"])
                            & set(authorization_context.audiences)
                        )
                    )
                ]
                if denied_candidates:
                    raise PermissionError(
                        "production release authorization does not admit all intake candidates"
                    )
                if batch_value is None:
                    resource_name = f"{component['summary']['source_kind']}_package"
                    package_digest = component["summary"]["package_file_sha256"]
                    if (
                        resource_name in package_resources
                        and package_resources[resource_name] != package_digest
                    ):
                        raise PermissionError(
                            "production batch contains multiple packages for one authorization resource"
                        )
                    package_resources[resource_name] = package_digest
            if batch_bindings:
                if len(batch_bindings) != len(components):
                    raise PermissionError(
                        "production batch cannot mix manifest-authorized and "
                        "legacy package-authorized intakes"
                    )
                first_binding = batch_bindings[0]
                if any(binding != first_binding for binding in batch_bindings[1:]):
                    raise PermissionError(
                        "production release components do not share one batch admission manifest"
                    )
                actual_package_set = digest(
                    sorted(component["summary"]["package_file_sha256"] for component in components)
                )
                if actual_package_set != first_binding.package_set_digest:
                    raise PermissionError(
                        "production release components do not cover the exact "
                        "batch admission manifest"
                    )
                authorization_context.require_resources(
                    {BATCH_MANIFEST_RESOURCE: first_binding.manifest_file_sha256}
                )
            else:
                authorization_context.require_resources(package_resources)
        if not any(component["proposed"] is not None for component in components):
            raise ValueError("release batch has no governed graph operations")

        requested_run_ids = [component["run_id"] for component in components]
        expected_component_bindings: list[dict[str, Any]] = []
        for component in components:
            proposed = component["proposed"]
            approved_replay = None
            if proposed is not None:
                approved_replay = dict(proposed)
                approved_replay["status"] = "approved"
                approved_replay["approval_id"] = approval_id
                approved_replay = seal_artifact(approved_replay)
            expected_component_bindings.append(
                {
                    "change_set_digest": (
                        approved_replay["canonical_digest"]["value"]
                        if approved_replay is not None
                        else None
                    ),
                    "change_set_id": (
                        approved_replay["change_set_id"]
                        if approved_replay is not None
                        else None
                    ),
                    "intake_summary_digest": digest(component["summary"]),
                    "operation_ids": (
                        [
                            operation["operation_id"]
                            for operation in approved_replay["operations"]
                        ]
                        if approved_replay is not None
                        else []
                    ),
                    "run_id": component["run_id"],
                    "source_id": component["bundle"]["source_id"],
                    "source_version_id": component["bundle"]["source_version_id"],
                    "trace_id": component["bundle"]["trace_id"],
                }
            )

        state.requested_run_ids = requested_run_ids
        state.expected_component_bindings = expected_component_bindings

    @staticmethod
    def _load_and_verify_release_receipt(existing_path: Path) -> dict[str, Any]:
        existing = read_json(existing_path)
        if existing.get("schema") == "urn:nee:hygiene-release:1.0.0" and not existing.get("receipt_digest"):
            existing = _seal_receipt(existing)
            existing_path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _verify_receipt(existing)
        return existing

    @staticmethod
    def _matches_prepared_release_receipt(existing: dict[str, Any], state: _PrepareManyState) -> bool:
        existing_components = existing.get("component_runs") or []
        existing_run_ids = [c.get("run_id") for c in existing_components]
        next_pair = existing.get("next_pair") or {}
        backend = state.backend
        return (
            existing.get("status") == "PREPARED"
            and existing.get("approval_id") == state.approval_id
            and existing_run_ids == state.requested_run_ids
            and existing_components == state.expected_component_bindings
            and next_pair.get("embedding_backend") == backend.backend_id
            and next_pair.get("model_id") == backend.model_id
            and next_pair.get("model_revision") == backend.model_revision
            and next_pair.get("model_manifest_digest") == backend.model_manifest_digest
            and bool(existing.get("production_qualified")) == bool(backend.production_qualified)
        )

    def _validate_component_against_active(self, component: dict[str, Any], active: dict[str, Any], backend: EmbeddingBackend) -> None:
        active_graph_path = self._resolve(active["graph_path"])
        rebase_component_run(component, active_graph_path, active)
        validate_planned_graph_delta(
            summary=component["planned_graph_delta"],
            bundle=component["bundle"],
            change_set=component["proposed"],
            graph_path=active_graph_path,
        )
        if backend.production_qualified:
            expected_gen = active["generation_id"]
            if component["summary"].get("model_or_index_generation") != expected_gen or component["intake_payload"].get("model_or_index_generation") != expected_gen:
                raise PermissionError("production intake was not created against the active BGE-M3 generation")
        if component["summary"]["graph_sha256_before"] != active["graph_sha256"]:
            raise RuntimeError(f"intake run is stale relative to the active graph/index pair: {component['run_id']}")
        if component["proposed"] is not None:
            if component["proposed"]["base_graph_hash"] != active["graph_sha256"]:
                raise RuntimeError(f"change set is stale relative to the active graph/index pair: {component['run_id']}")
            if component["proposed"]["base_graph_snapshot"] != active["graph_snapshot"]:
                raise RuntimeError(f"change set snapshot is stale relative to the active graph/index pair: {component['run_id']}")

    def _prepare_stage_check_ledger_and_active_pair(self, state: _PrepareManyState) -> bool:
        backend = state.backend
        components = state.components
        for existing_path in sorted(self.releases_dir.glob("release-*.json")):
            existing = self._load_and_verify_release_receipt(existing_path)
            if self._matches_prepared_release_receipt(existing, state):
                self.validate_pair(existing.get("next_pair") or {})
                self._ensure_preparation_ledger(existing, components)
                state.existing_matching_receipt = existing
                return True
        active = (
            self.bootstrap(backend, authorization_context=state.authorization_context)
            if not self.active_path.exists()
            else self.active_pair()
        )
        backend_binding = {
            "embedding_backend": backend.backend_id,
            "model_id": backend.model_id,
            "model_revision": backend.model_revision,
            "model_manifest_digest": backend.model_manifest_digest,
        }
        backend_mismatches = {
            key: {"active": active.get(key), "backend": value}
            for key, value in backend_binding.items()
            if active.get(key) != value
        }
        if backend_mismatches:
            raise RuntimeError(f"release backend must match the active generation backend: {backend_mismatches}")
        for component in components:
            self._validate_component_against_active(component, active, backend)
        state.active = active
        return False

    def _prepare_stage_reconcile_operations(self, state: _PrepareManyState) -> None:
        components = state.components
        approval_id = state.approval_id
        change_set_ids = [
            component["proposed"]["change_set_id"]
            for component in components
            if component["proposed"] is not None
        ]
        if len(change_set_ids) != len(set(change_set_ids)):
            raise ValueError("batch release change-set IDs must be unique")
        operation_ids: set[str] = set()
        target_ids: set[str] = set()
        for component in components:
            if component["proposed"] is None:
                continue
            for operation in component["proposed"]["operations"]:
                if operation["operation_id"] in operation_ids:
                    raise ValueError("batch release contains a duplicate operation ID")
                if operation["target_id"] in target_ids:
                    raise ValueError("batch release contains a duplicate target ID")
                operation_ids.add(operation["operation_id"])
                target_ids.add(operation["target_id"])

        approved_components: list[dict[str, Any]] = []
        for component in components:
            if component["proposed"] is None:
                approved_components.append({**component, "approved": None})
                continue
            approved_component = dict(component["proposed"])
            approved_component["status"] = "approved"
            approved_component["approval_id"] = approval_id
            approved_component = seal_artifact(approved_component)
            validate_artifact(approved_component)
            write_json(
                component["run_dir"] / "graph_change_set_approved.json",
                approved_component,
            )
            approved_components.append({**component, "approved": approved_component})

        actionable_components = [
            component for component in approved_components if component["approved"] is not None
        ]
        if len(approved_components) == 1:
            approved = approved_components[0]["approved"]
        else:
            combined_operations: list[dict[str, Any]] = []
            combined_payloads: dict[str, dict[str, Any]] = {}
            operation_contexts: dict[str, dict[str, Any]] = {}
            component_bindings: list[dict[str, Any]] = []
            source_versions: dict[str, str] = {}
            for component in approved_components:
                approved_component = component["approved"]
                if approved_component is None:
                    component_bindings.append(
                        {
                            "change_set_digest": None,
                            "change_set_id": None,
                            "intake_summary_digest": digest(component["summary"]),
                            "operation_ids": [],
                            "run_id": component["run_id"],
                            "source_id": component["bundle"]["source_id"],
                            "source_version_id": component["bundle"]["source_version_id"],
                            "trace_id": component["bundle"]["trace_id"],
                        }
                    )
                    continue
                extensions = approved_component["extensions"]
                source = extensions.get("com.partner_advisory.nee.source")
                payloads = extensions.get("com.partner_advisory.nee.operation-payloads")
                if not isinstance(source, dict) or not isinstance(payloads, dict):
                    raise ValueError(
                        "batch component is missing governed source or operation payload bindings"
                    )
                source_version_id = source.get("source_version_id")
                source_digest = digest(source)
                if (
                    source_version_id in source_versions
                    and source_versions[source_version_id] != source_digest
                ):
                    raise ValueError(
                        "batch release source-version identity has conflicting metadata"
                    )
                source_versions[source_version_id] = source_digest
                component_operation_ids: list[str] = []
                for operation in approved_component["operations"]:
                    operation_id = operation["operation_id"]
                    payload = payloads.get(operation_id)
                    if not isinstance(payload, dict):
                        raise ValueError("batch component operation has no governed payload")
                    combined_operation = dict(operation)
                    combined_operation["sequence"] = len(combined_operations) + 1
                    combined_operations.append(combined_operation)
                    combined_payloads[operation_id] = payload
                    operation_contexts[operation_id] = {
                        "change_set_id": approved_component["change_set_id"],
                        "run_id": approved_component["run_id"],
                        "source": source,
                        "trace_id": approved_component["trace_id"],
                    }
                    component_operation_ids.append(operation_id)
                component_bindings.append(
                    {
                        "change_set_digest": approved_component["canonical_digest"]["value"],
                        "change_set_id": approved_component["change_set_id"],
                        "intake_summary_digest": digest(component["summary"]),
                        "operation_ids": component_operation_ids,
                        "run_id": approved_component["run_id"],
                        "source_id": source["source_id"],
                        "source_version_id": source["source_version_id"],
                        "trace_id": approved_component["trace_id"],
                    }
                )
            batch_change_set_id = stable_id(
                "change-set-batch",
                [
                    binding["change_set_digest"] or binding["intake_summary_digest"]
                    for binding in component_bindings
                ],
                approval_id,
                length=28,
            )
            approved = dict(actionable_components[0]["approved"])
            approved.update(
                {
                    "artifact_id": batch_change_set_id,
                    "change_set_id": batch_change_set_id,
                    "run_id": stable_id(
                        "run-release-batch",
                        [value["run_id"] for value in component_bindings],
                        length=24,
                    ),
                    "trace_id": digest(
                        [
                            "release-batch-trace",
                            *[value["trace_id"] for value in component_bindings],
                        ]
                    )[:32],
                    "operations": combined_operations,
                    "extensions": {
                        "com.partner_advisory.nee.component-change-sets": component_bindings,
                        "com.partner_advisory.nee.operation-contexts": operation_contexts,
                        "com.partner_advisory.nee.operation-payloads": combined_payloads,
                        "com.partner_advisory.nee.source": actionable_components[0]["approved"]["extensions"][
                            "com.partner_advisory.nee.source"
                        ],
                    },
                }
            )
            approved = seal_artifact(approved)
            validate_artifact(approved)
            write_json(
                actionable_components[0]["run_dir"] / "graph_change_set_batch_approved.json",
                approved,
            )

        state.approved_components = approved_components
        state.actionable_components = actionable_components
        state.approved = approved

    def _prepare_stage_execute_graph_and_sidecar(self, state: _PrepareManyState) -> None:
        approved_components = state.approved_components
        approved = state.approved
        active = state.active
        backend = state.backend
        fail_after_operations = state.fail_after_operations
        primary = approved_components[0]
        generation_id = stable_id(
            "generation",
            active["generation_id"],
            approved["canonical_digest"]["value"],
            backend.backend_id,
            backend.model_revision,
            backend.model_manifest_digest,
            length=28,
        )
        generation_dir = self.generations_dir / generation_id
        receipt_path = self.releases_dir / f"release-{generation_id}.json"
        if receipt_path.is_file():
            existing = read_json(receipt_path)
            _verify_receipt(existing)
            raise RuntimeError(
                "release receipt identity collides with different current intake bindings"
            )
        if generation_dir.exists():
            raise FileExistsError(
                f"incomplete immutable release generation exists: {generation_dir}"
            )
        self._verify_backend_snapshot(backend)
        self._assert_mutable_layout(generation_dir, receipt_path)
        generation_dir.mkdir(parents=True)
        try:
            self._assert_mutable_layout(generation_dir, receipt_path)
            graph_path = generation_dir / "graph.sqlite"
            sidecar_path = generation_dir / "sidecar.sqlite"
            active_graph_path = self._resolve(active["graph_path"])
            exact_file_copy(active_graph_path, graph_path)
            from ..graph_writer_core import GovernedGraphWriter

            graph_receipt = GovernedGraphWriter(graph_path).apply(
                approved, fail_after_operations=fail_after_operations
            )
            label_quality_validation = assert_label_quality_no_regression(active_graph_path, graph_path)
            actual_graph_delta = build_actual_graph_delta(
                planned_summaries=[component["planned_graph_delta"] for component in approved_components],
                before_graph_path=active_graph_path,
                after_graph_path=graph_path,
            )
            evaluate_gate30_non_degradation(graph_path, None, active_graph_path, None)
            graph_hash = sha256_file(graph_path)
            graph_snapshot = stable_id("graph-snapshot", graph_hash, length=24)
            active_sidecar_path = self._resolve(active["sidecar_path"]) if active.get("sidecar_path") else None
            staged_vector_paths: list[Path] = list(state.staged_vector_paths)
            for comp in approved_components:
                stg = Path(comp["run_dir"]) / "vector_staging.sqlite" if comp.get("run_dir") else None
                if stg and stg.is_file() and stg not in staged_vector_paths:
                    staged_vector_paths.append(stg)
            index_receipt = build_sidecar(
                graph_path=graph_path, sidecar_path=sidecar_path, generation_id=generation_id,
                graph_snapshot=graph_snapshot, backend=backend, prior_sidecar_path=active_sidecar_path,
                staged_vector_paths=staged_vector_paths or None,
            )
            self._verify_backend_snapshot(backend)

            # Gate 30: GS-10 Graph Signal Non-Degradation Evaluation
            graph_signal_evaluation = evaluate_gate30_non_degradation(
                graph_path, sidecar_path, active_graph_path, active_sidecar_path
            )
        except Exception:
            if generation_dir.exists():
                shutil.rmtree(generation_dir, ignore_errors=True)
            raise

        state.primary = primary
        state.generation_id = generation_id
        state.generation_dir = generation_dir
        state.receipt_path = receipt_path
        state.graph_path = graph_path
        state.sidecar_path = sidecar_path
        state.active_graph_path = active_graph_path
        state.graph_receipt = graph_receipt
        state.label_quality_validation = label_quality_validation
        state.actual_graph_delta = actual_graph_delta
        state.graph_hash = graph_hash
        state.graph_snapshot = graph_snapshot
        state.index_receipt = index_receipt
        state.graph_signal_evaluation = graph_signal_evaluation

    def _prepare_stage_seal_and_finalize(self, state: _PrepareManyState) -> None:
        generation_id = state.generation_id
        graph_snapshot = state.graph_snapshot
        graph_hash = state.graph_hash
        index_receipt = state.index_receipt
        graph_path = state.graph_path
        sidecar_path = state.sidecar_path
        backend = state.backend
        active = state.active
        generation_dir = state.generation_dir
        approved_components = state.approved_components
        primary = state.primary
        approval_id = state.approval_id
        approved = state.approved
        graph_receipt = state.graph_receipt
        actual_graph_delta = state.actual_graph_delta
        label_quality_validation = state.label_quality_validation
        authorization_context = state.authorization_context
        receipt_path = state.receipt_path
        next_pair = {
            "schema_version": "1.0.0",
            "generation_id": generation_id,
            "graph_snapshot": graph_snapshot,
            "graph_sha256": graph_hash,
            "sidecar_sha256": index_receipt["sidecar_sha256"],
            "graph_path": self._relative(graph_path),
            "sidecar_path": self._relative(sidecar_path),
            "embedding_backend": backend.backend_id,
            "model_id": backend.model_id,
            "model_revision": backend.model_revision,
            "model_manifest_digest": backend.model_manifest_digest,
            "production_qualified": backend.production_qualified,
            "assurance_tier": backend_assurance_tier(backend),
            "internal_operational_policy_digest": getattr(
                backend, "internal_operational_policy_digest", None
            ),
            "previous_generation_id": active["generation_id"],
            "activated_at": None,
        }
        pair_validation = self.validate_pair(next_pair)
        write_json(generation_dir / "pair_manifest.json", next_pair)
        component_runs = [
            {
                "change_set_digest": (
                    component["approved"]["canonical_digest"]["value"]
                    if component["approved"] is not None
                    else None
                ),
                "change_set_id": (
                    component["approved"]["change_set_id"]
                    if component["approved"] is not None
                    else None
                ),
                "intake_summary_digest": digest(component["summary"]),
                "operation_ids": [
                    operation["operation_id"]
                    for operation in (
                        component["approved"]["operations"]
                        if component["approved"] is not None
                        else []
                    )
                ],
                "run_id": component["run_id"],
                "source_id": component["bundle"]["source_id"],
                "source_version_id": component["bundle"]["source_version_id"],
                "trace_id": component["bundle"]["trace_id"],
            }
            for component in approved_components
        ]
        mutation_summary = build_graph_mutation_summary(
            generation_before=active["generation_id"],
            generation_after=next_pair["generation_id"],
            graph_sha256_before=active["graph_sha256"],
            graph_sha256_after=next_pair["graph_sha256"],
            before_sidecar_path=self.runtime_root / active["sidecar_path"],
            after_sidecar_path=self.runtime_root / next_pair["sidecar_path"],
            sidecar_sha256_before=active.get("sidecar_sha256"),
            sidecar_sha256_after=next_pair.get("sidecar_sha256"),
            actual_graph_delta=actual_graph_delta,
        )
        receipt = _seal_receipt(
            {
                "schema_version": "1.0.0",
                "receipt_id": stable_id("release-receipt", generation_id, length=28),
                "status": "PREPARED",
                "run_id": primary["run_id"],
                "trace_id": primary["bundle"]["trace_id"],
                "source_id": primary["bundle"]["source_id"],
                "approval_id": approval_id,
                "change_set_id": approved["change_set_id"],
                "change_set_digest": approved["canonical_digest"]["value"],
                "component_runs": component_runs,
                "previous_pair": active,
                "next_pair": next_pair,
                "graph_write_receipt": graph_receipt,
                "graph_delta_summary": actual_graph_delta,
                "mutation_summary": mutation_summary,
                "label_quality_validation": label_quality_validation,
                "embedding_generation_receipt": index_receipt,
                "graph_signal_evaluation": state.graph_signal_evaluation,
                "pair_validation": pair_validation,
                "production_qualified": backend.production_qualified,
                "authorization": (
                    authorization_context.audit_record()
                    if authorization_context
                    else None
                ),
                "prepared_at": utc_now(),
            }
        )
        write_json_exclusive(receipt_path, receipt)
        self._ensure_preparation_ledger(receipt, approved_components)

        state.next_pair = next_pair
        state.pair_validation = pair_validation
        state.component_runs = component_runs
        state.receipt = receipt

    def prepare_many(
        self,
        *,
        run_ids: list[str],
        approval_id: str,
        backend: EmbeddingBackend,
        fail_after_operations: int | None = None,
        authorization_context: AuthorizationContext | None = None,
        staged_vector_paths: Sequence[Union[str, Path]] | None = None,
    ) -> dict[str, Any]:
        """Prepare one atomic graph/index generation from one or more intake runs."""
        state = _PrepareManyState(
            run_ids=run_ids, approval_id=approval_id, backend=backend,
            fail_after_operations=fail_after_operations, authorization_context=authorization_context,
            staged_vector_paths=[Path(p).resolve() for p in staged_vector_paths] if staged_vector_paths else [],
        )
        self._prepare_stage_validate_inputs(state)
        self._prepare_stage_audit_components(state)
        if self._prepare_stage_check_ledger_and_active_pair(state):
            assert state.existing_matching_receipt is not None
            return state.existing_matching_receipt
        self._prepare_stage_reconcile_operations(state)
        self._prepare_stage_execute_graph_and_sidecar(state)
        self._prepare_stage_seal_and_finalize(state)
        return state.receipt


    def activate(
        self,
        receipt_path: Path,
        *,
        authorization_context: AuthorizationContext | None = None,
    ) -> dict[str, Any]:
        return self._activate(
            receipt_path, authorization_context=authorization_context, qualification_transition_marker=None
        )

    def _activate_for_qualification(
        self,
        receipt_path: Path,
        *,
        authorization_context: AuthorizationContext,
    ) -> dict[str, Any]:
        """Activate only for the in-process, input-bound qualification runner."""

        return self._activate(
            receipt_path, authorization_context=authorization_context,
            qualification_transition_marker=_QUALIFICATION_TRANSITION_MARKER,
        )

    def _activate(
        self,
        receipt_path: Path,
        *,
        authorization_context: AuthorizationContext | None,
        qualification_transition_marker: object | None,
    ) -> dict[str, Any]:
        from dual_pipeline.l0.ingestion_timing import timed_child_span
        with timed_child_span("activation"):
            self._assert_mutable_layout(receipt_path)
            receipt = read_json(receipt_path)
            _verify_receipt(receipt)
            retrieval_evaluation = None
            if qualification_transition_marker is None:
                from ..retrieval_release import validate_activation_evaluation

                retrieval_evaluation = validate_activation_evaluation(self.runtime_root, receipt)
            self._require_receipt_action_authorization(
                receipt,
                authorization_context,
                action="activate",
            qualification_transition_marker=qualification_transition_marker,
            additional_resources=(
                {
                    "retrieval_release_evaluation": retrieval_evaluation[
                        "evaluation_digest"
                    ]
                }
                if retrieval_evaluation and retrieval_evaluation.get("required")
                else None
            ),
        )
        if receipt["status"] != "PREPARED":
            raise ValueError("only a prepared release receipt can be activated")
        next_pair = dict(receipt["next_pair"])
        self.validate_pair(next_pair)
        component_runs = receipt.get("component_runs") or [
            {
                "run_id": receipt["run_id"],
                "source_id": receipt["source_id"],
                "trace_id": receipt["trace_id"],
            }
        ]
        recorder_bindings: list[tuple[dict[str, Any], RunRecorder]] = []
        for component in component_runs:
            run_id = component["run_id"]
            if not RUN_ID_PATTERN.fullmatch(run_id):
                raise ValueError("release receipt contains an unsafe component run ID")
            bundle_path = (self.runtime_root / "runs" / run_id / "candidate_bundle.json").resolve()
            try:
                bundle_path.relative_to((self.runtime_root / "runs").resolve())
            except ValueError as exc:
                raise ValueError("release component bundle escapes the runtime root") from exc
            bundle = read_json(bundle_path)
            validate_artifact(bundle)
            if (
                bundle["trace_id"] != component["trace_id"]
                or bundle["source_id"] != component["source_id"]
            ):
                raise RuntimeError("release component receipt is not bound to its intake bundle")
            recorder_bindings.append(
                (
                    component,
                    RunRecorder(
                        self.runtime_root,
                        run_id=run_id,
                        trace_id=component["trace_id"],
                        use_case="intake",
                        graph_snapshot=next_pair["graph_snapshot"],
                        model_or_index_generation=next_pair["generation_id"],
                    ),
                )
            )
        activated_at = utc_now()
        next_pair["activated_at"] = activated_at
        activation_id = stable_id("activation", receipt["receipt_id"], length=28)
        mutation_summary = receipt.get("mutation_summary")
        if not mutation_summary and receipt.get("graph_delta_summary"):
            mutation_summary = build_graph_mutation_summary(
                generation_before=receipt["previous_pair"]["generation_id"],
                generation_after=next_pair["generation_id"],
                graph_sha256_before=receipt["previous_pair"]["graph_sha256"],
                graph_sha256_after=next_pair["graph_sha256"],
                before_sidecar_path=(
                    self.runtime_root / receipt["previous_pair"]["sidecar_path"]
                    if receipt.get("previous_pair", {}).get("sidecar_path")
                    else None
                ),
                after_sidecar_path=self.runtime_root / next_pair["sidecar_path"],
                sidecar_sha256_before=receipt["previous_pair"].get("sidecar_sha256"),
                sidecar_sha256_after=next_pair.get("sidecar_sha256"),
                actual_graph_delta=receipt.get("graph_delta_summary"),
            )
        activation = _seal_receipt(
            {
                "schema_version": "1.0.0",
                "activation_id": activation_id,
                "status": "ACTIVE",
                "receipt_id": receipt["receipt_id"],
                "run_id": receipt["run_id"],
                "component_runs": receipt.get("component_runs", []),
                "previous_generation_id": receipt["previous_pair"]["generation_id"],
                "active_pair": next_pair,
                "activated_at": activated_at,
                "mutation_summary": mutation_summary,
                "idempotent": False,
                "authorization": (
                    authorization_context.audit_record()
                    if authorization_context
                    else None
                ),
                "retrieval_release_evaluation": retrieval_evaluation,
            }
        )
        activation_path = self.releases_dir / f"activation-{next_pair['generation_id']}.json"
        self._assert_mutable_layout(activation_path)
        with file_lock(self.lock_path):
            current = self.active_pair()
            if activation_path.exists():
                existing = read_json(activation_path)
                _verify_activation_binding(existing, receipt)
                if current["generation_id"] == receipt["previous_pair"]["generation_id"]:
                    self.validate_pair(existing["active_pair"])
                    write_json(self.active_path, existing["active_pair"])
                elif current["generation_id"] != next_pair["generation_id"]:
                    raise RuntimeError(
                        "durable activation receipt conflicts with the active pair"
                    )
                return existing
            if current["generation_id"] != receipt["previous_pair"]["generation_id"]:
                if current["generation_id"] == next_pair["generation_id"]:
                    restored = dict(receipt["previous_pair"])
                    restored["activated_at"] = utc_now()
                    write_json(self.active_path, restored)
                    recovery_errors: list[str] = []
                    for component, recorder in recorder_bindings:
                        try:
                            recorder.record(
                                stage="E0",
                                event_type="release_activation_aborted",
                                payload={
                                    "status": "ABORTED",
                                    "recovered_incomplete_transition": True,
                                    "activation_id": activation["activation_id"],
                                    "preparation_receipt_digest": receipt[
                                        "receipt_digest"
                                    ],
                                    "attempted_generation_id": next_pair[
                                        "generation_id"
                                    ],
                                    "restored_generation_id": restored[
                                        "generation_id"
                                    ],
                                },
                                correlation=_correlation(
                                    source_id=component["source_id"]
                                ),
                                status="FAIL",
                                graph_snapshot=restored["graph_snapshot"],
                                generation_id=restored["generation_id"],
                                model_id=restored["model_id"],
                            )
                            recorder.seal(
                                INTAKE_RELEASE_STAGES + ["W0", "E0"],
                                _correlation(source_id=component["source_id"]),
                                status="FAIL",
                            )
                        except Exception as recovery_error:
                            recovery_errors.append(type(recovery_error).__name__)
                    raise RuntimeError(
                        "recovered an incomplete activation with no durable receipt"
                        + (
                            f"; ledger compensation errors={recovery_errors}"
                            if recovery_errors
                            else ""
                        )
                    )
                raise RuntimeError("active pair changed after release preparation")
            next_pair["previous_generation_id"] = current["generation_id"]
            attempted_recorders: list[tuple[dict[str, Any], RunRecorder]] = []
            try:
                for component, recorder in recorder_bindings:
                    attempted_recorders.append((component, recorder))
                    recorder.record(
                        stage="E0",
                        event_type="release_receipt",
                        payload=activation,
                        correlation=_correlation(source_id=component["source_id"]),
                        graph_snapshot=next_pair["graph_snapshot"],
                        generation_id=next_pair["generation_id"],
                        model_id=next_pair["model_id"],
                    )
                    recorder.seal(
                        INTAKE_RELEASE_STAGES + ["W0", "E0"],
                        _correlation(source_id=component["source_id"]),
                    )
                write_json_exclusive(activation_path, activation)
                write_json(self.active_path, next_pair)
            except BaseException as activation_error:
                if activation_path.exists():
                    try:
                        published = read_json(activation_path)
                        _verify_activation_binding(published, receipt)
                    except Exception:
                        activation_path.unlink(missing_ok=True)
                restored = dict(receipt["previous_pair"])
                restored["activated_at"] = utc_now()
                write_json(self.active_path, restored)
                compensation_errors: list[str] = []
                for component, recorder in attempted_recorders:
                    try:
                        recorder.record(
                            stage="E0",
                            event_type="release_activation_aborted",
                            payload={
                                "status": "ABORTED",
                                "activation_id": activation["activation_id"],
                                "activation_receipt_digest": activation[
                                    "receipt_digest"
                                ],
                                "attempted_generation_id": next_pair[
                                    "generation_id"
                                ],
                                "restored_generation_id": restored[
                                    "generation_id"
                                ],
                            },
                            correlation=_correlation(
                                source_id=component["source_id"]
                            ),
                            status="FAIL",
                            graph_snapshot=restored["graph_snapshot"],
                            generation_id=restored["generation_id"],
                            model_id=restored["model_id"],
                        )
                        recorder.seal(
                            INTAKE_RELEASE_STAGES + ["W0", "E0"],
                            _correlation(source_id=component["source_id"]),
                            status="FAIL",
                        )
                    except BaseException as compensation_error:
                        compensation_errors.append(type(compensation_error).__name__)
                if compensation_errors:
                    activation_error.add_note(
                        "activation failed and ledger compensation was incomplete: "
                        f"{compensation_errors}"
                    )
                raise
        return activation

    def rollback(
        self,
        receipt_path: Path,
        *,
        authorization_context: AuthorizationContext | None = None,
    ) -> dict[str, Any]:
        return self._rollback(
            receipt_path,
            authorization_context=authorization_context,
            qualification_transition_marker=None,
        )

    def _rollback_for_qualification(
        self,
        receipt_path: Path,
        *,
        authorization_context: AuthorizationContext,
    ) -> dict[str, Any]:
        """Roll back only for the in-process, input-bound qualification runner."""

        return self._rollback(
            receipt_path,
            authorization_context=authorization_context,
            qualification_transition_marker=_QUALIFICATION_TRANSITION_MARKER,
        )

    def _rollback(
        self,
        receipt_path: Path,
        *,
        authorization_context: AuthorizationContext | None,
        qualification_transition_marker: object | None,
    ) -> dict[str, Any]:
        self._assert_mutable_layout(receipt_path)
        receipt = read_json(receipt_path)
        _verify_receipt(receipt)
        self._require_receipt_action_authorization(
            receipt,
            authorization_context,
            action="rollback",
            qualification_transition_marker=qualification_transition_marker,
        )
        previous = receipt["previous_pair"]
        self.validate_pair(previous)
        rollback_path = (
            self.releases_dir
            / f"rollback-{receipt['next_pair']['generation_id']}.json"
        )
        self._assert_mutable_layout(rollback_path)
        with file_lock(self.lock_path):
            current = self.active_pair()
            if rollback_path.exists():
                existing = read_json(rollback_path)
                _verify_rollback_binding(existing, receipt)
                if current["generation_id"] == receipt["next_pair"]["generation_id"]:
                    self.validate_pair(existing["restored_pair"])
                    write_json(self.active_path, existing["restored_pair"])
                elif current["generation_id"] != previous["generation_id"]:
                    raise RuntimeError(
                        "durable rollback receipt conflicts with the active pair"
                    )
                return existing
            if current["generation_id"] == previous["generation_id"]:
                activation_path = (
                    self.releases_dir
                    / f"activation-{receipt['next_pair']['generation_id']}.json"
                )
                if activation_path.exists():
                    activation_receipt = read_json(activation_path)
                    _verify_receipt(activation_receipt)
                    recovered = _seal_receipt(
                        {
                            "schema_version": "1.0.0",
                            "rollback_id": stable_id(
                                "rollback", receipt["receipt_id"], length=28
                            ),
                            "status": "ROLLED_BACK",
                            "receipt_id": receipt["receipt_id"],
                            "run_id": receipt["run_id"],
                            "rolled_back_generation_id": receipt["next_pair"][
                                "generation_id"
                            ],
                            "restored_pair": current,
                            "rolled_back_at": utc_now(),
                            "idempotent": True,
                            "recovered_incomplete_transition": True,
                            "authorization": (
                                authorization_context.audit_record()
                                if authorization_context
                                else None
                            ),
                        }
                    )
                    write_json_exclusive(rollback_path, recovered)
                    return recovered
                return {
                    "status": "ROLLED_BACK",
                    "idempotent": True,
                    "active_pair": current,
                }
            if current["generation_id"] != receipt["next_pair"]["generation_id"]:
                raise RuntimeError("rollback target is not the currently active release")
            restored = dict(previous)
            restored["activated_at"] = utc_now()
            rollback = _seal_receipt(
                {
                    "schema_version": "1.0.0",
                    "rollback_id": stable_id(
                        "rollback", receipt["receipt_id"], length=28
                    ),
                    "status": "ROLLED_BACK",
                    "receipt_id": receipt["receipt_id"],
                    "run_id": receipt["run_id"],
                    "rolled_back_generation_id": receipt["next_pair"][
                        "generation_id"
                    ],
                    "restored_pair": restored,
                    "rolled_back_at": utc_now(),
                    "idempotent": False,
                    "authorization": (
                        authorization_context.audit_record()
                        if authorization_context
                        else None
                    ),
                }
            )
            write_json(self.active_path, restored)
            try:
                write_json_exclusive(rollback_path, rollback)
            except BaseException:
                if rollback_path.exists():
                    published = read_json(rollback_path)
                    _verify_rollback_binding(published, receipt)
                    raise
                write_json(self.active_path, current)
                raise
        return rollback

    def _compensate_failed_qualification(
        self,
        receipt_path: Path,
        *,
        authorization_context: AuthorizationContext,
    ) -> dict[str, Any]:
        """Revert this process's failed qualification even if its JWT just expired.

        This is intentionally private and narrower than operator rollback.  It
        accepts only the exact production-qualification context already sealed
        into both preparation and activation evidence; it cannot target an
        unrelated release or serve as a general expired-token bypass.
        """

        self._assert_mutable_layout(receipt_path)
        receipt = read_json(receipt_path)
        _verify_receipt(receipt)
        if (
            not receipt.get("production_qualified")
            or "knowledge:production-qualify"
            not in authorization_context.capabilities
            or not isinstance(receipt.get("authorization"), dict)
            or receipt["authorization"].get("context_digest")
            != authorization_context.context_digest
            or receipt["next_pair"].get("model_manifest_digest")
            != authorization_context.approved_model_manifest_digest
        ):
            raise PermissionError(
                "qualification compensation is not bound to the prepared release context"
            )
        activation_path = (
            self.releases_dir
            / f"activation-{receipt['next_pair']['generation_id']}.json"
        )
        activation = read_json(activation_path)
        _verify_receipt(activation)
        if (
            not isinstance(activation.get("authorization"), dict)
            or activation["authorization"].get("context_digest")
            != authorization_context.context_digest
            or activation.get("receipt_id") != receipt["receipt_id"]
        ):
            raise PermissionError(
                "qualification compensation is not bound to the activation evidence"
            )
        previous = receipt["previous_pair"]
        self.validate_pair(previous)
        rollback_path = (
            self.releases_dir
            / f"rollback-{receipt['next_pair']['generation_id']}.json"
        )
        self._assert_mutable_layout(rollback_path)
        with file_lock(self.lock_path):
            current = self.active_pair()
            if rollback_path.exists():
                existing = read_json(rollback_path)
                _verify_rollback_binding(existing, receipt)
                if current["generation_id"] == receipt["next_pair"]["generation_id"]:
                    self.validate_pair(existing["restored_pair"])
                    write_json(self.active_path, existing["restored_pair"])
                elif current["generation_id"] != previous["generation_id"]:
                    raise RuntimeError(
                        "durable qualification rollback conflicts with the active pair"
                    )
                return existing
            if current["generation_id"] == previous["generation_id"]:
                return {
                    "status": "ROLLED_BACK",
                    "idempotent": True,
                    "active_pair": current,
                }
            if current["generation_id"] != receipt["next_pair"]["generation_id"]:
                raise RuntimeError(
                    "qualification compensation target is not the active release"
                )
            restored = dict(previous)
            restored["activated_at"] = utc_now()
            rollback = _seal_receipt(
                {
                    "schema_version": "1.0.0",
                    "rollback_id": stable_id(
                        "rollback", receipt["receipt_id"], length=28
                    ),
                    "status": "ROLLED_BACK",
                    "receipt_id": receipt["receipt_id"],
                    "run_id": receipt["run_id"],
                    "rolled_back_generation_id": receipt["next_pair"][
                        "generation_id"
                    ],
                    "restored_pair": restored,
                    "rolled_back_at": utc_now(),
                    "idempotent": False,
                    "compensation_reason": "production_qualification_failed",
                    "authorization": authorization_context.audit_record(),
                }
            )
            write_json(self.active_path, restored)
            try:
                write_json_exclusive(rollback_path, rollback)
            except BaseException:
                if rollback_path.exists():
                    published = read_json(rollback_path)
                    _verify_rollback_binding(published, receipt)
                    raise
                write_json(self.active_path, current)
                raise
        return rollback


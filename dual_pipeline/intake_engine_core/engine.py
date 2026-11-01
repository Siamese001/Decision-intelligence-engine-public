"""IntakeEngine class coordinating ingestion, review, and disposition flows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dual_pipeline.a2a_finalization import VerifiedA2AFinalization
from dual_pipeline.authorization import AuthorizationContext
from dual_pipeline.core import is_link_or_reparse, read_json
from dual_pipeline.index import HybridRetriever
from dual_pipeline.semantic_cache import SemanticCache
from dual_pipeline.telemetry import RunRecorder

from .disposition_flow import finish_dispositions
from .ingest_flow import execute_ingest
from .review_flow import execute_review
from .validation import (
    prepare_semantic_cache,
    production_binding,
    require_bundle_access,
    validate_production_run,
)


class IntakeEngine:
    """Enterprise knowledge graph intake engine."""

    def __init__(
        self,
        graph_path: Path,
        runtime_root: Path,
        *,
        retriever: HybridRetriever | None = None,
        retrieval_audiences: list[str] | None = None,
        authorization_context: AuthorizationContext | None = None,
        active_pair: dict[str, Any] | None = None,
        batch_admission_manifest: Path | None = None,
        ingestion_reasoning: Any | None = None,
        semantic_cache_mode: str | None = None,
    ):
        self.graph_path = graph_path.resolve()
        supplied_runtime_root = Path(runtime_root).expanduser().absolute()
        if is_link_or_reparse(supplied_runtime_root):
            raise RuntimeError("runtime root cannot be a link or reparse point")
        self.runtime_root = supplied_runtime_root.resolve()
        self.retriever = retriever
        self.retrieval_audiences = retrieval_audiences
        self.authorization_context = authorization_context
        self.active_pair_binding = active_pair
        self.ingestion_reasoning = ingestion_reasoning
        self.semantic_cache_mode = semantic_cache_mode
        self.batch_admission_manifest = (
            Path(batch_admission_manifest).expanduser().resolve()
            if batch_admission_manifest is not None
            else None
        )

    def _prepare_semantic_cache(
        self,
        bundle: dict[str, Any],
    ) -> tuple[
        SemanticCache,
        dict[str, dict[str, Any]],
        dict[str, dict[str, Any]],
        list[dict[str, Any]],
    ]:
        return prepare_semantic_cache(self, bundle)

    def _require_bundle_access(self, bundle: dict[str, Any]) -> None:
        require_bundle_access(self, bundle)

    def _production_binding(self, graph_hash: str) -> dict[str, Any]:
        return production_binding(self, graph_hash)

    @staticmethod
    def _validate_production_run(
        run_dir: Path,
        summary: dict[str, Any],
        binding: dict[str, Any],
    ) -> dict[str, Any]:
        return validate_production_run(run_dir, summary, binding)

    def ingest(
        self,
        package_path: Path,
        *,
        a2a_finalization_receipt: Path | None = None,
    ) -> dict[str, Any]:
        return execute_ingest(
            self,
            package_path,
            a2a_finalization_receipt=a2a_finalization_receipt,
        )

    def preview_c0_exceptions(self, package_path: Path) -> dict[str, Any]:
        """Dry-run C0 routing and conflict analysis without mutating graph or run state."""
        from dual_pipeline.contracts import validate_artifact
        from dual_pipeline.core import read_json, sha256_file
        from dual_pipeline.identity import resolve_bundle
        from dual_pipeline.intake import build_dispositions, build_routes
        from dual_pipeline.intake_core.comparison import compare_candidates
        from dual_pipeline.intake_core.hitl import build_shift_left_exception_digest
        from dual_pipeline.normalization import normalize_evidence_envelope

        pkg = read_json(package_path)
        validate_artifact(pkg)
        ghash = sha256_file(self.graph_path)
        bundle = normalize_evidence_envelope(pkg, run_context=ghash)

        resolutions = resolve_bundle(
            bundle,
            self.graph_path,
            authorized_audiences=self.retrieval_audiences,
        )
        comparison = compare_candidates(
            bundle,
            self.graph_path,
            retriever=self.retriever,
            retrieval_audiences=self.retrieval_audiences,
        )
        routes = build_routes(bundle, resolutions, comparison)
        dispositions = build_dispositions(bundle, routes, resolutions, decisions=[])
        return build_shift_left_exception_digest(bundle, routes, resolutions, dispositions, comparison)

    def review(self, run_id: str, decisions_path: Path) -> dict[str, Any]:
        return execute_review(self, run_id, decisions_path)

    def _finish_dispositions(
        self,
        *,
        bundle: dict[str, Any],
        routes: list[dict[str, Any]],
        resolutions: list[dict[str, Any]],
        recorder: RunRecorder,
        decisions: list[dict[str, Any]],
        graph_hash_before: str,
        source_kind: str,
        package_file_sha256: str,
        a2a_finalization_binding: dict[str, Any] | None,
        a2a_finalization_proof: VerifiedA2AFinalization | None,
        a2a_finalization_reference: dict[str, Any] | None,
        production_binding: dict[str, Any] | None,
        batch_admission: dict[str, str] | None,
        intake_authorization: dict[str, Any] | None,
        review_authorization: dict[str, Any] | None,
        review_applied: bool,
        semantic_cache: SemanticCache,
        cache_bindings: dict[str, dict[str, Any]],
        cache_observations: list[dict[str, Any]],
        comparison: dict[str, Any],
        ingestion_reasoning: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return finish_dispositions(
            self,
            bundle=bundle,
            routes=routes,
            resolutions=resolutions,
            recorder=recorder,
            decisions=decisions,
            graph_hash_before=graph_hash_before,
            source_kind=source_kind,
            package_file_sha256=package_file_sha256,
            a2a_finalization_binding=a2a_finalization_binding,
            a2a_finalization_proof=a2a_finalization_proof,
            a2a_finalization_reference=a2a_finalization_reference,
            production_binding=production_binding,
            batch_admission=batch_admission,
            intake_authorization=intake_authorization,
            review_authorization=review_authorization,
            review_applied=review_applied,
            semantic_cache=semantic_cache,
            cache_bindings=cache_bindings,
            cache_observations=cache_observations,
            comparison=comparison,
            ingestion_reasoning=ingestion_reasoning,
        )

    def status(self, run_id: str) -> dict[str, Any]:
        summary_path = self.runtime_root / "runs" / run_id / "intake_summary.json"
        if not summary_path.is_file():
            raise ValueError(f"unknown intake run: {run_id}")
        return read_json(summary_path)

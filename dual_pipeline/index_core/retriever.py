"""Multi-lane hybrid retrieval orchestration and rank fusion."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from dual_pipeline.assurance import (
    INTERNAL_OPERATIONAL,
    backend_assurance_tier,
)
from dual_pipeline.core import connect_sqlite

from .backends import EmbeddingBackend
from .constants import (
    BM25_RRF_WEIGHTS,
    CLASSIFICATION_RANK,
    ENTITY_FACET_RETRIEVAL_MODES,
    LEGACY_RRF_WEIGHTS,
    RETRIEVAL_MODE_RRF_WEIGHTS,
    STRATEGIC_BM25_RRF_WEIGHTS,
)
from .embedding_space_contract import build_query_cache_key
from .facets import retrieve_entity_facets_impl
from .filters import (
    _exact_entity_title_matches,
    _record_ids_matching_object_facets,
    _record_is_in_assertion_scope,
    compute_bm25_ranks,
    find_exact_anchor_record_ids,
    normalize_assertion_scope,
    normalize_exact_anchor_ids,
    normalize_object_facet_filters,
    validate_bm25_assertion_ranks,
)
from .fusion import (
    build_lane_result,
    compute_candidate_union,
    fuse_hybrid_ranks,
    multi_lane_retrieve,
    score_colbert,
)
from .records import resolve_retrieval_mode, retrieval_metadata_from_artifact  # noqa: lazy-decouple
from .retriever_cache import BoundedLRUDict, PrePaddedColbertCache
from .sidecar import validate_sidecar
from .sparse_index import SparseIndexCache, get_global_sparse_index_cache
from .vector_cache import VectorCache, get_global_vector_cache


def _expand_internal_audiences(audiences: Iterable[str], classification: str) -> set[str]:
    req = set(audiences)
    if classification != "public" and "nee-engineering" in req:
        req.add("internal_consulting")
    return req


def _validate_backend_alignment(metadata: dict[str, Any], backend: EmbeddingBackend) -> None:
    expected = {
        "embedding_backend": backend.backend_id,
        "model_id": backend.model_id,
        "model_revision": backend.model_revision,
        "model_manifest_digest": backend.model_manifest_digest or "not-applicable",
        "production_qualified": "true" if backend.production_qualified else "false",
    }
    backend_tier = backend_assurance_tier(backend)
    if backend_tier == INTERNAL_OPERATIONAL or metadata.get("assurance_tier"):
        expected["assurance_tier"] = backend_tier
    internal_policy_digest = getattr(backend, "internal_operational_policy_digest", None)
    if backend_tier == INTERNAL_OPERATIONAL:
        expected["internal_operational_policy_digest"] = internal_policy_digest or ""
    mismatches = {
        key: {"sidecar": metadata.get(key), "backend": value}
        for key, value in expected.items()
        if metadata.get(key) != value
    }
    if mismatches:
        raise RuntimeError(
            f"query backend does not match the active sidecar generation: {mismatches}"
        )


def _resolve_and_validate_encoded_query(
    backend: EmbeddingBackend,
    query: str,
    encoded_query: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[float], dict[str, float], list[list[float]]]:
    encoded = encoded_query if encoded_query is not None else backend.encode(query)
    if (
        not isinstance(encoded, dict)
        or not isinstance(encoded.get("dense"), list)
        or not isinstance(encoded.get("sparse"), dict)
        or not isinstance(encoded.get("colbert"), list)
    ):
        raise ValueError("encoded query has an invalid hybrid representation")
    from .embedding_space_contract import assert_query_vector_contract

    assert_query_vector_contract(encoded["dense"])
    return encoded, encoded["dense"], encoded["sparse"], encoded["colbert"]


class HybridRetriever:
    def __init__(
        self,
        sidecar_path: Path,
        backend: EmbeddingBackend,
        vector_cache: VectorCache | None = None,
        sparse_cache: SparseIndexCache | None = None,
    ):
        self.sidecar_path = sidecar_path.resolve()
        self.backend = backend
        self.vector_cache = vector_cache or get_global_vector_cache()
        self.sparse_cache = sparse_cache or get_global_sparse_index_cache()
        self.validation = validate_sidecar(self.sidecar_path)
        _validate_backend_alignment(self.validation["metadata"], backend)
        self._lock = threading.RLock()
        self._lane_artifacts_cache: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self._colbert_token_cache: BoundedLRUDict[str, np.ndarray] = BoundedLRUDict(maxsize=4096)
        self._colbert_padded_cache = PrePaddedColbertCache()
        self._query_encoding_cache: BoundedLRUDict[
            str, tuple[dict[str, Any], list[float], dict[str, float], list[list[float]]]
        ] = BoundedLRUDict(maxsize=512)

    def retrieve_lane(
        self,
        query: str,
        lane: str,
        *,
        classification: str,
        audiences: list[str],
        dense_limit: int = 100,
        sparse_limit: int = 100,
        bm25_limit: int = 100,
        union_limit: int = 100,
        colbert_limit: int = 100,
        top_k: int = 20,
        rrf_k: int = 60,
        retrieval_mode: str | None = None,
        encoded_query: dict[str, Any] | None = None,
        facet_filters: dict[str, Iterable[str]] | None = None,
        allowed_assertion_ids: Iterable[str] | None = None,
        bm25_assertion_ranks: dict[str, int] | None = None,
        exact_anchor_assertion_ids: Iterable[str] | None = None,
        rrf_weights: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        if lane not in {"approved", "candidate", "external_public"}:
            raise ValueError(f"unsupported retrieval lane: {lane}")
        normalized_facet_filters = normalize_object_facet_filters(facet_filters)
        assertion_scope = normalize_assertion_scope(allowed_assertion_ids)
        exact_anchor_ids = normalize_exact_anchor_ids(exact_anchor_assertion_ids)
        if (
            normalized_facet_filters is not None
            and not self.validation["object_facet_filtering_supported"]
        ):
            raise RuntimeError(
                "facet filtering requested against a legacy sidecar without "
                "controlled-facet metadata"
            )
        resolved_mode, mode_policy = resolve_retrieval_mode(
            [lane], retrieval_mode, allow_lane_subset=True
        )
        allowed_answer_uses = mode_policy[lane]
        if classification not in CLASSIFICATION_RANK:
            raise ValueError(f"unsupported retrieval classification: {classification}")
        if not audiences or any(not value.strip() for value in audiences):
            raise ValueError("at least one nonempty retrieval audience is required")
        if encoded_query is not None:
            encoded, query_dense, query_sparse, query_colbert = _resolve_and_validate_encoded_query(
                self.backend, query, encoded_query
            )
        else:
            cache_key = build_query_cache_key(self.backend, query)
            with self._lock:
                cached = self._query_encoding_cache.get(cache_key)
            if cached is not None:
                encoded, query_dense, query_sparse, query_colbert = cached
            else:
                encoded, query_dense, query_sparse, query_colbert = (
                    _resolve_and_validate_encoded_query(self.backend, query, None)
                )
                with self._lock:
                    self._query_encoding_cache[cache_key] = (
                        encoded,
                        query_dense,
                        query_sparse,
                        query_colbert,
                    )
        validate_bm25_assertion_ranks(bm25_assertion_ranks)
        connection = connect_sqlite(self.sidecar_path, readonly=True)
        try:
            requested_audiences = _expand_internal_audiences(audiences, classification)
            facet_record_ids = (
                _record_ids_matching_object_facets(connection, normalized_facet_filters)
                if normalized_facet_filters is not None
                else None
            )
            authorized_records: dict[str, dict[str, Any]] = {}
            with self._lock:
                if lane not in self._lane_artifacts_cache:
                    self._lane_artifacts_cache[lane] = [
                        (row["record_id"], json.loads(row["artifact_json"]))
                        for row in connection.execute(
                            "SELECT record_id,artifact_json FROM retrieval_record WHERE lane=?",
                            (lane,),
                        )
                    ]
                lane_artifacts = self._lane_artifacts_cache[lane]
            for record_id, artifact in lane_artifacts:
                if facet_record_ids is not None and record_id not in facet_record_ids:
                    continue
                if assertion_scope is not None and not _record_is_in_assertion_scope(
                    artifact,
                    assertion_scope,
                ):
                    continue
                record_classification = artifact["classification"]
                record_audiences = set(artifact["audiences"])
                record_metadata = retrieval_metadata_from_artifact(artifact)
                if resolved_mode in ENTITY_FACET_RETRIEVAL_MODES and (
                    artifact.get("record_type") != "entity_facet"
                    or record_metadata["retrieval_kind"] != "entity_facet"
                ):
                    continue
                # Check provenance quarantine unless explicitly escalated
                # Note: Assuming kwargs escalate or just default block
                is_quarantined = artifact.get("assurance_tier") == "legacy_quarantine"
                if (
                    CLASSIFICATION_RANK[record_classification]
                    <= CLASSIFICATION_RANK[classification]
                    and bool(requested_audiences & record_audiences)
                    and record_metadata["answer_use"] in allowed_answer_uses
                    and not is_quarantined
                ):
                    authorized_records[record_id] = dict(artifact)

            injected_record_ids = find_exact_anchor_record_ids(exact_anchor_ids, authorized_records)
            exact_entity_title_record_ids = (
                _exact_entity_title_matches(query, authorized_records)
                if resolved_mode in ENTITY_FACET_RETRIEVAL_MODES
                else []
            )

            # In-Memory Generational Vector Cache Dense Scoring
            gen_id = self.validation["metadata"].get("generation_id")
            dense_scores = self.vector_cache.score_dense(
                sidecar_path=self.sidecar_path,
                lane=lane,
                query_vector=query_dense,
                authorized_record_ids=set(authorized_records.keys()),
                generation_id=gen_id,
                limit=dense_limit,
            )

            sparse_scores = self.sparse_cache.score_sparse(
                sidecar_path=self.sidecar_path,
                lane=lane,
                query_sparse=query_sparse,
                authorized_record_ids=set(authorized_records.keys()),
                generation_id=gen_id,
                sparse_limit=sparse_limit,
            )

            bm25_ranks = compute_bm25_ranks(bm25_assertion_ranks, authorized_records, bm25_limit)

            preliminary_ranks = {
                "dense": {record_id: index for index, (_, record_id) in enumerate(dense_scores, 1)},
                "sparse": {
                    record_id: index for index, (_, record_id) in enumerate(sparse_scores, 1)
                },
                "bm25": bm25_ranks,
            }
            if rrf_weights is not None:
                weights = rrf_weights
            elif bm25_assertion_ranks is not None:
                weights = RETRIEVAL_MODE_RRF_WEIGHTS.get(resolved_mode, BM25_RRF_WEIGHTS)
            else:
                weights = LEGACY_RRF_WEIGHTS
            preliminary_weights = weights
            admitted = compute_candidate_union(
                preliminary_ranks, preliminary_weights, rrf_k, union_limit
            )

            with self._lock:
                colbert_scores = score_colbert(
                    connection,
                    query_colbert,
                    admitted[:colbert_limit],
                    colbert_cache=self._colbert_token_cache,
                    padded_cache=self._colbert_padded_cache,
                )

            ranks = {
                "dense": {record_id: index for index, (_, record_id) in enumerate(dense_scores, 1)},
                "sparse": {
                    record_id: index for index, (_, record_id) in enumerate(sparse_scores, 1)
                },
                "bm25": bm25_ranks,
                "colbert": {
                    record_id: index for index, (_, record_id) in enumerate(colbert_scores, 1)
                },
            }
            component_scores = {
                "dense": {record_id: score for score, record_id in dense_scores},
                "sparse": {record_id: score for score, record_id in sparse_scores},
                "colbert": {record_id: score for score, record_id in colbert_scores},
            }
            results, _ = fuse_hybrid_ranks(
                admitted=admitted,
                ranks=ranks,
                weights=weights,
                rrf_k=rrf_k,
                injected_record_ids=injected_record_ids,
                exact_entity_title_record_ids=exact_entity_title_record_ids,
                authorized_records=authorized_records,
                lane=lane,
                top_k=top_k,
                component_scores=component_scores,
            )
            return build_lane_result(
                lane=lane,
                resolved_mode=resolved_mode,
                allowed_answer_uses=allowed_answer_uses,
                normalized_facet_filters=normalized_facet_filters,
                facet_filtering_supported=self.validation["object_facet_filtering_supported"],
                encoded=encoded,
                bm25_assertion_ranks=bm25_assertion_ranks,
                exact_anchor_ids=exact_anchor_ids,
                dense_scores=dense_scores,
                sparse_scores=sparse_scores,
                bm25_ranks=bm25_ranks,
                admitted=admitted,
                injected_record_ids=injected_record_ids,
                exact_entity_title_record_ids=exact_entity_title_record_ids,
                colbert_scores=colbert_scores,
                authorized_records=authorized_records,
                weights=weights,
                results=results,
                assertion_scope=assertion_scope,
            )
        finally:
            connection.close()

    def retrieve_entity_facets(
        self,
        query: str,
        lane: str,
        *,
        classification: str,
        audiences: list[str],
        top_k: int = 20,
    ) -> dict[str, Any]:
        """Retrieve only lane-isolated entity facets for graph navigation."""
        return retrieve_entity_facets_impl(
            self,
            query,
            lane,
            classification=classification,
            audiences=audiences,
            top_k=top_k,
        )

    def retrieve(
        self,
        query: str,
        allowed_lanes: list[str],
        *,
        classification: str,
        audiences: list[str],
        retrieval_mode: str | None = None,
        encoded_query: dict[str, Any] | None = None,
        facet_filters: dict[str, Iterable[str]] | None = None,
        allowed_assertion_ids: Iterable[str] | None = None,
        bm25_assertion_ranks_by_lane: dict[str, dict[str, int]] | None = None,
        exact_anchor_assertion_ids_by_lane: dict[str, Iterable[str]] | None = None,
        rrf_weights: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Multi-lane hybrid retrieval orchestration delegating to fusion module."""
        return multi_lane_retrieve(
            self,
            query,
            allowed_lanes,
            classification=classification,
            audiences=audiences,
            retrieval_mode=retrieval_mode,
            encoded_query=encoded_query,
            facet_filters=facet_filters,
            allowed_assertion_ids=allowed_assertion_ids,
            bm25_assertion_ranks_by_lane=bm25_assertion_ranks_by_lane,
            exact_anchor_assertion_ids_by_lane=exact_anchor_assertion_ids_by_lane,
            rrf_weights=rrf_weights,
        )

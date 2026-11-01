"""Unified multi-lane hybrid retrieval and sidecar vector indexing facade.

This module re-exports all indexing, embedding backends, sidecar validation,
facet filtering, and hybrid retrieval components from `dual_pipeline.index_core`.
"""

from __future__ import annotations

from dual_pipeline.index_core import (
    _SHA256_PATTERN,
    APPROVED_VALIDATION_STATES,
    ARTIFACT_ID_PATTERN,
    BGE_M3_MODEL_POLICY,
    BM25_RRF_WEIGHTS,
    STRATEGIC_BM25_RRF_WEIGHTS,
    BoundedLRUDict,
    CLASSIFICATION_RANK,
    COLBERT_DIMENSIONS,
    DEFAULT_INTERNAL_OPERATIONAL_POLICY,
    DENSE_DIMENSIONS,
    DEVELOPMENT,
    EMBEDDING_BATCH_SIZE,
    ENTERPRISE_PRODUCTION,
    ENTITY_FACET_MAX_ASSERTION_TEXT_CHARACTERS,
    ENTITY_FACET_MAX_ASSERTIONS,
    ENTITY_FACET_MAX_TEXT_CHARACTERS,
    ENTITY_FACET_RETRIEVAL_MODES,
    HYBRID_RETRIEVAL_POLICY,
    HYBRID_RETRIEVAL_POLICY_DIGEST,
    INTERNAL_OPERATIONAL,
    LEGACY_DEFAULT_RETRIEVAL_MODE_BY_LANES,
    LEGACY_RRF_WEIGHTS,
    MAX_COLBERT_TOKENS,
    MAX_EXACT_ANCHOR_INJECTIONS_PER_LANE,
    MAX_EXACT_ENTITY_TITLE_MATCHES_PER_LANE,
    OBJECT_FACET_METADATA_KEYS,
    OBJECT_FACET_SCHEMA_VERSION,
    RETRIEVAL_KIND_BY_RECORD_TYPE,
    RETRIEVAL_METADATA_FIELDS,
    RETRIEVAL_METADATA_SCHEMA_VERSION,
    RETRIEVAL_MODE_POLICY,
    ROOT,
    SIDECAR_SCHEMA,
    Any,
    Counter,
    DevelopmentFlagEmbeddingBackend,
    EmbeddingBackend,
    FlagEmbeddingBackend,
    GenerationalRetrieverCache,
    HybridRetriever,
    InternalOperationalFlagEmbeddingBackend,
    Iterable,
    LaneSparseIndex,
    Path,
    PrePaddedColbertCache,
    Protocol,
    SparseIndexCache,
    _audiences,
    _bounded_entity_facet_text,
    _cached_sidecar_validation,
    _classification,
    _contract_reference,
    _dot,
    _exact_entity_title_matches,
    _pack,
    _record_ids_matching_object_facets,
    _record_is_in_assertion_scope,
    _resolve_embedding_device,
    _resolve_embedding_fp16,
    _unpack,
    _validate_sidecar_uncached,
    annotations,
    answer_use_for_profile,
    backend_assurance_tier,
    build_sidecar,
    cached_retriever,
    canonicalize_audiences,
    connect_sqlite,
    copy,
    copy_staged_vectors,
    create_artifact,
    defaultdict,
    digest,
    encode_and_stage_records,
    explicit_strategic_profile,
    get_backend,
    get_global_retriever_cache,
    get_global_sparse_index_cache,
    init_vector_staging_db,
    inspect_staging_store,
    is_link_or_reparse,
    json,
    load_internal_operational_policy,
    lru_cache,
    math,
    normalize_assertion_scope,
    normalize_iso_date,
    normalize_object_facet_filters,
    normalize_space,
    np,
    os,
    policy_digest,
    re,
    read_json,
    render_entity_facet_records,
    render_retrieval_record_object_facets,
    render_retrieval_records,
    resolve_retrieval_mode,
    retrieval_clearance,
    retrieval_metadata_for_record,
    retrieval_metadata_from_artifact,
    retrieval_record_type_for_assertion,
    sha256_bytes,
    sha256_file,
    sqlite_integrity,
    stable_id,
    stage_encoded_vectors,
    struct,
    utc_now,
    validate_artifact,
    validate_bge_model_manifest,
    validate_internal_backend_policy,
    validate_sidecar,
)

__all__ = (
    "APPROVED_VALIDATION_STATES",
    "ARTIFACT_ID_PATTERN",
    "Any",
    "BGE_M3_MODEL_POLICY",
    "BM25_RRF_WEIGHTS",
    "STRATEGIC_BM25_RRF_WEIGHTS",
    "BoundedLRUDict",
    "CLASSIFICATION_RANK",
    "COLBERT_DIMENSIONS",
    "Counter",
    "DEFAULT_INTERNAL_OPERATIONAL_POLICY",
    "DENSE_DIMENSIONS",
    "DEVELOPMENT",
    "DevelopmentFlagEmbeddingBackend",
    "EMBEDDING_BATCH_SIZE",
    "ENTERPRISE_PRODUCTION",
    "ENTITY_FACET_MAX_ASSERTIONS",
    "ENTITY_FACET_MAX_ASSERTION_TEXT_CHARACTERS",
    "ENTITY_FACET_MAX_TEXT_CHARACTERS",
    "ENTITY_FACET_RETRIEVAL_MODES",
    "EmbeddingBackend",
    "FlagEmbeddingBackend",
    "GenerationalRetrieverCache",
    "HYBRID_RETRIEVAL_POLICY",
    "HYBRID_RETRIEVAL_POLICY_DIGEST",
    "HybridRetriever",
    "INTERNAL_OPERATIONAL",
    "InternalOperationalFlagEmbeddingBackend",
    "Iterable",
    "LEGACY_DEFAULT_RETRIEVAL_MODE_BY_LANES",
    "LEGACY_RRF_WEIGHTS",
    "LaneSparseIndex",
    "PrePaddedColbertCache",
    "MAX_COLBERT_TOKENS",
    "MAX_EXACT_ANCHOR_INJECTIONS_PER_LANE",
    "MAX_EXACT_ENTITY_TITLE_MATCHES_PER_LANE",
    "OBJECT_FACET_METADATA_KEYS",
    "OBJECT_FACET_SCHEMA_VERSION",
    "Path",
    "Protocol",
    "RETRIEVAL_KIND_BY_RECORD_TYPE",
    "RETRIEVAL_METADATA_FIELDS",
    "RETRIEVAL_METADATA_SCHEMA_VERSION",
    "RETRIEVAL_MODE_POLICY",
    "ROOT",
    "SIDECAR_SCHEMA",
    "SparseIndexCache",
    "_SHA256_PATTERN",
    "_audiences",
    "_bounded_entity_facet_text",
    "_cached_sidecar_validation",
    "_classification",
    "_contract_reference",
    "_dot",
    "_exact_entity_title_matches",
    "_pack",
    "_record_ids_matching_object_facets",
    "_record_is_in_assertion_scope",
    "_resolve_embedding_device",
    "_resolve_embedding_fp16",
    "_unpack",
    "_validate_sidecar_uncached",
    "annotations",
    "answer_use_for_profile",
    "backend_assurance_tier",
    "build_sidecar",
    "cached_retriever",
    "canonicalize_audiences",
    "connect_sqlite",
    "copy",
    "create_artifact",
    "defaultdict",
    "digest",
    "explicit_strategic_profile",
    "get_backend",
    "get_global_retriever_cache",
    "get_global_sparse_index_cache",
    "is_link_or_reparse",
    "json",
    "load_internal_operational_policy",
    "lru_cache",
    "math",
    "normalize_assertion_scope",
    "normalize_iso_date",
    "normalize_object_facet_filters",
    "normalize_space",
    "np",
    "os",
    "policy_digest",
    "re",
    "read_json",
    "render_entity_facet_records",
    "render_retrieval_record_object_facets",
    "render_retrieval_records",
    "resolve_retrieval_mode",
    "retrieval_clearance",
    "retrieval_metadata_for_record",
    "retrieval_metadata_from_artifact",
    "retrieval_record_type_for_assertion",
    "sha256_bytes",
    "sha256_file",
    "sqlite_integrity",
    "stable_id",
    "struct",
    "utc_now",
    "validate_artifact",
    "validate_bge_model_manifest",
    "validate_internal_backend_policy",
    "validate_sidecar",
    "copy_staged_vectors",
    "encode_and_stage_records",
    "init_vector_staging_db",
    "inspect_staging_store",
    "stage_encoded_vectors",
    "verify_sidecar_binding",
    "score_prefiltered_candidates",
)


def verify_sidecar_binding(
    sidecar_path: Path | str,
    graph_path: Path | str,
) -> dict[str, Any]:
    """Atomically verifies that sidecar.sqlite metadata matches graph.sqlite SHA-256.

    Raises RuntimeError with SIDECAR_GRAPH_HASH_MISMATCH if binding is invalid or mismatched.
    """
    sidecar_p = Path(sidecar_path).resolve()
    graph_p = Path(graph_path).resolve()
    if not sidecar_p.is_file():
        raise FileNotFoundError(f"Sidecar file not found: {sidecar_p}")
    if not graph_p.is_file():
        raise FileNotFoundError(f"Graph file not found: {graph_p}")

    current_graph_sha = sha256_file(graph_p)
    conn = connect_sqlite(sidecar_p, readonly=True)
    try:
        rows = conn.execute("SELECT key, value FROM metadata").fetchall()
        meta = {r[0]: r[1] for r in rows}
    finally:
        conn.close()

    bound_sha = meta.get("bound_graph_sha256") or meta.get("graph_sha256")
    if not bound_sha:
        raise RuntimeError(
            "SIDECAR_GRAPH_HASH_MISMATCH: sidecar metadata lacks graph_sha256 binding"
        )
    if bound_sha != current_graph_sha:
        raise RuntimeError(
            f"SIDECAR_GRAPH_HASH_MISMATCH: sidecar is bound to graph_sha256 '{bound_sha}', "
            f"but current graph.sqlite has '{current_graph_sha}'"
        )
    return {
        "status": "PASS",
        "bound_graph_sha256": bound_sha,
        "current_graph_sha256": current_graph_sha,
        "generation_id": meta.get("generation_id"),
        "record_count": int(meta.get("record_count", 0)),
    }


def score_prefiltered_candidates(
    sidecar_path: Path | str,
    query_vector: Any,
    *,
    allowed_record_ids: Any | None = None,
    lane: str | None = None,
    top_k: int = 20,
    graph_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    """Performs pre-filtered candidate vector scoring against dense vectors in sidecar.sqlite.

    If graph_path is provided, atomically verifies graph hash lockstep before scoring.
    Filters candidate records by allowed_record_ids and/or lane before computing cosine similarity.
    """
    if graph_path is not None:
        verify_sidecar_binding(sidecar_path, graph_path)

    sidecar_p = Path(sidecar_path).resolve()
    conn = connect_sqlite(sidecar_p, readonly=True)
    try:
        q_vec = np.asarray(query_vector, dtype=np.float32)
        q_norm = float(np.linalg.norm(q_vec))
        if q_norm > 0:
            q_vec = q_vec / q_norm

        where_clauses: list[str] = []
        params: list[Any] = []

        if lane is not None:
            where_clauses.append("r.lane = ?")
            params.append(lane)

        if allowed_record_ids is not None:
            allowed_list = list(allowed_record_ids)
            if not allowed_list:
                return []
            phs = ",".join("?" for _ in allowed_list)
            where_clauses.append(f"r.record_id IN ({phs})")
            params.extend(allowed_list)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        query = f"""
            SELECT d.record_id, d.dimensions, d.vector, r.lane, r.artifact_json
            FROM dense_vector d
            JOIN retrieval_record r ON r.record_id = d.record_id
            {where_sql}
        """
        rows = conn.execute(query, params).fetchall()

        scores: list[dict[str, Any]] = []
        for row in rows:
            rid, dim, blob, r_lane, art_json = row[0], row[1], bytes(row[2]), row[3], row[4]
            vec = np.asarray(_unpack(blob), dtype=np.float32)
            v_norm = float(np.linalg.norm(vec))
            sim = float(np.dot(q_vec, vec / v_norm)) if v_norm > 0 else 0.0
            scores.append(
                {
                    "record_id": rid,
                    "lane": r_lane,
                    "score": sim,
                    "artifact": json.loads(art_json) if art_json else {},
                }
            )
        scores.sort(key=lambda x: x["score"], reverse=True)
        return scores[:top_k]
    finally:
        conn.close()

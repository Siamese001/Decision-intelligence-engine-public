"""Scratch shadow unit index builder and geometry validation."""
from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from dual_pipeline.assurance import backend_assurance_tier
from dual_pipeline.core import (
    connect_sqlite,
    digest,
    sha256_file,
    utc_now,
)
from storage.connection import sqlite3

from .backends import EmbeddingBackend
from .constants import (
    BM25_RRF_WEIGHTS,
    DENSE_DIMENSIONS,
    OBJECT_FACET_SCHEMA_VERSION,
    RETRIEVAL_METADATA_SCHEMA_VERSION,
    SIDECAR_SCHEMA,
)
from .differential import populate_sidecar_vectors
from .geometry import compute_geometry_metrics
from .policy import load_semantic_unit_policy
from .records import render_retrieval_record_object_facets
from .sidecar import validate_sidecar
from .unit_renderers import (
    render_entity_profile_records,
    render_evidence_passage_records,
    render_proposition_records,
)


def _load_dense_vectors_by_prefix(
    conn: sqlite3.Connection, prefix: str
) -> dict[str, np.ndarray]:
    rows = conn.execute(
        """SELECT record_id, vector FROM dense_vector
           WHERE record_id LIKE ? ORDER BY record_id""",
        (f"{prefix}%",),
    ).fetchall()
    vectors = {}
    for r in rows:
        v = np.frombuffer(r[1], dtype=np.float32)
        norm = np.linalg.norm(v)
        if norm > 1e-12:
            v = v / norm
        vectors[r[0]] = v
    return vectors


def evaluate_shadow_geometry(sidecar_path: Path) -> dict[str, Any]:
    """Evaluate passage near-duplicate share and profile pairwise cosine on sidecar."""
    conn = connect_sqlite(sidecar_path, readonly=True)
    try:
        # 1. Passages (prefix: 'passage-')
        pass_vecs = _load_dense_vectors_by_prefix(conn, "passage-")
        p_geom = compute_geometry_metrics(list(pass_vecs.values()))

        # 2. Profiles (prefix: 'entity-profile-')
        prof_vecs = _load_dense_vectors_by_prefix(conn, "entity-profile-")
        pr_geom = compute_geometry_metrics(list(prof_vecs.values()))

        return {
            "passage_count": len(pass_vecs),
            "passage_near_duplicate_share": p_geom["near_duplicate_share"],
            "passage_mean_pairwise_cosine": p_geom["mean_pairwise_cos"],
            "passage_near_duplicate_target_met": p_geom["near_duplicate_share"] <= 0.05,
            "profile_count": len(prof_vecs),
            "profile_mean_pairwise_cosine": pr_geom["mean_pairwise_cos"],
            "profile_mean_cosine_target_met": pr_geom["mean_pairwise_cos"] < 0.553,
        }
    finally:
        conn.close()


def build_shadow_unit_index(
    *,
    graph_path: Path,
    base_sidecar_path: Path,
    shadow_dir: Path,
    generation_id: str,
    backend: EmbeddingBackend,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble scratch shadow index holding current corpus and additive semantic units."""
    if policy is None:
        policy = load_semantic_unit_policy()

    shadow_dir.mkdir(parents=True, exist_ok=True)
    shadow_sidecar_path = shadow_dir / "sidecar.sqlite"
    partial_path = shadow_sidecar_path.with_name(f".{shadow_sidecar_path.name}.partial")
    for p in (
        shadow_sidecar_path,
        partial_path,
        partial_path.with_name(f"{partial_path.name}-journal"),
        partial_path.with_name(f"{partial_path.name}-wal"),
        partial_path.with_name(f"{partial_path.name}-shm"),
    ):
        p.unlink(missing_ok=True)

    # 1. Read base records and metadata
    sconn = connect_sqlite(base_sidecar_path, readonly=True)
    try:
        base_meta = {r[0]: r[1] for r in sconn.execute("SELECT key, value FROM metadata")}
        graph_snapshot = base_meta.get("graph_snapshot", "shadow-graph-snapshot")
        base_records = [
            json.loads(r[0])
            for r in sconn.execute("SELECT artifact_json FROM retrieval_record ORDER BY record_id")
        ]
    finally:
        sconn.close()

    # 2. Read graph entities
    gconn = connect_sqlite(graph_path, readonly=True)
    try:
        nodes = {r[0]: r[1] for r in gconn.execute("SELECT node_id, canonical_name FROM node")}
    finally:
        gconn.close()

    # 3. Render additive unit records
    passages = render_evidence_passage_records(graph_path, graph_snapshot, policy=policy)
    propositions = render_proposition_records(graph_path, graph_snapshot, policy=policy)
    asrt_recs = [r for r in base_records if r.get("record_type") == "assertion"]
    profiles = render_entity_profile_records(asrt_recs, entity_names=nodes, graph_snapshot=graph_snapshot)

    all_records = list(base_records) + passages + propositions + profiles
    facet_rows = render_retrieval_record_object_facets(graph_path, all_records)

    started_at = utc_now()
    conn: sqlite3.Connection | None = None
    try:
        from ..storage.governed_context import GovernedStorageContext
        conn = GovernedStorageContext.connect(partial_path)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA mmap_size=268435456")
        conn.executescript(SIDECAR_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")

        conn.executemany(
            """INSERT INTO retrieval_record
               (record_id,lane,record_type,namespace,retrieval_text_hash,
                retrieval_kind,epistemic_status,provenance_kind,answer_use,artifact_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    r["record_id"], r["lane"], r["record_type"], r["namespace"],
                    r["retrieval_text_hash"], r["retrieval_kind"], r["epistemic_status"],
                    r["provenance_kind"], r["answer_use"],
                    json.dumps(r, ensure_ascii=False, sort_keys=True),
                )
                for r in all_records
            ],
        )

        conn.executemany(
            """INSERT INTO retrieval_record_object_facet
               (record_id,assignment_id,object_type,object_id,vocabulary,value,
                assignment_method,review_state)
               VALUES (?,?,?,?,?,?,?,?)""",
            [
                (
                    f["record_id"], f["assignment_id"], f["object_type"], f["object_id"],
                    f["vocabulary"], f["value"], f["assignment_method"], f["review_state"],
                )
                for f in facet_rows
            ],
        )

        _, colbert_dim, reused_cnt, delta_cnt = populate_sidecar_vectors(
            connection=conn,
            records=all_records,
            backend=backend,
            prior_sidecar_path=base_sidecar_path,
        )

        lane_counts = Counter(r["lane"] for r in all_records)
        manifest_rows = [
            {"record_id": r["record_id"], "lane": r["lane"], "retrieval_text_hash": r["retrieval_text_hash"]}
            for r in all_records
        ]
        retrieval_metadata_rows = sorted(
            [
                {
                    "record_id": r["record_id"], "retrieval_kind": r["retrieval_kind"],
                    "epistemic_status": r["epistemic_status"], "provenance_kind": r["provenance_kind"],
                    "answer_use": r["answer_use"],
                }
                for r in all_records
            ],
            key=lambda row: row["record_id"],
        )

        metadata = {
            "schema_version": "1.0.0",
            "retrieval_metadata_schema_version": RETRIEVAL_METADATA_SCHEMA_VERSION,
            "generation_id": generation_id,
            "graph_snapshot": graph_snapshot,
            "graph_sha256": sha256_file(graph_path),
            "model_id": backend.model_id,
            "model_revision": backend.model_revision,
            "model_manifest_digest": backend.model_manifest_digest or "not-applicable",
            "embedding_backend": backend.backend_id,
            "production_qualified": "true" if backend.production_qualified else "false",
            "assurance_tier": backend_assurance_tier(backend),
            "internal_operational_policy_digest": getattr(backend, "internal_operational_policy_digest", None) or "not-applicable",
            "dense_dimensions": str(DENSE_DIMENSIONS),
            "colbert_dimensions": str(colbert_dim),
            "record_count": str(len(all_records)),
            "approved_record_count": str(lane_counts.get("approved", 0)),
            "candidate_record_count": str(lane_counts.get("candidate", 0)),
            "external_public_record_count": str(lane_counts.get("external_public", 0)),
            "retrieval_text_manifest_digest": digest(manifest_rows),
            "retrieval_metadata_manifest_digest": digest(retrieval_metadata_rows),
            "object_facet_schema_version": OBJECT_FACET_SCHEMA_VERSION,
            "object_facet_assignment_count": str(len(facet_rows)),
            "object_facet_manifest_digest": digest(facet_rows),
            "retrieval_configuration_digest": digest(
                {
                    "dense_weight": BM25_RRF_WEIGHTS["dense"],
                    "sparse_weight": BM25_RRF_WEIGHTS["sparse"],
                    "bm25_weight": BM25_RRF_WEIGHTS["bm25"],
                    "colbert_weight": BM25_RRF_WEIGHTS["colbert"],
                    "rrf_k": 60,
                    "lane_blending": False,
                    "entity_title_exact_match": True,
                }
            ),
            "built_at": started_at,
        }
        conn.executemany("INSERT INTO metadata VALUES (?,?)", sorted(metadata.items()))
        conn.commit()
        conn.close()
        conn = None

        os.replace(partial_path, shadow_sidecar_path)
    except Exception:
        if conn is not None:
            conn.rollback()
            conn.close()
        partial_path.unlink(missing_ok=True)
        raise

    validate_sidecar(
        shadow_sidecar_path,
        expected_generation_id=generation_id,
        expected_graph_snapshot=graph_snapshot,
        expected_graph_sha256=sha256_file(graph_path),
    )

    geom = evaluate_shadow_geometry(shadow_sidecar_path)

    return {
        "shadow_sidecar_path": str(shadow_sidecar_path),
        "shadow_sidecar_sha256": sha256_file(shadow_sidecar_path),
        "generation_id": generation_id,
        "base_record_count": len(base_records),
        "passage_record_count": len(passages),
        "proposition_record_count": len(propositions),
        "entity_profile_record_count": len(profiles),
        "total_record_count": len(all_records),
        "reused_vectors": reused_cnt,
        "delta_vectors": delta_cnt,
        "validation_passed": True,
        "geometry": geom,
    }

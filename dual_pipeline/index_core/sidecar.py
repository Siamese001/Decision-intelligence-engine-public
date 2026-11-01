"""Sidecar generation, building, caching, and validation."""

from __future__ import annotations

import copy
import json
import os
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence, Union

from dual_pipeline.assurance import backend_assurance_tier
from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import (
    connect_sqlite,
    digest,
    sha256_file,
    sqlite_integrity,
    utc_now,
)
from dual_pipeline.hash_cache import cached_sha256_file

from .backends import EmbeddingBackend
from .constants import (
    _SHA256_PATTERN,
    BM25_RRF_WEIGHTS,
    DENSE_DIMENSIONS,
    OBJECT_FACET_METADATA_KEYS,
    OBJECT_FACET_SCHEMA_VERSION,
    RETRIEVAL_METADATA_FIELDS,
    RETRIEVAL_METADATA_SCHEMA_VERSION,
    SIDECAR_SCHEMA,
)
from .differential import populate_sidecar_vectors, validate_record_artifact_if_needed
from .records import (  # noqa: lazy-decouple
    render_retrieval_record_object_facets,
    render_retrieval_records,
    retrieval_metadata_from_artifact,
)


def build_sidecar(
    *, graph_path: Path, sidecar_path: Path, generation_id: str, graph_snapshot: str,
    backend: EmbeddingBackend, prior_sidecar_path: Path | None = None,
    staged_vector_paths: Sequence[Union[str, Path]] | None = None,
    host_chunk_size: int | None = None, inference_batch_size: int | None = None,
) -> dict[str, Any]:
    if sidecar_path.exists():
        raise FileExistsError(f"immutable sidecar already exists: {sidecar_path}")
    records = render_retrieval_records(graph_path, graph_snapshot)
    if not records:
        raise RuntimeError("eligible retrieval corpus is empty")
    object_facet_rows = render_retrieval_record_object_facets(graph_path, records)
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    # The final sidecar path is the public readiness signal for a generation.
    # Build and validate the SQLite database under a private sibling name, then
    # publish it atomically.  This prevents an observer from treating a schema-
    # only file, an in-flight transaction, or a journal-backed partial index as
    # an embedded retrieval generation.
    partial_path = sidecar_path.with_name(f".{sidecar_path.name}.partial")
    partial_companions = (
        partial_path,
        partial_path.with_name(f"{partial_path.name}-journal"),
        partial_path.with_name(f"{partial_path.name}-wal"),
        partial_path.with_name(f"{partial_path.name}-shm"),
    )
    for path in partial_companions:
        path.unlink(missing_ok=True)

    connection: sqlite3.Connection | None = None
    started_at = utc_now()
    colbert_dimensions: int | None = None
    try:
        from ..storage.governed_context import GovernedStorageContext
        connection = GovernedStorageContext.connect(partial_path)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA mmap_size=268435456")
        connection.execute("PRAGMA cache_size=-64000")
        connection.executescript(SIDECAR_SCHEMA)
        connection.execute("BEGIN IMMEDIATE")

        connection.executemany(
            """INSERT INTO retrieval_record
               (record_id,lane,record_type,namespace,retrieval_text_hash,
                retrieval_kind,epistemic_status,provenance_kind,answer_use,artifact_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    record["record_id"],
                    record["lane"],
                    record["record_type"],
                    record["namespace"],
                    record["retrieval_text_hash"],
                    record["retrieval_kind"],
                    record["epistemic_status"],
                    record["provenance_kind"],
                    record["answer_use"],
                    json.dumps(record, ensure_ascii=False, sort_keys=True),
                )
                for record in records
            ],
        )
        connection.executemany(
            """INSERT INTO retrieval_record_object_facet
               (record_id,assignment_id,object_type,object_id,vocabulary,value,
                assignment_method,review_state)
               VALUES (?,?,?,?,?,?,?,?)""",
            [
                (
                    row["record_id"],
                    row["assignment_id"],
                    row["object_type"],
                    row["object_id"],
                    row["vocabulary"],
                    row["value"],
                    row["assignment_method"],
                    row["review_state"],
                )
                for row in object_facet_rows
            ],
        )

        pop_res = populate_sidecar_vectors(
            connection=connection, records=records, backend=backend, prior_sidecar_path=prior_sidecar_path,
            staged_vector_paths=staged_vector_paths,
            host_chunk_size=host_chunk_size, inference_batch_size=inference_batch_size,
        )
        total_recs, colbert_dimensions, reused_cnt, delta_cnt = pop_res
        staged_cnt = getattr(pop_res, "staged_records", 0)
        from .differential import get_effective_embedding_telemetry
        embedding_telemetry = get_effective_embedding_telemetry(
            backend=backend, total_records=total_recs, reused_records=reused_cnt, delta_records=delta_cnt,
            cache_hits=reused_cnt, staged_records=staged_cnt, host_chunk_size=host_chunk_size, inference_batch_size=inference_batch_size,
        )
        lane_counts = Counter(record["lane"] for record in records)
        manifest_rows = [
            {"record_id": record["record_id"], "lane": record["lane"], "retrieval_text_hash": record["retrieval_text_hash"]}
            for record in records
        ]
        retrieval_metadata_rows = sorted(
            [
                {
                    "record_id": r["record_id"], "retrieval_kind": r["retrieval_kind"],
                    "epistemic_status": r["epistemic_status"], "provenance_kind": r["provenance_kind"],
                    "answer_use": r["answer_use"],
                }
                for r in records
            ],
            key=lambda row: row["record_id"],
        )
        graph_file_sha = cached_sha256_file(graph_path)
        metadata = {
            "schema_version": "1.0.0",
            "retrieval_metadata_schema_version": RETRIEVAL_METADATA_SCHEMA_VERSION,
            "generation_id": generation_id,
            "graph_snapshot": graph_snapshot,
            "graph_sha256": graph_file_sha,
            "bound_graph_sha256": graph_file_sha,
            "model_id": backend.model_id,
            "model_revision": backend.model_revision,
            "model_manifest_digest": backend.model_manifest_digest or "not-applicable",
            "embedding_backend": backend.backend_id,
            "production_qualified": "true" if backend.production_qualified else "false",
            "assurance_tier": backend_assurance_tier(backend),
            "internal_operational_policy_digest": getattr(backend, "internal_operational_policy_digest", None) or "not-applicable",
            "encoding_fingerprint_digest": getattr(backend, "encoding_fingerprint_digest", None) or "not-applicable",
            "dense_dimensions": str(DENSE_DIMENSIONS),
            "colbert_dimensions": str(colbert_dimensions),
            "record_count": str(len(records)),
            "staged_vector_hits": str(staged_cnt),
            "approved_record_count": str(lane_counts.get("approved", 0)),
            "candidate_record_count": str(lane_counts.get("candidate", 0)),
            "external_public_record_count": str(lane_counts.get("external_public", 0)),
            "retrieval_text_manifest_digest": digest(manifest_rows),
            "retrieval_metadata_manifest_digest": digest(retrieval_metadata_rows),
            "object_facet_schema_version": OBJECT_FACET_SCHEMA_VERSION,
            "object_facet_assignment_count": str(len(object_facet_rows)),
            "object_facet_manifest_digest": digest(object_facet_rows),
            "retrieval_configuration_digest": digest({
                "dense_weight": BM25_RRF_WEIGHTS["dense"], "sparse_weight": BM25_RRF_WEIGHTS["sparse"],
                "bm25_weight": BM25_RRF_WEIGHTS["bm25"], "colbert_weight": BM25_RRF_WEIGHTS["colbert"],
                "rrf_k": 60, "lane_blending": False, "entity_title_exact_match": True,
            }),
            "built_at": started_at,
        }
        connection.executemany("INSERT INTO metadata VALUES (?,?)", sorted(metadata.items()))
        connection.commit()
        connection.close()
        connection = None
        # The generation directory is unique and protected by the release
        # manager, but retain the final-path check so a concurrent or stale
        # writer cannot silently replace an immutable artifact.
        reused_ids = getattr(pop_res, "reused_record_ids", frozenset())
        if sidecar_path.exists():
            raise FileExistsError(f"immutable sidecar appeared during publication: {sidecar_path}")
        os.replace(partial_path, sidecar_path)
    except Exception:
        if connection is not None:
            connection.rollback()
            connection.close()
        for path in partial_companions:
            path.unlink(missing_ok=True)
        raise
    validation = validate_sidecar(
        sidecar_path,
        expected_generation_id=generation_id,
        expected_graph_snapshot=graph_snapshot,
        expected_graph_sha256=graph_file_sha,
        reused_record_ids=reused_ids,
    )
    sidecar_file_sha = cached_sha256_file(sidecar_path)
    return {
        "generation_id": generation_id,
        "graph_snapshot": graph_snapshot,
        "graph_sha256": graph_file_sha,
        "sidecar_sha256": sidecar_file_sha,
        "model_id": backend.model_id,
        "model_revision": backend.model_revision,
        "model_manifest_digest": backend.model_manifest_digest,
        "embedding_backend": backend.backend_id,
        "production_qualified": backend.production_qualified,
        "publication_state": "PUBLISHED",
        "assurance_tier": backend_assurance_tier(backend),
        "internal_operational_policy_digest": getattr(backend, "internal_operational_policy_digest", None),
        "encoding_fingerprint_digest": getattr(backend, "encoding_fingerprint_digest", None),
        "record_count": len(records),
        "reused_records": reused_cnt,
        "delta_records": delta_cnt,
        "staged_records": staged_cnt,
        "staged_vector_hits": staged_cnt,
        "cache_hits": reused_cnt,
        "effective_device": embedding_telemetry["effective_device"],
        "effective_precision": embedding_telemetry["effective_precision"],
        "effective_inference_batch": embedding_telemetry["effective_inference_batch"],
        "effective_host_chunk": embedding_telemetry["effective_host_chunk"],
        "embedding_telemetry": embedding_telemetry,
        "lane_counts": dict(sorted(Counter(record["lane"] for record in records).items())),
        "retrieval_text_manifest_digest": validation["metadata"]["retrieval_text_manifest_digest"],
        "object_facet_assignment_count": int(validation["metadata"]["object_facet_assignment_count"]),
        "object_facet_manifest_digest": validation["metadata"]["object_facet_manifest_digest"],
        "validation": validation,
    }


def _validate_sidecar_uncached(
    sidecar_path: Path,
    *,
    expected_generation_id: str | None = None,
    expected_graph_snapshot: str | None = None,
    expected_graph_sha256: str | None = None,
    enforce_unit_policy: bool = False,
    reused_record_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    integrity = sqlite_integrity(sidecar_path)
    if integrity["integrity_check"] != "ok" or integrity["foreign_key_violation_count"]:
        raise RuntimeError(f"sidecar integrity failed: {integrity}")
    connection = connect_sqlite(sidecar_path, readonly=True)
    try:
        metadata = {
            row["key"]: row["value"]
            for row in connection.execute("SELECT * FROM metadata")
        }
        object_facet_table_present = bool(
            connection.execute(
                """SELECT 1 FROM sqlite_master
                    WHERE type='table'
                      AND name='retrieval_record_object_facet'"""
            ).fetchone()
        )
        object_facet_metadata_flags = [
            key in metadata for key in OBJECT_FACET_METADATA_KEYS
        ]
        object_facet_metadata_present = all(object_facet_metadata_flags)
        if any(object_facet_metadata_flags) and not object_facet_metadata_present:
            raise RuntimeError("sidecar object-facet metadata is incomplete")
        if object_facet_table_present != object_facet_metadata_present:
            raise RuntimeError(
                "sidecar object-facet table and metadata binding are incomplete"
            )
        object_facet_rows: list[dict[str, str]] = []
        if object_facet_table_present:
            expected_object_facet_columns = {
                "record_id",
                "assignment_id",
                "object_type",
                "object_id",
                "vocabulary",
                "value",
                "assignment_method",
                "review_state",
            }
            object_facet_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(retrieval_record_object_facet)"
                )
            }
            if not expected_object_facet_columns <= object_facet_columns:
                raise RuntimeError("sidecar object-facet columns are incomplete")
            object_facet_rows = [
                dict(row)
                for row in connection.execute(
                    """SELECT record_id,assignment_id,object_type,object_id,
                              vocabulary,value,assignment_method,review_state
                         FROM retrieval_record_object_facet
                        ORDER BY record_id,assignment_id"""
                )
            ]
            if any(row["review_state"] != "approved" for row in object_facet_rows):
                raise RuntimeError(
                    "sidecar object-facet projection contains unapproved assignments"
                )
            if metadata.get("object_facet_schema_version") != OBJECT_FACET_SCHEMA_VERSION:
                raise RuntimeError("sidecar object-facet schema version mismatch")
            if int(metadata["object_facet_assignment_count"]) != len(object_facet_rows):
                raise RuntimeError("sidecar object-facet assignment count mismatch")
            if metadata["object_facet_manifest_digest"] != digest(object_facet_rows):
                raise RuntimeError("sidecar object-facet manifest binding mismatch")
        checks = {
            "generation_id": expected_generation_id,
            "graph_snapshot": expected_graph_snapshot,
            "graph_sha256": expected_graph_sha256,
        }
        for key, expected in checks.items():
            if expected is not None:
                val = metadata.get(key)
                if key == "graph_sha256" and not val:
                    val = metadata.get("bound_graph_sha256")
                if val != expected:
                    raise RuntimeError(f"sidecar {key} binding mismatch")
        counts = {
            "records": connection.execute("SELECT count(*) FROM retrieval_record").fetchone()[0],
            "dense": connection.execute("SELECT count(*) FROM dense_vector").fetchone()[0],
            "sparse_records": connection.execute(
                "SELECT count(DISTINCT record_id) FROM sparse_weight"
            ).fetchone()[0],
            "colbert_records": connection.execute(
                "SELECT count(DISTINCT record_id) FROM colbert_token"
            ).fetchone()[0],
        }
        if not all(value == counts["records"] for value in counts.values()):
            raise RuntimeError(f"sidecar representation cardinality mismatch: {counts}")
        sidecar_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(retrieval_record)")
        }
        metadata_columns_present = set(RETRIEVAL_METADATA_FIELDS) <= sidecar_columns
        partial_metadata_columns = set(RETRIEVAL_METADATA_FIELDS) & sidecar_columns
        if partial_metadata_columns and not metadata_columns_present:
            raise RuntimeError("sidecar retrieval metadata columns are incomplete")
        select_columns = ["record_id", "lane", "artifact_json"]
        if metadata_columns_present:
            select_columns.extend(RETRIEVAL_METADATA_FIELDS)
        lane_contamination_count = 0
        retrieval_metadata_binding_contamination_count = 0
        retrieval_metadata_rows: list[dict[str, str]] = []
        for row in connection.execute(
            f"SELECT {','.join(select_columns)} FROM retrieval_record ORDER BY record_id"
        ):
            artifact = json.loads(row["artifact_json"])
            validate_record_artifact_if_needed(artifact, row["record_id"], reused_record_ids)
            if artifact["record_id"] != row["record_id"] or artifact["lane"] != row["lane"]:
                lane_contamination_count += 1
            if metadata_columns_present:
                artifact_metadata = retrieval_metadata_from_artifact(artifact)
                row_metadata = {field: row[field] for field in RETRIEVAL_METADATA_FIELDS}
                if artifact_metadata != row_metadata:
                    retrieval_metadata_binding_contamination_count += 1
                retrieval_metadata_rows.append({"record_id": row["record_id"], **row_metadata})
        if lane_contamination_count:
            raise RuntimeError(f"sidecar lane/record binding contamination: {lane_contamination_count}")
        if metadata_columns_present:
            if metadata.get("retrieval_metadata_schema_version") != RETRIEVAL_METADATA_SCHEMA_VERSION:
                raise RuntimeError("sidecar retrieval metadata schema version mismatch")
            if metadata.get("retrieval_metadata_manifest_digest") != digest(retrieval_metadata_rows):
                raise RuntimeError("sidecar retrieval metadata manifest binding mismatch")
        if retrieval_metadata_binding_contamination_count:
            raise RuntimeError(
                f"sidecar retrieval metadata binding contamination: {retrieval_metadata_binding_contamination_count}"
            )
        if int(metadata["record_count"]) != counts["records"]:
            raise RuntimeError("sidecar metadata record count mismatch")
        dense_shape = connection.execute(
            """SELECT min(dimensions),max(dimensions),
                      min(length(vector)),max(length(vector))
                 FROM dense_vector"""
        ).fetchone()
        if tuple(dense_shape) != (
            DENSE_DIMENSIONS,
            DENSE_DIMENSIONS,
            DENSE_DIMENSIONS * 4,
            DENSE_DIMENSIONS * 4,
        ):
            raise RuntimeError(f"sidecar dense-vector shape mismatch: {tuple(dense_shape)}")
        expected_colbert_dimensions = int(metadata["colbert_dimensions"])
        colbert_shape = connection.execute(
            """SELECT min(dimensions),max(dimensions),
                      min(length(vector)),max(length(vector))
                 FROM colbert_token"""
        ).fetchone()
        if tuple(colbert_shape) != (
            expected_colbert_dimensions,
            expected_colbert_dimensions,
            expected_colbert_dimensions * 4,
            expected_colbert_dimensions * 4,
        ):
            raise RuntimeError(f"sidecar ColBERT-vector shape mismatch: {tuple(colbert_shape)}")
        lane_counts = {
            row["lane"]: row["count"]
            for row in connection.execute(
                "SELECT lane,count(*) AS count FROM retrieval_record GROUP BY lane"
            )
        }
        for lane in ("approved", "candidate", "external_public"):
            if int(metadata[f"{lane}_record_count"]) != lane_counts.get(lane, 0):
                raise RuntimeError(f"sidecar {lane} metadata count mismatch")
        from dual_pipeline.index_core.sidecar_validation import run_extended_sidecar_invariants
        run_extended_sidecar_invariants(
            connection, metadata, enforce_unit_policy=enforce_unit_policy
        )
        return {
            "status": "PASS",
            "integrity": integrity,
            "metadata": metadata,
            "counts": counts,
            "lane_contamination_count": lane_contamination_count,
            "retrieval_metadata_columns_present": metadata_columns_present,
            "retrieval_metadata_binding_contamination_count": (
                retrieval_metadata_binding_contamination_count
            ),
            "object_facet_filtering_supported": object_facet_table_present,
            "object_facet_assignment_count": len(object_facet_rows),
            "object_facet_manifest_digest": (
                metadata["object_facet_manifest_digest"]
                if object_facet_table_present
                else None
            ),
        }
    finally:
        connection.close()


@lru_cache(maxsize=16)
def _cached_sidecar_validation(
    resolved_path: str,
    content_sha256: str,
    expected_generation_id: str | None,
    expected_graph_snapshot: str | None,
    expected_graph_sha256: str | None,
    enforce_unit_policy: bool = False,
    reused_record_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Cache the expensive immutable-sidecar audit by exact file content."""

    if not _SHA256_PATTERN.fullmatch(content_sha256):
        raise ValueError("sidecar validation cache requires a SHA-256 content binding")
    import sys
    uncached_fn = getattr(
        sys.modules.get("dual_pipeline.index"),
        "_validate_sidecar_uncached",
        _validate_sidecar_uncached,
    )
    try:
        return uncached_fn(
            Path(resolved_path),
            expected_generation_id=expected_generation_id,
            expected_graph_snapshot=expected_graph_snapshot,
            expected_graph_sha256=expected_graph_sha256,
            enforce_unit_policy=enforce_unit_policy,
            reused_record_ids=reused_record_ids,
        )
    except TypeError:
        return uncached_fn(
            Path(resolved_path),
            expected_generation_id=expected_generation_id,
            expected_graph_snapshot=expected_graph_snapshot,
            expected_graph_sha256=expected_graph_sha256,
            enforce_unit_policy=enforce_unit_policy,
        )


def validate_sidecar(
    sidecar_path: Path,
    *,
    expected_generation_id: str | None = None,
    expected_graph_snapshot: str | None = None,
    expected_graph_sha256: str | None = None,
    enforce_unit_policy: bool = False,
    reused_record_ids: Sequence[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Validate an immutable sidecar, reusing only an exact content-bound audit."""

    resolved = Path(sidecar_path).expanduser().resolve()
    content_sha256 = sha256_file(resolved)
    validation = copy.deepcopy(
        _cached_sidecar_validation(
            str(resolved),
            content_sha256,
            expected_generation_id,
            expected_graph_snapshot,
            expected_graph_sha256,
            enforce_unit_policy=enforce_unit_policy,
            reused_record_ids=frozenset(reused_record_ids),
        )
    )
    if sha256_file(resolved) != content_sha256:
        _cached_sidecar_validation.cache_clear()
        raise RuntimeError("sidecar changed while its validation result was being bound")
    return validation

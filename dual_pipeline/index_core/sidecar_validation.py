"""Extended sidecar validation: set equality, vector norms, and unit policy enforcement."""
from __future__ import annotations

import logging
from typing import Any

import numpy as np

from storage.connection import sqlite3

from .constants import DENSE_DIMENSIONS
from .geometry import unpack_float32

logger = logging.getLogger("dual_pipeline.index_core.sidecar_validation")

ALLOWED_SEMANTIC_UNIT_TYPES = frozenset(
    {"assertion", "source_chunk", "entity_facet", "action_item"}
)
FORBIDDEN_SEMANTIC_UNIT_TYPES = frozenset(
    {"edge", "architecture_relationship"}
)


def run_extended_sidecar_invariants(
    connection: sqlite3.Connection,
    metadata: dict[str, Any],
    *,
    enforce_unit_policy: bool = True,
) -> None:
    """Validates structural set equality, dense norm bounds, and semantic unit policy."""
    _validate_record_id_set_equality(connection)
    _validate_dense_vector_norms_and_values(connection)
    _validate_colbert_token_positions(connection)
    _validate_object_facet_subset(connection)
    _validate_semantic_unit_policy_types(
        connection, metadata, enforce_unit_policy=enforce_unit_policy
    )


def _validate_record_id_set_equality(connection: sqlite3.Connection) -> None:
    """Verifies that record_ids across all representation tables are strictly identical sets."""
    # 1. retrieval_record vs dense_vector
    missing_in_dense = connection.execute(
        "SELECT count(*) FROM (SELECT record_id FROM retrieval_record EXCEPT SELECT record_id FROM dense_vector)"
    ).fetchone()[0]
    extra_in_dense = connection.execute(
        "SELECT count(*) FROM (SELECT record_id FROM dense_vector EXCEPT SELECT record_id FROM retrieval_record)"
    ).fetchone()[0]
    if missing_in_dense > 0 or extra_in_dense > 0:
        raise RuntimeError(
            f"sidecar representation record_id set mismatch between retrieval_record and dense_vector: "
            f"+{missing_in_dense} missing in dense, +{extra_in_dense} extra in dense"
        )

    # 2. retrieval_record vs sparse_weight
    missing_in_sparse = connection.execute(
        "SELECT count(*) FROM (SELECT record_id FROM retrieval_record EXCEPT SELECT record_id FROM sparse_weight)"
    ).fetchone()[0]
    extra_in_sparse = connection.execute(
        "SELECT count(*) FROM (SELECT record_id FROM sparse_weight EXCEPT SELECT record_id FROM retrieval_record)"
    ).fetchone()[0]
    if missing_in_sparse > 0 or extra_in_sparse > 0:
        raise RuntimeError(
            f"sidecar representation record_id set mismatch between retrieval_record and sparse_weight: "
            f"+{missing_in_sparse} missing in sparse, +{extra_in_sparse} extra in sparse"
        )

    # 3. retrieval_record vs colbert_token
    missing_in_colbert = connection.execute(
        "SELECT count(*) FROM (SELECT record_id FROM retrieval_record EXCEPT SELECT record_id FROM colbert_token)"
    ).fetchone()[0]
    extra_in_colbert = connection.execute(
        "SELECT count(*) FROM (SELECT record_id FROM colbert_token EXCEPT SELECT record_id FROM retrieval_record)"
    ).fetchone()[0]
    if missing_in_colbert > 0 or extra_in_colbert > 0:
        raise RuntimeError(
            f"sidecar representation record_id set mismatch between retrieval_record and colbert_token: "
            f"+{missing_in_colbert} missing in colbert, +{extra_in_colbert} extra in colbert"
        )


def _validate_dense_vector_norms_and_values(connection: sqlite3.Connection) -> None:
    """Verifies that all dense vectors are non-zero, finite, and strictly L2-normalized."""
    rows = connection.execute("SELECT record_id, dimensions, vector FROM dense_vector").fetchall()
    if not rows:
        return

    for row in rows:
        rid = row[0]
        dim = row[1]
        blob = bytes(row[2])
        if dim != DENSE_DIMENSIONS:
            raise RuntimeError(
                f"sidecar dense vector dimension mismatch for {rid}: {dim} != {DENSE_DIMENSIONS}"
            )
        vec = unpack_float32(blob)
        if vec is None:
            raise RuntimeError(f"sidecar dense vector could not be unpacked for {rid}")
        if np.isnan(vec).any() or np.isinf(vec).any():
            raise RuntimeError(f"sidecar dense vector contains NaN or Inf values for record_id {rid}")
        norm = float(np.linalg.norm(vec))
        if norm < 1e-4:
            raise RuntimeError(f"sidecar dense vector has zero or near-zero norm for record_id {rid}")
        if norm < 0.85 or norm > 1.15:
            raise RuntimeError(
                f"sidecar dense vector violates L2-norm bounds [0.85, 1.15] for record_id {rid}: norm={norm:.4f}"
            )


def _validate_colbert_token_positions(connection: sqlite3.Connection) -> None:
    """Verifies that colbert_token positions for each record_id are strictly contiguous 0..K-1."""
    rows = connection.execute(
        "SELECT record_id, position, dimensions, vector FROM colbert_token ORDER BY record_id, position"
    ).fetchall()
    if not rows:
        return

    current_rid = None
    expected_pos = 0
    for row in rows:
        rid = row[0]
        pos = row[1]
        blob = bytes(row[3])
        if rid != current_rid:
            current_rid = rid
            expected_pos = 1 if pos == 1 else 0
        if pos != expected_pos:
            raise RuntimeError(
                f"sidecar colbert_token positions for record_id '{rid}' are non-contiguous: expected {expected_pos}, got {pos}"
            )
        expected_pos += 1
        vec = unpack_float32(blob)
        if vec is None:
            raise RuntimeError(f"sidecar colbert token vector could not be unpacked for record_id '{rid}' at position {pos}")
        if np.isnan(vec).any() or np.isinf(vec).any():
            raise RuntimeError(f"sidecar colbert token vector contains NaN or Inf for record_id '{rid}' at position {pos}")


def _validate_object_facet_subset(connection: sqlite3.Connection) -> None:
    """Verifies that retrieval_record_object_facet records map strictly to existing retrieval_record rows."""
    facet_table_present = bool(
        connection.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type='table'
                 AND name='retrieval_record_object_facet'"""
        ).fetchone()
    )
    if not facet_table_present:
        return
    orphan_facets = connection.execute(
        "SELECT count(*) FROM (SELECT record_id FROM retrieval_record_object_facet EXCEPT SELECT record_id FROM retrieval_record)"
    ).fetchone()[0]
    if orphan_facets > 0:
        raise RuntimeError(
            f"sidecar contains {orphan_facets} orphan record_id entries in retrieval_record_object_facet"
        )


def _validate_semantic_unit_policy_types(
    connection: sqlite3.Connection,
    metadata: dict[str, Any],
    *,
    enforce_unit_policy: bool = False,
) -> None:
    """Rejects forbidden edge types when semantic unit policy is active or requested."""
    is_unit_index = bool(metadata.get("semantic_unit_index", False)) or enforce_unit_policy
    if not is_unit_index:
        return

    record_types = {
        row[0] for row in connection.execute("SELECT DISTINCT record_type FROM retrieval_record").fetchall()
    }

    forbidden = record_types & FORBIDDEN_SEMANTIC_UNIT_TYPES
    if forbidden:
        raise RuntimeError(
            f"sidecar contains forbidden record_type under semantic unit policy: {sorted(forbidden)}"
        )

    disallowed = record_types - ALLOWED_SEMANTIC_UNIT_TYPES
    if disallowed:
        raise RuntimeError(
            f"sidecar contains unsupported record_type under semantic unit policy: {sorted(disallowed)}"
        )

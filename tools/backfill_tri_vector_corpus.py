#!/usr/bin/env python3
"""Backfill Tri-Vector Corpus & Enforce Quarantine."""

import argparse
import json
import os
from pathlib import Path


def backfill():
    import struct

    from dual_pipeline.index_core.backends import (
        InternalOperationalFlagEmbeddingBackend,
        _resolve_embedding_device,
    )
    from dual_pipeline.index_core.sidecar import connect_sqlite
    from dual_pipeline.storage.quantized_colbert_packer import pack_colbert_tensor

    # Get active pair sidecar path
    active_pair_path = os.environ.get("ACTIVE_PAIR_PATH", ".local/dual_pipeline/active_pair.json")
    with open(active_pair_path, "r") as f:
        active = json.load(f)
    sidecar_path = Path(active["sidecar_path"])

    device = _resolve_embedding_device()
    backend = InternalOperationalFlagEmbeddingBackend(device=device)

    conn = connect_sqlite(sidecar_path, readonly=False)

    # Inject legacy_quarantine
    conn.execute("""
        UPDATE retrieval_record
        SET artifact_json = json_insert(artifact_json, '$.assurance_tier', 'legacy_quarantine')
        WHERE json_extract(artifact_json, '$.assurance_tier') IS NULL
    """)

    # Get records without dense vector
    records = conn.execute("""
        SELECT r.record_id, r.artifact_json
        FROM retrieval_record r
        LEFT JOIN dense_vector d ON r.record_id = d.record_id
        WHERE d.record_id IS NULL
    """).fetchall()

    for row in records:
        rid = row["record_id"]
        artifact = json.loads(row["artifact_json"])
        text = artifact.get("retrieval_text", "")
        if not text:
            continue

        encoded = backend.encode(text)

        # Insert dense
        dense = encoded.get("dense", [])
        if dense:
            dense_bytes = struct.pack(f"<{len(dense)}f", *dense)
            conn.execute("INSERT INTO dense_vector (record_id, dimensions, vector) VALUES (?, ?, ?)",
                         (rid, len(dense), dense_bytes))

        # Insert sparse
        sparse = encoded.get("sparse", {})
        for token, weight in sparse.items():
            conn.execute("INSERT INTO sparse_weight (record_id, token, weight) VALUES (?, ?, ?)",
                         (rid, token, float(weight)))

        # Insert colbert
        colbert = encoded.get("colbert", [])
        for pos, vec in enumerate(colbert):
            vec_bytes = pack_colbert_tensor(vec)
            conn.execute("INSERT INTO colbert_token (record_id, position, token, dimensions, vector) VALUES (?, ?, ?, ?, ?)",
                         (rid, pos, "", len(vec), vec_bytes))

    conn.commit()
    conn.close()
    print("Backfill complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-all", action="store_true")
    args = parser.parse_args()
    backfill()

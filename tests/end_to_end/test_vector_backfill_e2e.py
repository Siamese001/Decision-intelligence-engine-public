import json
import sqlite3

from dual_pipeline.index_core.constants import SIDECAR_SCHEMA


def test_backfill_end_to_end(tmp_path, monkeypatch):
    sidecar_path = tmp_path / "sidecar.db"
    conn = sqlite3.connect(sidecar_path)
    conn.executescript(SIDECAR_SCHEMA)
    conn.close()

    # insert dummy data
    artifact = {
        "retrieval_text": "Sample text for embeddings.",
        "classification": "public",
        "audiences": ["all"],
        "retrieval_kind": "assertion",
        "epistemic_status": "approved",
        "provenance_kind": "source_bound",
        "answer_use": "factual_answer",
        "record_type": "assertion"
    }
    conn = sqlite3.connect(sidecar_path)
    conn.execute("""
        INSERT INTO retrieval_record (
            record_id, lane, record_type, namespace, retrieval_text_hash,
            retrieval_kind, epistemic_status, provenance_kind, answer_use, artifact_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ("rec-1", "approved", "assertion", "test", "hash1",
          "assertion", "approved", "source_bound", "factual_answer", json.dumps(artifact)))
    conn.commit()
    conn.close()

    # Create dummy active_pair.json
    active_pair = {"sidecar_path": str(sidecar_path)}
    active_path = tmp_path / "active_pair.json"
    with open(active_path, "w") as f:
        json.dump(active_pair, f)

    import dual_pipeline.index_core.backends
    class DummyBackend:
        def encode(self, text):
            return {
                "dense": [0.1] * 1024,
                "sparse": {"token": 0.5},
                "colbert": [[0.2] * 32],
                "colbert_tokens": ["token"]
            }
    monkeypatch.setattr(dual_pipeline.index_core.backends, "InternalOperationalFlagEmbeddingBackend", lambda device: DummyBackend())

    # Run backfill
    monkeypatch.setenv("ACTIVE_PAIR_PATH", str(active_path))
    # mock sys.argv to avoid argparse issues if run directly
    import sys
    monkeypatch.setattr(sys, 'argv', ['backfill_tri_vector_corpus.py'])

    # Fix the script path resolution
    import tools.backfill_tri_vector_corpus
    tools.backfill_tri_vector_corpus.backfill()

    # Verify DB
    conn = sqlite3.connect(sidecar_path)
    # Check assurance tier
    rec = conn.execute("SELECT artifact_json FROM retrieval_record").fetchone()[0]
    rec_json = json.loads(rec)
    assert rec_json.get("assurance_tier") == "legacy_quarantine"

    # Check vectors
    dense_count = conn.execute("SELECT count(*) FROM dense_vector").fetchone()[0]
    sparse_count = conn.execute("SELECT count(*) FROM sparse_weight").fetchone()[0]
    colbert_count = conn.execute("SELECT count(*) FROM colbert_token").fetchone()[0]

    assert dense_count == 1
    assert sparse_count == 1
    assert colbert_count == 1

    colbert_dim = conn.execute("SELECT dimensions FROM colbert_token").fetchone()[0]
    assert colbert_dim == 32
    conn.close()

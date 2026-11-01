import sqlite3

from dual_pipeline.index_core.constants import SIDECAR_SCHEMA
from dual_pipeline.index_core.fusion import _fetch_missing_colbert_tokens


def test_corrupted_colbert_token_adversarial(tmp_path):
    sidecar_path = tmp_path / "sidecar.db"
    conn = sqlite3.connect(sidecar_path)
    conn.executescript(SIDECAR_SCHEMA)
    conn.close()

    conn = sqlite3.connect(sidecar_path)
    # Insert corrupted colbert token (bad dimensions or garbage bytes)
    conn.execute("INSERT INTO retrieval_record (record_id, lane, record_type, namespace, retrieval_text_hash, retrieval_kind, epistemic_status, provenance_kind, answer_use, artifact_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 ("rec-bad", "approved", "assertion", "test", "hash", "assertion", "approved", "source_bound", "factual_answer", "{}"))

    conn.execute("INSERT INTO colbert_token (record_id, position, token, dimensions, vector) VALUES (?, ?, ?, ?, ?)",
                 ("rec-bad", 0, "bad", 32, b"garbage_bytes_that_cannot_unpack!"))
    conn.commit()

    cache = {}
    # Should not crash, should just return empty tensor
    _fetch_missing_colbert_tokens(conn, ["rec-bad"], cache)

    assert "rec-bad" in cache
    # The shape should be (0, 32) representing empty tensor on fail
    assert cache["rec-bad"].shape == (0, 32)
    conn.close()

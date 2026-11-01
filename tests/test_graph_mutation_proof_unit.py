"""Unit tests for graph mutation physical proof primitives."""
import sqlite3

import pytest

from tools.graph_mutation_proof_core import proof, raw_byte_scan, snapshot


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, body TEXT)")
    conn.execute("INSERT INTO assertion VALUES ('seed_row', 'seed')")
    conn.commit()
    conn.close()
    return path


def test_snapshot_records_file_state_and_counts(tmp_path):
    db = _make_db(tmp_path / "g.sqlite")
    snap = snapshot(db)
    assert snap["size_bytes"] == db.stat().st_size
    assert len(snap["sha256"]) == 64
    assert snap["row_counts"] == {"assertion": 1}


def test_raw_byte_scan_finds_and_misses(tmp_path):
    db = _make_db(tmp_path / "g.sqlite")
    result = raw_byte_scan(db, ["seed_row", "not_there"])
    assert isinstance(result["seed_row"], int) and result["seed_row"] > 0
    assert result["not_there"] is None


def test_raw_byte_scan_empty_id_list(tmp_path):
    db = _make_db(tmp_path / "g.sqlite")
    assert raw_byte_scan(db, []) == {}


def test_raw_byte_scan_match_across_chunk_boundary(tmp_path, monkeypatch):
    db = _make_db(tmp_path / "g.sqlite")
    expected = raw_byte_scan(db, ["seed_row"])["seed_row"]
    monkeypatch.setattr(proof, "_CHUNK", 7)
    assert raw_byte_scan(db, ["seed_row"])["seed_row"] == expected


def test_snapshot_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        snapshot(tmp_path / "absent.sqlite")

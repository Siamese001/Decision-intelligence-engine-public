"""Adversarial tests: fake proofs must fail (dry run, WAL-only, wrong delta, no IDs, failing readback)."""
import sqlite3

from tools.graph_mutation_proof_core import prove, snapshot

NEW_ID = "asrt_wave1_adv_marker_0001"
PROBE = {"new_row": ("SELECT 1 FROM assertion WHERE assertion_id=?", (NEW_ID,))}


def _make_db(path, wal=False):
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, body TEXT)")
    conn.execute("INSERT INTO assertion VALUES ('seed', 'x')")
    conn.commit()
    conn.close()


def _status(receipt):
    return {c["check"]: c["status"] for c in receipt["checks"]}


def test_rolled_back_write_fails_file_and_raw_checks(tmp_path):
    db = tmp_path / "g.sqlite"
    _make_db(db)
    before = snapshot(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO assertion VALUES (?, 'dry run')", (NEW_ID,))
    conn.rollback()
    conn.close()
    after = snapshot(db, tables=list(before["row_counts"]))
    receipt = prove(before, after, [NEW_ID], {"assertion": 1}, PROBE)
    status = _status(receipt)
    assert receipt["overall"] == "FAIL"
    assert status["file_changed"] == "FAIL" and status["raw_bytes"] == "FAIL"
    assert status["row_delta"] == "FAIL" and status["readback"] == "FAIL"


def test_id_only_in_wal_does_not_count_as_committed(tmp_path):
    db = tmp_path / "g.sqlite"
    _make_db(db, wal=True)
    holder = sqlite3.connect(db)
    holder.execute("PRAGMA wal_autocheckpoint=0")
    before = snapshot(db)
    holder.execute("INSERT INTO assertion VALUES (?, 'wal only')", (NEW_ID,))
    holder.commit()
    after = snapshot(db, tables=list(before["row_counts"]))
    receipt = prove(before, after, [NEW_ID], {"assertion": 1}, PROBE)
    holder.close()
    assert _status(receipt)["raw_bytes"] == "FAIL"
    assert receipt["overall"] == "FAIL"


def test_empty_id_list_cannot_pass(tmp_path):
    db = tmp_path / "g.sqlite"
    _make_db(db)
    before = snapshot(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO assertion VALUES (?, 'real')", (NEW_ID,))
    conn.commit()
    conn.close()
    after = snapshot(db, tables=list(before["row_counts"]))
    receipt = prove(before, after, [], {"assertion": 1}, PROBE)
    assert _status(receipt)["raw_bytes"] == "FAIL"
    assert receipt["overall"] == "FAIL"


def test_wrong_expected_delta_fails_row_delta(tmp_path):
    db = tmp_path / "g.sqlite"
    _make_db(db)
    before = snapshot(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO assertion VALUES (?, 'real')", (NEW_ID,))
    conn.commit()
    conn.close()
    after = snapshot(db, tables=list(before["row_counts"]))
    receipt = prove(before, after, [NEW_ID], {"assertion": 5}, PROBE)
    assert _status(receipt)["row_delta"] == "FAIL"
    assert receipt["overall"] == "FAIL"


def test_missing_readback_probe_cannot_pass(tmp_path):
    db = tmp_path / "g.sqlite"
    _make_db(db)
    before = snapshot(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO assertion VALUES (?, 'real')", (NEW_ID,))
    conn.commit()
    conn.close()
    after = snapshot(db, tables=list(before["row_counts"]))
    receipt = prove(before, after, [NEW_ID], {"assertion": 1}, None)
    assert _status(receipt)["readback"] == "FAIL"
    assert receipt["overall"] == "FAIL"

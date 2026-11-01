"""End-to-end: real committed write on a temp database passes all five checks, via API and CLI."""
import json
import sqlite3

from tools.graph_mutation_proof_core import prove, snapshot
from tools.prove_graph_mutation import main as cli_main

NEW_ID = "asrt_wave1_e2e_marker_0001"
PROBE = {"new_row": ("SELECT 1 FROM assertion WHERE assertion_id=?", (NEW_ID,))}


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE assertion (assertion_id TEXT PRIMARY KEY, body TEXT)")
    for i in range(200):
        conn.execute("INSERT INTO assertion VALUES (?, ?)", (f"seed_{i}", "x" * 200))
    conn.commit()
    conn.close()


def _commit_write(path):
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO assertion VALUES (?, ?)", (NEW_ID, "wave1 e2e body"))
    conn.commit()
    conn.close()


def test_committed_write_passes_all_five_checks(tmp_path):
    db = tmp_path / "g.sqlite"
    _make_db(db)
    before = snapshot(db)
    _commit_write(db)
    after = snapshot(db, tables=list(before["row_counts"]))
    receipt = prove(before, after, [NEW_ID], {"assertion": 1}, PROBE)
    assert receipt["overall"] == "PASS", receipt
    assert [c["check"] for c in receipt["checks"]] == [
        "file_changed", "raw_bytes", "integrity", "readback", "row_delta"]
    assert all(c["status"] == "PASS" for c in receipt["checks"])
    assert receipt["checks"][1]["detail"]["offsets"][NEW_ID] > 0


def test_cli_exit_zero_on_real_write_and_one_on_noop(tmp_path):
    db = tmp_path / "g.sqlite"
    _make_db(db)
    before_file = tmp_path / "before.json"
    assert cli_main(["snapshot", "--db", str(db), "--out", str(before_file)]) == 0
    args = ["prove", "--before", str(before_file), "--db", str(db), "--id", NEW_ID,
            "--delta", "assertion=1", "--probe", "SELECT 1 FROM assertion WHERE assertion_id=?", NEW_ID,
            "--out", str(tmp_path / "receipt.json")]
    assert cli_main(args) == 1  # nothing written yet
    _commit_write(db)
    assert cli_main(args) == 0
    receipt = json.loads((tmp_path / "receipt.json").read_text())
    assert receipt["overall"] == "PASS"

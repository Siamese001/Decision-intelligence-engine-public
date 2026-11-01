"""Wave 3 adversarial and edge-case tests for Gate C3 & Gate C4 corpus parity verifier."""
from __future__ import annotations

import concurrent.futures
import json
import shutil
import sqlite3
from pathlib import Path
import pytest

from dual_pipeline.document_pipeline_core.quality import qualify_document_judge
from dual_pipeline.llm_judge_core.adversarial_probe import (
    CounterfactualRecallError,
    run_counterfactual_recall_probe,
)
from tools.verify_document_graph_parity import audit_source_parity, main as parity_cli_main


REPO_ROOT = Path(__file__).resolve().parent.parent


def _create_isolated_parity_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_type TEXT,
            external_ref TEXT,
            title TEXT,
            cold_archive_ref TEXT
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            source_id TEXT,
            quote_text TEXT
        );
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            status TEXT
        );
        CREATE TABLE edge (
            edge_id TEXT PRIMARY KEY,
            from_node_id TEXT,
            to_node_id TEXT
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            object_node_id TEXT,
            canonical_status TEXT,
            canonical_eligible INTEGER,
            claim_text TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT
        );
        """
    )
    conn.commit()
    conn.close()


# Category 1: Boundary payload
def test_boundary_payload_empty_source_and_missing_db(tmp_path: Path) -> None:
    res = run_counterfactual_recall_probe("", projection={})
    assert res["status"] == "PASS"
    assert res["total_probes"] == 0

    missing_res = audit_source_parity(tmp_path / "nonexistent.sqlite")
    assert missing_res["status"] == "FAIL"
    assert missing_res["violations"][0]["code"] == "ERR_DB_NOT_FOUND"


# Category 2: Malformed input
def test_malformed_input_corrupt_projection_files_and_non_dict_judge(tmp_path: Path) -> None:
    db_path = tmp_path / "parity.sqlite"
    _create_isolated_parity_db(db_path)
    out_dir = tmp_path / "output" / "project_a" / "2026-10-20"
    out_dir.mkdir(parents=True)
    (out_dir / "03_bad_canonical_signal_projection.json").write_text("{corrupt json", encoding="utf-8")

    res = audit_source_parity(db_path, tmp_path / "output")
    assert res["status"] == "PASS"

    qualified, passed, _ = qualify_document_judge({"raw_judge_scorecard": "not-a-dict", "verdict": "PASS"})
    assert passed is False
    assert qualified["verdict"] == "FAIL"


# Category 3: Concurrency & race
def test_concurrency_parallel_parity_audits_and_recall_probes(tmp_path: Path) -> None:
    db_path = tmp_path / "concurrent.sqlite"
    _create_isolated_parity_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO source VALUES ('src-ok', 'document', 'doc-ok', 'Doc OK', 'output/doc/file.md')"
    )
    conn.execute("INSERT INTO evidence_item VALUES ('ev-1', 'src-ok', 'Substantive fact 123.')")
    conn.execute("INSERT INTO node VALUES ('node:1', 'canonical'), ('node:2', 'canonical')")
    conn.execute("INSERT INTO edge VALUES ('edge:1', 'node:1', 'node:2')")
    for i in range(4):
        conn.execute(
            "INSERT INTO assertion VALUES (?, 'node:1', 'node:2', 'canonical', 1, ?)",
            (f"asrt-{i}", f"Substantive architectural fact {i} with metric {i * 10}%."),
        )
        conn.execute("INSERT INTO assertion_evidence VALUES (?, 'ev-1')", (f"asrt-{i}",))
    conn.commit()
    conn.close()

    def _worker(idx: int) -> str:
        r = audit_source_parity(db_path, strict=True)
        return r["status"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(_worker, range(16)))
    assert all(s == "PASS" for s in statuses)


# Category 4: Overflow & exhaustion
def test_overflow_large_multi_unit_stratified_sampling() -> None:
    pages = [
        f"## Page {i}\n| Area | Sub Capabilities |\n| --- | --- |\n| Enterprise AI | Workstream Unit {i:03d} |"
        for i in range(1, 65)
    ]
    source_text = "\n\n".join(pages)
    proj = {
        "evidence_index": {
            f"ev-{i}": {"exact_text": f"Enterprise AI | Workstream Unit {i:03d}"}
            for i in range(1, 65)
        },
        "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
    }
    receipt = run_counterfactual_recall_probe(source_text, projection=proj, max_probes=12, enforce=True)
    assert receipt["status"] == "PASS"
    assert receipt["total_probes"] == 12
    assert receipt["recall"] == 1.0


# Category 5: Invariant bypass attempt (Gate C4 catches zero-canonical, .staging_*, 3-assertion stub, boilerplate, and truncated workbooks)
def test_invariant_bypass_gate_c4_catches_all_five_defect_classes(tmp_path: Path) -> None:
    db_path = tmp_path / "defective.sqlite"
    _create_isolated_parity_db(db_path)
    out_dir = tmp_path / "output" / "proj" / "2026-10-20"
    out_dir.mkdir(parents=True)
    (out_dir / "03_trunc_canonical_signal_projection.json").write_text(
        json.dumps(
            {
                "meeting_id": "document-truncated-sow",
                "graph_mutations": {
                    "relationship_signals": [{"signal_id": f"s-{i}"} for i in range(10)],
                },
            }
        ),
        encoding="utf-8",
    )

    conn = sqlite3.connect(db_path)
    # 1. Zero-canonical + .staging_ leak
    conn.execute(
        "INSERT INTO source VALUES ('src-zero', 'meeting', 'm-zero', 'Zero Canonical', '.staging_Aruna_123/email.md')"
    )
    conn.execute("INSERT INTO evidence_item VALUES ('ev-z', 'src-zero', 'Quote')")
    conn.execute("INSERT INTO assertion VALUES ('a-z', 'n1', 'n2', 'candidate', 0, 'Unpromoted claim')")
    conn.execute("INSERT INTO assertion_evidence VALUES ('a-z', 'ev-z')")

    # 2. Synthetic 3-assertion stub
    conn.execute(
        "INSERT INTO source VALUES ('src-stub', 'meeting', 'm-stub', 'Stub Meeting', 'output/stub/m.md')"
    )
    conn.execute("INSERT INTO evidence_item VALUES ('ev-s', 'src-stub', 'Stub quote')")
    for idx in range(3):
        conn.execute(
            "INSERT INTO assertion VALUES (?, 'n1', 'n2', 'canonical', 1, ?)",
            (f"a-stub-{idx}", f"Canonical governance release promotion fact {idx}."),
        )
        conn.execute("INSERT INTO assertion_evidence VALUES (?, 'ev-s')", (f"a-stub-{idx}",))

    # 3. Single-assertion workbook
    conn.execute(
        "INSERT INTO source VALUES ('src-1row', 'document', 'document-pos-map', 'Document Position Mapping', 'output/doc/p.md')"
    )
    conn.execute("INSERT INTO evidence_item VALUES ('ev-1r', 'src-1row', 'Workbook header')")
    conn.execute("INSERT INTO assertion VALUES ('a-1r', 'n1', 'n2', 'canonical', 1, 'Single generic claim')")
    conn.execute("INSERT INTO assertion_evidence VALUES ('a-1r', 'ev-1r')")

    # 4. Boilerplate substitution document
    conn.execute(
        "INSERT INTO source VALUES ('src-boiler', 'document', 'document-gold', 'Document Gold Dataset', 'output/doc/g.md')"
    )
    conn.execute("INSERT INTO evidence_item VALUES ('ev-b', 'src-boiler', 'Gold table')")
    for idx in range(5):
        conn.execute(
            "INSERT INTO assertion VALUES (?, 'n1', 'n2', 'canonical', 1, ?)",
            (f"a-b-{idx}", f"Metric {idx} is an identified metric in Document Gold Dataset."),
        )
        conn.execute("INSERT INTO assertion_evidence VALUES (?, 'ev-b')", (f"a-b-{idx}",))

    # 5. Truncated document vs on-disk 03_* (2 assertions in DB vs 10 signals in 03_*)
    conn.execute(
        "INSERT INTO source VALUES ('src-trunc', 'document', 'document-truncated-sow', 'Document Truncated SOW', 'output/proj/2026-10-20/m.md')"
    )
    conn.execute("INSERT INTO evidence_item VALUES ('ev-t', 'src-trunc', 'SOW text')")
    for idx in range(2):
        conn.execute(
            "INSERT INTO assertion VALUES (?, 'n1', 'n2', 'canonical', 1, ?)",
            (f"a-t-{idx}", f"Substantive SOW deliverable claim {idx} with SLA 99.9%."),
        )
        conn.execute("INSERT INTO assertion_evidence VALUES (?, 'ev-t')", (f"a-t-{idx}",))

    conn.commit()
    conn.close()

    report = audit_source_parity(db_path, tmp_path / "output")
    assert report["status"] == "FAIL"
    codes = {v["code"] for v in report["violations"]}
    assert "ERR_STAGING_PATH_LEAKAGE" in codes
    assert "ERR_ZERO_CANONICAL_ASSERTIONS" in codes
    assert "ERR_STUB_GRAPH_PROJECTION" in codes
    assert "ERR_BOILERPLATE_CLAIM_SUBSTITUTION" in codes
    assert "ERR_TRUNCATED_DOCUMENT_PROJECTION" in codes

    exit_code = parity_cli_main(["--db-path", str(db_path), "--output-dir", str(tmp_path / "output"), "--json"])
    assert exit_code == 1


# Category 6: Rollback & recovery
def test_rollback_and_recovery_strict_candidate_and_orphan_node_detection(tmp_path: Path) -> None:
    db_path = tmp_path / "strict.sqlite"
    _create_isolated_parity_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO node VALUES ('node:cand', 'candidate'), ('node:orphan', 'canonical')")
    conn.commit()
    conn.close()

    res_strict = audit_source_parity(db_path, strict=True)
    assert res_strict["status"] == "FAIL"
    codes = {v["code"] for v in res_strict["violations"]}
    assert "ERR_UNPROMOTED_CANDIDATE_NODES" in codes
    assert "ERR_ORPHAN_CANONICAL_NODES" in codes

    # Promote candidate and connect nodes -> passes strict mode
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE node SET status = 'canonical' WHERE node_id = 'node:cand'")
    conn.execute("INSERT INTO edge VALUES ('edge:link', 'node:cand', 'node:orphan')")
    conn.commit()
    conn.close()

    res_recovered = audit_source_parity(db_path, strict=True)
    assert res_recovered["status"] == "PASS"
    assert parity_cli_main(["--db-path", str(db_path), "--strict", "--json"]) == 0


# Category 7: Exact production regression
def test_exact_production_regression_db_backed_counterfactual_probe(tmp_path: Path) -> None:
    db_path = tmp_path / "probe_db.sqlite"
    _create_isolated_parity_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO source VALUES ('src-plan', 'document', 'doc-plan', 'Planning', 'output/p.md')")
    conn.execute("INSERT INTO evidence_item VALUES ('ev-p1', 'src-plan', 'AI FinOps Spend Visibility & Control')")
    conn.execute(
        "INSERT INTO assertion VALUES ('a-p1', 'n1', 'n2', 'canonical', 1, 'AI FinOps Spend Visibility & Control is led by Apoorv.')"
    )
    conn.execute("INSERT INTO assertion_evidence VALUES ('a-p1', 'ev-p1')")
    conn.commit()
    conn.close()

    source_text = (
        "## Page 1\n| Area | Sub Capabilities |\n| --- | --- |\n| Enterprise AI | AI FinOps Spend Visibility & Control |\n\n"
        "## Page 8\n| Area | Sub Capabilities |\n| --- | --- |\n| Product Ops | OTT Planning App |\n"
    )
    probe_res = run_counterfactual_recall_probe(
        source_text, db_path=db_path, source_id="src-plan", min_recall=0.90
    )
    assert probe_res["status"] == "FAIL"
    assert probe_res["recall"] == 0.5

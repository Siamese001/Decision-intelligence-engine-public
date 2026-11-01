"""End-to-end lifecycle verification test for GraphDB extraction and mutation CLI surface.

Exercises:
1. CLI graph-stats execution on baseline graph.
2. CLI graph-diff execution across baseline and mutated snapshots (verifying Table 1 and Table 2).
3. CLI graph-gc execution across dry-run and execute cycles.
4. CLI runtime-evidence inventory and control file enforcement.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from dual_pipeline.cli import main
from dual_pipeline.core import ROOT


@pytest.fixture
def isolated_graph_pair(tmp_path: Path):
    """Create isolated baseline and mutated SQLite graph pair for E2E verification."""
    seed_graph = ROOT / "graph" / "nee_graph.sqlite"
    baseline = tmp_path / "baseline_graph.sqlite"
    mutated = tmp_path / "mutated_graph.sqlite"

    shutil.copy2(seed_graph, baseline)
    shutil.copy2(seed_graph, mutated)

    # Apply a known mutation to the mutated copy
    conn = sqlite3.connect(str(mutated))
    cur = conn.cursor()
    new_node_id = "org:e2e_test_partner"
    new_assertion_id = "asrt:e2e_test_assertion_001"

    now_str = "2026-09-27T12:00:00Z"
    cur.execute(
        """INSERT OR REPLACE INTO node (node_id, canonical_name, node_type, active, created_at, updated_at)
           VALUES (?, ?, ?, 1, ?, ?)""",
        (new_node_id, "E2E Test Partner", "org", now_str, now_str),
    )
    cur.row_factory = sqlite3.Row
    template_row = dict(cur.execute("SELECT * FROM assertion LIMIT 1").fetchone())
    import hashlib
    template_row["assertion_id"] = new_assertion_id
    template_row["assertion_fingerprint"] = hashlib.sha256(b"fp_e2e_test_001").hexdigest()
    template_row["subject_node_id"] = new_node_id
    template_row["canonical_status"] = "candidate"
    template_row["currentness_state"] = "current"
    cols = ", ".join(template_row.keys())
    placeholders = ", ".join(["?"] * len(template_row))
    cur.execute(f"INSERT OR REPLACE INTO assertion ({cols}) VALUES ({placeholders})", list(template_row.values()))
    conn.commit()
    conn.close()

    return baseline, mutated, new_node_id, new_assertion_id


def test_e2e_cli_graph_stats(isolated_graph_pair, capsys):
    """Verify nee graph-stats runs end-to-end through CLI main()."""
    baseline, _, _, _ = isolated_graph_pair
    exit_code = main(["graph-stats", "--graph", str(baseline), "--json"])
    assert exit_code == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "PASS"
    assert "table_counts" in payload
    assert "node" in payload["table_counts"]
    assert "assertion" in payload["table_counts"]
    assert "computed_metrics" in payload


def test_e2e_cli_graph_diff_lifecycle(isolated_graph_pair, capsys):
    """Verify nee graph-diff runs end-to-end and captures both Table 1 and Table 2 deltas."""
    baseline, mutated, new_node_id, new_assertion_id = isolated_graph_pair

    exit_code = main([
        "graph-diff",
        "--baseline", str(baseline),
        "--candidate", str(mutated),
        "--json",
    ])
    assert exit_code == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["status"] == "PASS"
    assert payload["is_noop"] is False
    assert payload["graph_sha256_before"] != payload["graph_sha256_after"]

    # Table 1: Granular Delta Table
    delta_table = payload["granular_delta_table"]
    node_row = next(r for r in delta_table if r["component_key"] == "candidate_assertion")
    assert node_row["delta"] == 1

    # Table 2: Itemized Assertion Delta
    itemized = payload["itemized_assertion_delta"]
    assert itemized["created_count"] == 1
    assert any(item["assertion_id"] == new_assertion_id for item in itemized["items"])


def test_e2e_cli_graph_gc_lifecycle(isolated_graph_pair, capsys):
    """Verify nee graph-gc runs end-to-end in dry-run mode via CLI main()."""
    _, mutated, _, _ = isolated_graph_pair

    exit_code = main([
        "graph-gc",
        "--graph", str(mutated),
        "--dry-run",
    ])
    assert exit_code == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "PASS"
    assert payload["dry_run"] is True


def test_e2e_cli_runtime_evidence_inventory_and_controls(capsys):
    """Verify nee runtime-evidence inventory and enforce-controls run end-to-end via CLI main()."""
    # 1. Inventory
    inv_exit = main([
        "runtime-evidence",
        "inventory",
        "--repository-root", str(ROOT),
    ])
    assert inv_exit == 0
    inv_cap = capsys.readouterr()
    inv_payload = json.loads(inv_cap.out)
    assert inv_payload["status"] == "PASS"
    assert "inventory" in inv_payload
    assert "runtime_pairs" in inv_payload["inventory"]

    # 2. Enforce Controls
    ctrl_exit = main([
        "runtime-evidence",
        "enforce-controls",
        "--repository-root", str(ROOT),
    ])
    assert ctrl_exit == 0
    ctrl_cap = capsys.readouterr()
    ctrl_payload = json.loads(ctrl_cap.out)
    assert ctrl_payload["status"] == "PASS"
    assert ctrl_payload["control_errors"] == []

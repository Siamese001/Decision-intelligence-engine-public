"""Regression and counterfactual tests for Wave 1 Gate 32 governed graph write remediation."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dual_pipeline.graph_writer_core.assertions import update_assertion_object_literals
from tools.google_partner_advisory_sync_core import (
    _build_action_assertion_updates,
    merge_cadence_ledgers,
    sync_ledger_with_active_graph,
)
from tools.lint_graph_write_paths import inspect_file_for_direct_graph_writes


def _seed_test_graph(tmp_path: Path) -> Path:
    pair_dir = tmp_path / ".local" / "dual_pipeline"
    gen_dir = pair_dir / "generations" / "gen_test"
    gen_dir.mkdir(parents=True, exist_ok=True)
    db_path = gen_dir / "graph.sqlite"
    pair_path = pair_dir / "active_pair.json"
    pair_path.write_text(
        json.dumps({"graph_path": "generations/gen_test/graph.sqlite"}),
        encoding="utf-8",
    )
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE source (source_id TEXT PRIMARY KEY, source_name TEXT)"
    )
    conn.execute(
        "CREATE TABLE assertion ("
        "assertion_id TEXT PRIMARY KEY, "
        "primary_source_id TEXT, "
        "object_literal TEXT, "
        "canonical_status TEXT, "
        "canonical_eligible INTEGER)"
    )
    conn.execute(
        "INSERT INTO source VALUES ('src_1', '20261027 Weekly PartnerAdvisory Gcp Alignment')"
    )
    conn.execute(
        "INSERT INTO assertion VALUES ('asrt_act_1', 'src_1', 'Original action summary', 'canonical', 1)"
    )
    conn.execute(
        "INSERT INTO assertion VALUES ('asrt_demoted_1', 'src_1', 'Demoted claim', 'candidate', 0)"
    )
    conn.commit()
    conn.close()
    return db_path


def test_gate_32_linter_zero_violations_on_google_partner_advisory_sync_core() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    target = repo_root / "tools" / "google_partner_advisory_sync_core.py"
    violations = inspect_file_for_direct_graph_writes(target, repo_root)
    assert violations == []


def test_sync_ledger_with_active_graph_governed_writeback(tmp_path: Path) -> None:
    db_path = _seed_test_graph(tmp_path)
    ledger = {
        "last_sources": ["20261027 Weekly PartnerAdvisory Gcp Alignment"],
        "open_action_items": [
            {
                "action_id": "ACT-01",
                "graph_assertion_id": "asrt_act_1",
                "owner": "Amit Ayer",
                "status": "COMPLETED",
                "completed_by": "Amit Ayer",
                "completed_date": "2026-10-31",
                "summary": "Deliver architecture readiness checklist",
            }
        ],
    }

    ro_result = sync_ledger_with_active_graph(ledger, repo_root=tmp_path, writeback=False)
    assert ro_result["synced"] is True
    assert ro_result["updated_action_assertions"] == []
    assert ro_result["demoted_assertions"] == ["asrt_demoted_1"]

    rw_result = sync_ledger_with_active_graph(ledger, repo_root=tmp_path, writeback=True)
    assert rw_result["synced"] is True
    assert rw_result["updated_action_assertions"] == ["asrt_act_1"]

    ro_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    row = ro_conn.execute(
        "SELECT object_literal FROM assertion WHERE assertion_id = 'asrt_act_1'"
    ).fetchone()
    ro_conn.close()
    assert row is not None
    assert row[0] == "Completed by Amit Ayer on 2026-10-31 (Amit Ayer): Deliver architecture readiness checklist"


def test_update_assertion_object_literals_direct_path_and_empty(tmp_path: Path) -> None:
    db_path = _seed_test_graph(tmp_path)
    assert update_assertion_object_literals(db_path, []) == []
    updated = update_assertion_object_literals(
        db_path, [("Updated via path", "asrt_act_1"), ("", "asrt_act_1")]
    )
    assert updated == ["asrt_act_1"]
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    val = conn.execute(
        "SELECT object_literal FROM assertion WHERE assertion_id = 'asrt_act_1'"
    ).fetchone()[0]
    conn.close()
    assert val == "Updated via path"


def test_merge_cadence_ledgers_propagates_completed_actions() -> None:
    primary = {
        "open_action_items": [
            {"action_id": "A1", "status": "OPEN", "summary": "Item 1"}
        ]
    }
    mirror = {
        "open_action_items": [
            {
                "action_id": "A1",
                "status": "COMPLETED",
                "completed_by": "Amit Ayer",
                "completed_date": "2026-10-31",
                "completion_marker": "**Completed by Amit Ayer 2026-10-31**",
            }
        ]
    }
    merged = merge_cadence_ledgers(primary, mirror)
    assert merged["open_action_items"][0]["status"] == "COMPLETED"
    assert merged["open_action_items"][0]["completed_by"] == "Amit Ayer"
    updates = _build_action_assertion_updates(
        {"open_action_items": [{"graph_assertion_id": "asrt_1", "status": "OPEN", "summary": "Open item"}]}
    )
    assert updates == [("Open item", "asrt_1")]

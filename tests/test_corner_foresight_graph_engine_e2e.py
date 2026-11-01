"""End-to-End, Unit & Adversarial Verification for the Multi-Hop Corner Foresight Graph Engine.

Verifies:
1. Transitive `entity_redirect` alias resolution and 1-to-3 hop causal domino chain propagation.
2. All 5 blind-spot discovery pillars (expiring horizons & action gates, workstream dormancy & cross-BU tensions,
   single-source bridge fragility, orphan node trajectory cliffs, and Top 3 Unasked Questions synthesis).
3. Strict read-only zero-mutation invariance (`PRE_GRAPH_SHA256 == POST_GRAPH_SHA256`) and em-dash sanitization.
4. Real CLI execution of `tools/run_corner_foresight_scout.py` and `run_corner_foresight_blindspot_scout` adapter wiring.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tools.run_corner_foresight_scout import _resolve_graph_path, main as cli_main
from tools.scheduled_task_core.adapters_core.foresight_engine import (
    CornerForesightGraphEngine,
    _is_canonical_assertion_row,
    _read_pair_graph,
    render_foresight_radar_markdown,
    resolve_target_graph_db,
)
from tools.scheduled_task_core.adapters_core.watchdogs import (
    _collect_untested_modules,
    _resolve_watchdog_outcome,
    run_corner_foresight_blindspot_scout,
    run_testing_gaps_assurance,
)
from tools.scheduled_task_core.authority import ApprovalRecord, AuthorityContext
from tools.scheduled_task_core.contracts import ExecutionMode, TriggerOrigin


def _seed_foresight_sandbox_db(db_path: Path, *, include_orphans: bool = True) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            node_type TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE entity_redirect (
            from_node_id TEXT,
            to_node_id TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            scope_text TEXT,
            assertion_type TEXT,
            confidence_score REAL,
            valid_to TEXT,
            created_at TEXT,
            primary_source_id TEXT,
            canonical_status TEXT,
            canonical_eligible INTEGER,
            currentness_state TEXT,
            superseded_by_assertion_id TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT
        );
        CREATE TABLE p0_p1_action_register (
            action_id TEXT PRIMARY KEY,
            action_title TEXT,
            owner TEXT,
            due_date TEXT,
            blocked_until TEXT,
            recommended_next_step TEXT,
            status TEXT
        );
        CREATE TABLE cross_bu_tension (
            tension_id TEXT PRIMARY KEY,
            source_bu TEXT,
            target_bu TEXT,
            description TEXT,
            mitigation_strategy TEXT,
            severity_score INTEGER
        );
        """
    )
    # Seed transitive redirect chain: alias_old -> alias_mid -> ws_solar
    conn.executemany(
        "INSERT INTO entity_redirect VALUES (?, ?, 1)",
        [
            ("alias_old", "alias_mid"),
            ("alias_mid", "ws_solar"),
        ],
    )
    # Seed core nodes across workstreams, SOWs, deliverables, stakeholders (with em-dash to test sanitization)
    nodes = [
        ("ws_solar", "Solar & Battery — Q4 Program", "workstream", 1),
        ("ws_grid", "Grid Modernization", "workstream", 1),
        ("ws_dormant", "Legacy Substation Alpha", "workstream", 1),
        ("sow_sherpa", "Project Beacon SOW 1", "sow", 1),
        ("deliv_iap", "GCP Landing Zone & IAP", "deliverable", 1),
        ("person_craig", "Craig Finch", "stakeholder", 1),
        ("alias_old", "Old Solar Alias", "workstream", 1),
    ]
    if include_orphans:
        for i in range(55):
            nodes.append((f"orphan_{i}", f"Disconnected Node {i}", "entity", 1))
    conn.executemany("INSERT INTO node VALUES (?, ?, ?, ?)", nodes)

    # Seed 3-hop causal domino chain and single-source bridge assertions
    assertions = [
        # Hop 1 via alias_old (resolves to ws_solar) -> deliv_iap (with valid_to expiring horizon)
        (
            "asrt_exp_1", "alias_old", "DEPENDS_ON", "deliv_iap",
            "Solar workspace blocked on GCP IAP — urgent", "", "decision", 0.92,
            "2026-11-10", "2026-10-28", "src_1", "canonical", 1, "current", "",
        ),
        # Hop 2: deliv_iap -> sow_sherpa
        (
            "asrt_hop_2", "deliv_iap", "DELIVERS", "sow_sherpa",
            "IAP unlocks SOW 1 deployment", "", "confirmed_canonical_fact", 0.95,
            "", "2026-10-28", "src_1", "canonical", 1, "current", "",
        ),
        # Hop 3: person_craig -> deliv_iap (incoming OWNS_OR_LEADS) and person_craig -> ws_grid (bridge across 2 workstreams)
        (
            "asrt_hop_3", "person_craig", "OWNS_OR_LEADS", "deliv_iap",
            "Craig Finch leads landing zone provisioning", "", "assumption", 0.88,
            "", "2026-10-28", "src_1", "canonical", 1, "current", "",
        ),
        (
            "asrt_bridge_ws", "person_craig", "CONTRIBUTES_TO", "ws_grid",
            "Craig Finch bridges Grid Modernization", "", "hypothesis", 0.85,
            "", "2026-10-29", "src_1", "canonical", 1, "current", "",
        ),
        (
            "asrt_bridge_ws2", "person_craig", "CONTRIBUTES_TO", "ws_solar",
            "Craig Finch bridges Solar & Battery", "", "extracted_claim", 0.90,
            "", "2026-10-29", "src_1", "canonical", 1, "current", "",
        ),
        # Corroborated assertion (evidence_count = 2) to verify single-source filter skips multi-source facts
        (
            "asrt_corroborated", "ws_grid", "DEPENDS_ON", "sow_sherpa",
            "Corroborated multi-source claim", "", "decision", 0.95,
            "", "2026-10-30", "src_2", "canonical", 1, "current", "",
        ),
    ]
    # Add 6 July assertions on ws_dormant to trigger Pillar 2 dormancy cliff against 2026-10-30 frontier
    for idx in range(6):
        assertions.append((
            f"asrt_dormant_{idx}", "ws_dormant", "HAS_CLAIM_TEXT", "",
            f"Historical July claim {idx}", "", "extracted_claim", 0.85,
            "", "2026-07-10", "src_july", "canonical", 1, "current", "",
        ))
    conn.executemany(
        "INSERT INTO assertion VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        assertions,
    )
    conn.executemany(
        "INSERT INTO assertion_evidence VALUES (?, ?)",
        [
            ("asrt_exp_1", "ev_1"),
            ("asrt_hop_3", "ev_2"),
            ("asrt_corroborated", "ev_3a"),
            ("asrt_corroborated", "ev_3b"),
        ],
    )
    conn.execute(
        "INSERT INTO p0_p1_action_register VALUES ('act_01', 'Clear Vertex AI IAM', 'Craig Finch', '2026-11-05', 'IAM signoff', 'Schedule 15m sync with Craig', 'OPEN')"
    )
    conn.execute(
        "INSERT INTO cross_bu_tension VALUES ('ten_01', 'ONE_TECH_CORE', 'ONE_TECH_PRODUCTS', 'Platform ownership split', 'Joint architecture review', 5)"
    )
    conn.commit()
    conn.close()


def test_multi_hop_domino_traversal_and_five_pillars_e2e(tmp_path: Path) -> None:
    """Verifies transitive entity redirects, 3-hop domino blast radius, all 5 pillars, and zero graph mutation."""
    db_path = tmp_path / "graph.sqlite"
    _seed_foresight_sandbox_db(db_path, include_orphans=True)
    pre_sha = hashlib.sha256(db_path.read_bytes()).hexdigest()

    engine = CornerForesightGraphEngine(db_path, workspace_root=tmp_path)
    report = engine.run_discovery()

    assert report["zero_mutation_verified"] is True
    assert report["pre_graph_sha256"] == pre_sha
    assert report["post_graph_sha256"] == pre_sha

    tel = report["graph_traversal_telemetry"]
    assert tel["redirects_resolved"] == 2
    assert tel["multi_hop_paths_traced"] >= 4
    assert tel["structural_bridge_nodes_detected"] >= 1

    issues = {b["issue"] for b in report["blind_spots"]}
    assert "EXPIRING_COMMITMENT_BLINDSPOT" in issues
    assert "UNCLOSED_P0_P1_ACTION_LOOP" in issues
    assert "SILENT_WORKSTREAM_VELOCITY_DRIFT" in issues
    assert "CROSS_BU_REORG_TENSION_BLINDSPOT" in issues
    assert "SINGLE_SOURCE_BRIDGE_FRAGILITY" in issues
    assert "ORPHAN_NODE_TRAJECTORY_CLIFF" in issues
    assert len(report["top_3_unasked_questions"]) == 3

    # Verify 3-hop domino chain resolved alias_old -> ws_solar -> deliv_iap -> sow_sherpa / person_craig
    exp_spot = next(b for b in report["blind_spots"] if b["issue"] == "EXPIRING_COMMITMENT_BLINDSPOT")
    assert "GCP Landing Zone & IAP" in exp_spot["impacted_entities"]
    assert "Project Beacon SOW 1" in exp_spot["impacted_entities"]

    # Verify Markdown rendering sanitizes all em-dashes and en-dashes
    md = render_foresight_radar_markdown(report, radar_date="10/30/2026")
    assert "\u2014" not in md
    assert "\u2013" not in md
    assert " -- " not in md
    assert "Top 3 Unasked Questions" in md


def test_corner_foresight_scout_cli_and_pair_resolution(tmp_path: Path) -> None:
    """Verifies CLI runner execution, active_pair.json resolution, and JSON/Markdown artifact output."""
    rt_root = tmp_path / ".local" / "dual_pipeline"
    gen_dir = rt_root / "generations" / "gen_test"
    gen_dir.mkdir(parents=True)
    db_path = gen_dir / "graph.sqlite"
    _seed_foresight_sandbox_db(db_path, include_orphans=False)

    pair_file = rt_root / "active_pair.json"
    pair_file.write_text(json.dumps({"graph_path": "generations/gen_test/graph.sqlite"}), encoding="utf-8")

    assert _read_pair_graph(tmp_path / "missing.json") is None
    bad_pair = tmp_path / "bad_pair.json"
    bad_pair.write_text("{invalid", encoding="utf-8")
    assert _read_pair_graph(bad_pair) is None
    assert resolve_target_graph_db(tmp_path) == db_path.resolve()
    assert _resolve_graph_path(rt_root, db_path) == db_path.resolve()
    assert _resolve_graph_path(rt_root, None) == db_path.resolve()
    assert _is_canonical_assertion_row({"canonical_status": "candidate", "canonical_eligible": 0}) is False
    assert _is_canonical_assertion_row({"currentness_state": "superseded"}) is False
    assert _is_canonical_assertion_row({"superseded_by_assertion_id": "asrt_99"}) is False

    out_dir = tmp_path / "radar_out"
    rc = cli_main(["--runtime-root", str(rt_root), "--output-dir", str(out_dir), "--radar-date", "10/30/2026"])
    assert rc == 0
    assert (out_dir / "radar_10-30-2026.md").is_file()
    assert (out_dir / "radar_10-30-2026_report.json").is_file()

    rc_json = cli_main(["--graph", str(db_path), "--output-dir", str(out_dir), "--json"])
    assert rc_json == 0


def test_watchdog_adapter_modes_and_testing_gaps_helper(tmp_path: Path) -> None:
    """Verifies run_corner_foresight_blindspot_scout and run_testing_gaps_assurance across modes."""
    db_path = tmp_path / "graph.db"
    _seed_foresight_sandbox_db(db_path, include_orphans=False)
    (tmp_path / "expiring_commitment.json").write_text("{}", encoding="utf-8")

    sched_ctx = AuthorityContext(
        task_id="corner_foresight_blindspot_scout",
        run_id="r-sched",
        execution_mode=ExecutionMode.SCHEDULED,
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        actor="scheduler",
    )
    res_sched = run_corner_foresight_blindspot_scout(sched_ctx, tmp_path, tmp_path / "run_sched")
    assert res_sched.terminal_outcome == "HITL_REQUIRED"
    assert res_sched.metadata["zero_mutation_verified"] is True

    inter_ctx = AuthorityContext(
        task_id="corner_foresight_blindspot_scout",
        run_id="r-inter",
        execution_mode=ExecutionMode.INTERACTIVE,
        trigger_origin=TriggerOrigin.MANUAL_OPERATOR,
        actor="operator",
    )
    res_await = run_corner_foresight_blindspot_scout(inter_ctx, tmp_path, tmp_path / "run_await", approval=None)
    assert res_await.terminal_outcome == "AWAITING_APPROVAL"

    res_approved = run_corner_foresight_blindspot_scout(
        inter_ctx, tmp_path, tmp_path / "run_approved", approval={"approved": True}
    )
    assert res_approved.terminal_outcome == "COMPLETE"

    # Verify _collect_untested_modules and _resolve_watchdog_outcome helpers
    dp_dir = tmp_path / "dual_pipeline"
    dp_dir.mkdir(parents=True)
    (dp_dir / "__init__.py").write_text("", encoding="utf-8")
    (dp_dir / "sample_mod.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "untested_hotspot.py").write_text("y = 2\n", encoding="utf-8")
    untested = _collect_untested_modules(tmp_path)
    assert len(untested) == 2
    assert _resolve_watchdog_outcome(inter_ctx, False, None) == "COMPLETE"
    tg_ctx = AuthorityContext(
        task_id="testing_gaps_assurance",
        run_id="r-tg",
        execution_mode=ExecutionMode.SCHEDULED,
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        actor="scheduler",
    )
    res_tg = run_testing_gaps_assurance(tg_ctx, tmp_path, tmp_path / "run_tg")
    assert res_tg.terminal_outcome == "HITL_REQUIRED"

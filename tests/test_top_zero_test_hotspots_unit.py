"""Unit & boundary isolation tests for Top Zero-Test Hotspots and HIST-10 timeout completeness."""
from __future__ import annotations

import json
import math
import sqlite3
import struct
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.learning_engine_core.option_nuance import (
    _build_synthesized_entry,
    _collect_option_stats,
    _format_healing_option_entry,
    _is_eligible_human_record,
    recalibrate_post_learning_confidences_core,
    synthesize_hitl_option_nuance,
)
from dual_pipeline.live_meeting_recorder_core.ingest_worktree_cleanup import (
    _auto_commit_worktree_deliverables,
)
from tools.scheduled_task_core.adapters_core.watchdogs import (
    run_agentic_repo_best_practices_scout,
    run_corner_foresight_blindspot_scout,
    run_e2e_critical_functionality_assurance,
    run_evals_surface_assurance,
    run_frontier_llm_research_scout,
    run_hotspot_value_enhancement,
    run_retrieval_surface_assurance,
    run_system_learning_assurance,
    run_testing_gaps_assurance,
    run_third_party_tool_consolidation,
    run_timeout_lock_watchdog,
    run_tokenomics_cost_optimization,
)
from tools.scheduled_task_core.authority import AuthorityContext
from tools.scheduled_task_core.contracts import ExecutionMode, TriggerOrigin
from tools.tier2_dedup_core.sidecar_vector_recovery import (
    _deterministic_unit_vector,
    _ensure_missing_dense_vectors,
    _tokenize_retrieval_text,
    dispatch_special_hygiene_cli,
    remediate_additional_hygiene_fixes,
    repair_falsely_historical_merged_assertions,
)


def test_auto_commit_worktree_deliverables_enforces_subprocess_timeout(tmp_path: Path) -> None:
    """HIST-10: Verifies _auto_commit_worktree_deliverables passes explicit timeout to subprocess.run."""
    captured_timeouts: list[float | None] = []

    def _fake_run(cmd, *args, **kwargs):
        captured_timeouts.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    with patch(
        "dual_pipeline.live_meeting_recorder_core.ingest_worktree_cleanup.subprocess.run",
        autospec=True,
        side_effect=_fake_run,
    ):
        ok = _auto_commit_worktree_deliverables(tmp_path, "ingest/meeting-nexus-test")

    assert ok is True
    assert len(captured_timeouts) >= 1
    assert all(t is not None and t > 0 for t in captured_timeouts)


def test_deterministic_unit_vector_and_tokenizer_boundaries() -> None:
    """Verifies _deterministic_unit_vector unit L2 norm and _tokenize_retrieval_text boundary filters."""
    blob = _deterministic_unit_vector("seed-alpha", dim=64)
    assert len(blob) == 64 * 4
    vals = struct.unpack("<64f", blob)
    norm = math.sqrt(sum(v * v for v in vals))
    assert abs(norm - 1.0) < 1e-5
    assert blob == _deterministic_unit_vector("seed-alpha", dim=64)
    assert blob != _deterministic_unit_vector("seed-beta", dim=64)

    empty_counts = _tokenize_retrieval_text("a ! @ # " + ("x" * 45))
    assert empty_counts == {"record": 1}

    valid_counts = _tokenize_retrieval_text("Nexus pipeline nexus SOW_2026")
    assert valid_counts["nexus"] == 2
    assert valid_counts["pipeline"] == 1


def test_ensure_missing_dense_vectors_backfills_dense_sparse_and_colbert(tmp_path: Path) -> None:
    """Verifies _ensure_missing_dense_vectors populates dense, sparse, and ColBERT tables in sidecar DB."""
    db_path = tmp_path / "sidecar.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
            INSERT INTO metadata VALUES ('colbert_dimensions', '32');
            CREATE TABLE retrieval_record (
                record_id TEXT PRIMARY KEY,
                retrieval_text_hash TEXT,
                artifact_json TEXT
            );
            CREATE TABLE dense_vector (record_id TEXT PRIMARY KEY, dimensions INTEGER, vector BLOB);
            CREATE TABLE sparse_weight (record_id TEXT, token TEXT, weight REAL, PRIMARY KEY (record_id, token));
            CREATE TABLE colbert_token (
                record_id TEXT, position INTEGER, token TEXT, dimensions INTEGER, vector BLOB,
                PRIMARY KEY (record_id, position)
            );
            """
        )
        unit_blob = struct.pack("<1024f", 1.0, *([0.0] * 1023))
        conn.execute(
            "INSERT INTO retrieval_record VALUES ('rec-1', 'hash-1', ?)",
            (json.dumps({"retrieval_text": "Grid modernization Substation alpha"}),),
        )
        conn.execute("INSERT INTO dense_vector VALUES ('rec-1', 1024, ?)", (unit_blob,))
        conn.execute("INSERT INTO sparse_weight VALUES ('rec-1', 'entity', 1.0)")
        conn.execute("INSERT INTO colbert_token VALUES ('rec-1', 0, 'entity', 32, ?)", (b"\x00" * 128,))

        _ensure_missing_dense_vectors(conn, ["rec-1", "rec-2"])

        dv_rows = conn.execute("SELECT record_id, dimensions, vector FROM dense_vector ORDER BY record_id").fetchall()
        sw_rows = conn.execute("SELECT record_id, token, weight FROM sparse_weight ORDER BY record_id, token").fetchall()
        cb_rows = conn.execute("SELECT record_id, position, token, dimensions FROM colbert_token").fetchall()

    assert [r[0] for r in dv_rows] == ["rec-1", "rec-2"]
    assert all(r[2] != unit_blob for r in dv_rows)
    assert not any(r[1] == "entity" and r[2] == 1.0 for r in sw_rows)
    assert any(r[0] == "rec-1" and r[1] == "grid" for r in sw_rows)
    assert any(r[0] == "rec-1" and r[3] == 32 for r in cb_rows)


def test_remediate_additional_hygiene_fixes_and_redirect_restoration(tmp_path: Path) -> None:
    """Verifies repair_falsely_historical_merged_assertions and remediate_additional_hygiene_fixes."""
    db_path = tmp_path / "graph.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE node (node_id TEXT PRIMARY KEY, node_type TEXT, active INTEGER, status TEXT, updated_at TEXT);
            CREATE TABLE entity_merge_event (merge_event_id TEXT PRIMARY KEY, merged_node_id TEXT, canonical_node_id TEXT);
            CREATE TABLE entity_redirect (
                from_node_id TEXT, to_node_id TEXT, merge_event_id TEXT, active INTEGER, created_at TEXT
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY, subject_node_id TEXT, predicate TEXT, object_node_id TEXT,
                scope_text TEXT, canonical_status TEXT, canonical_eligible INTEGER, retrieval_eligible INTEGER,
                currentness_state TEXT, validation_state TEXT, approval_reference TEXT, confidence_score REAL,
                primary_source_id TEXT, superseded_by_assertion_id TEXT, superseded_at TEXT,
                created_at TEXT, updated_at TEXT
            );
            CREATE TABLE assertion_evidence (
                assertion_id TEXT, evidence_id TEXT, evidence_relationship TEXT, created_at TEXT
            );
            CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY, source_id TEXT);
            CREATE TABLE edge (
                edge_id TEXT PRIMARY KEY, from_node_id TEXT, edge_type TEXT, to_node_id TEXT,
                description TEXT, source_id TEXT
            );
            CREATE TABLE legacy_edge_assertion_map (
                edge_id TEXT PRIMARY KEY, assertion_id TEXT, migration_confidence REAL,
                migration_state TEXT, created_at TEXT
            );
            CREATE TABLE search_document (
                search_rowid INTEGER PRIMARY KEY AUTOINCREMENT, search_document_id TEXT,
                object_type TEXT, object_id TEXT, assertion_id TEXT, title TEXT, body TEXT,
                canonical_status TEXT, currentness_state TEXT, source_id TEXT,
                retrieval_eligible INTEGER, updated_at TEXT
            );
            CREATE TABLE audit_event (
                audit_event_id TEXT PRIMARY KEY, operator_signature TEXT, approval_reference TEXT
            );
            CREATE TABLE graph_quality_exception (
                exception_id TEXT PRIMARY KEY, exception_type TEXT, object_id TEXT, active INTEGER
            );
            """
        )
        conn.execute("INSERT INTO node VALUES ('n-loser', 'entity', 0, 'inactive', '2026-01-01')")
        conn.execute("INSERT INTO node VALUES ('n-winner', 'entity', 1, 'canonical', '2026-01-01')")
        conn.execute("INSERT INTO entity_merge_event VALUES ('me-1', 'n-loser', 'n-winner')")
        conn.execute("INSERT INTO evidence_item VALUES ('ev-1', 'src-real')")
        conn.execute(
            "INSERT INTO assertion VALUES ('asrt-1', 'n-loser', 'OWNS', 'n-winner', 'scope', "
            "'historical', 0, 0, 'historical', 'needs_review', NULL, 0.9, 'source-2af61f674935afe8672a6881', NULL, NULL, '2026-01-01', '2026-01-01')"
        )
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-1', 'ev-1', 'supports', '2026-01-01')")
        conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-orphan', 'ev-1', 'supports', '2026-01-01')")
        conn.execute("INSERT INTO edge VALUES ('edge-1', 'n-winner', 'DEPENDS_ON', 'n-loser', 'dep', 'src-real')")
        conn.execute(
            "INSERT INTO search_document (search_document_id, object_type, object_id, assertion_id, title, body, canonical_status, currentness_state, source_id, retrieval_eligible, updated_at) "
            "VALUES ('sd-1', 'assertion', 'asrt-1', 'asrt-1', 't', 'b', 'historical', 'historical', 'src-old', 1, '2026-01-01')"
        )
        conn.execute("INSERT INTO audit_event VALUES ('aud-1', '', '')")
        conn.execute(
            "INSERT INTO graph_quality_exception VALUES ('exc-1', 'HYGIENE_KEEP_DISTINCT', 'n-loser::n-winner', 1)"
        )

        pkg = {
            "findings": [
                {"proposed_fix": "deactivate_search_documents"},
                {"proposed_fix": "synthesize_edge_assertion", "raw_data": {"edge_id": "edge-1"}},
                {"proposed_fix": "quarantine_audit_record", "raw_data": {"audit_event_id": ["aud-1"]}},
                {"proposed_fix": "run_graph_gc"},
                {"proposed_fix": "repair_historical_merged_assertions"},
            ]
        }
        sample_ids: list[str] = []
        res = remediate_additional_hygiene_fixes(conn, pkg, "2026-10-27T00:00:00Z", sample_ids)
        aud_sig = conn.execute("SELECT operator_signature FROM audit_event WHERE audit_event_id='aud-1'").fetchone()[0]
        exc_act = conn.execute("SELECT active FROM graph_quality_exception WHERE exception_id='exc-1'").fetchone()[0]

    assert res["edge_assertions_synthesized"] == 1
    assert res["audit_records_quarantined"] == 1
    assert res["graph_gc_records_cleaned"] >= 1
    assert aud_sig == "QUARANTINED_BY_HYGIENE_COUNCIL"
    assert exc_act == 0
    assert len(sample_ids) >= 2


def test_option_nuance_filtering_and_synthesis(tmp_path: Path) -> None:
    """Verifies option_nuance filters autonomous records and synthesizes calibrated Bayesian option entries."""
    assert _is_eligible_human_record("not-a-dict") is False
    assert _is_eligible_human_record({"metadata": {"operator_class": "AUTONOMOUS"}}) is False
    assert _is_eligible_human_record({"operator_id": "autonomous_engine"}) is False
    assert _is_eligible_human_record({"operator_id": "operator_human"}) is True

    history = [
        {
            "operator_id": "human_op",
            "category": "ingestion",
            "selected_option": "Approve Wave 1",
            "options": [{"label": "Approve Wave 1"}, {"label": "Defer Wave 1"}],
            "outcome_type": "AFFIRMED",
            "rationale": "Strong evidence",
        },
        {
            "operator_id": "human_op",
            "category": "ingestion",
            "selected_option": "Approve Wave 1",
            "options": ["Approve Wave 1", "Defer Wave 1"],
            "outcome_type": "OVERRIDDEN",
            "rationale": "Confirmed second run",
        },
    ]
    stats = _collect_option_stats(history)
    assert len(stats) == 2
    built = {k: _build_synthesized_entry(k, v) for k, v in stats.items()}
    affirmed_entry = next(v for v in built.values() if v and v["affirmed_count"] == 2)
    rejected_entry = next(v for v in built.values() if v and v["rejected_count"] == 2)
    assert affirmed_entry["posterior_confidence"] > rejected_entry["posterior_confidence"]
    assert "2 of 2" in affirmed_entry["plain_english_summary"]

    heal_entry = _format_healing_option_entry(
        "repair_date",
        {"total_count": 5, "success_count": 5, "failure_count": 0, "posterior_confidence": 0.91},
    )
    assert heal_entry is not None
    assert heal_entry["posterior_tier"] == "HIGH"

    ledger_path = tmp_path / "hitl_ledger.json"
    store = HITLDecisionStore(ledger_path=ledger_path)
    store.record_decision(
        decision_id="q-1",
        prompt="Proceed with Wave 1?",
        selected_option="Approve Wave 1",
        operator_id="human_operator",
        category="governance",
        options=["Approve Wave 1", "Skip Wave 1"],
        rationale="Verified metrics",
    )
    out_store = tmp_path / "learned_store.json"
    payload = synthesize_hitl_option_nuance(ledger_path, out_store)
    assert out_store.is_file()
    assert payload["total_learned_options"] >= 1

    rt_root = tmp_path / "runtime"
    (rt_root / "config" / "runtime").mkdir(parents=True)
    recal = recalibrate_post_learning_confidences_core(
        runtime_root=rt_root,
        proposals_dir=tmp_path / "proposals",
        hitl_ledger_path=ledger_path,
    )
    assert recal["status"] == "RECALIBRATED"
    assert (rt_root / "config" / "runtime" / "learned_hitl_option_store.json").is_file()


def test_scheduled_task_watchdog_adapters_interactive_and_scheduled(tmp_path: Path) -> None:
    """Verifies all watchdog task adapters in watchdogs.py across clean and triggered states."""
    ctx_sched = AuthorityContext(
        task_id="testing_gaps_assurance",
        run_id="run_01",
        trigger_origin=TriggerOrigin.SCHEDULER_CRON,
        execution_mode=ExecutionMode.SCHEDULED,
        actor="scheduler",
        is_scheduled=True,
    )
    ctx_inter = AuthorityContext(
        task_id="testing_gaps_assurance",
        run_id="run_02",
        trigger_origin=TriggerOrigin.MANUAL_OPERATOR,
        execution_mode=ExecutionMode.INTERACTIVE,
        actor="operator_human",
        is_scheduled=False,
    )
    approval = {"approval_id": "app-1", "operator": "operator_human", "status": "APPROVED"}

    # 1. Clean runs -> COMPLETE
    for fn in (
        run_timeout_lock_watchdog,
        run_third_party_tool_consolidation,
        run_testing_gaps_assurance,
        run_frontier_llm_research_scout,
        run_e2e_critical_functionality_assurance,
        run_corner_foresight_blindspot_scout,
        run_hotspot_value_enhancement,
        run_tokenomics_cost_optimization,
        run_agentic_repo_best_practices_scout,
        run_retrieval_surface_assurance,
        run_system_learning_assurance,
        run_evals_surface_assurance,
    ):
        res = fn(ctx_sched, tmp_path / "clean_ws", tmp_path / "runs" / fn.__name__)
        assert res.terminal_outcome == "COMPLETE"

    # 2. Triggered runs -> HITL_REQUIRED in scheduled mode, COMPLETE with approval in interactive mode
    trig_ws = tmp_path / "trig_ws"
    trig_ws.mkdir(parents=True)
    for trigger_name in (
        "stale.lock",
        "redundant_wrapper.py",
        "untested_hotspot.py",
        "candidate_innovation.json",
        "broken_e2e_handoff.py",
        "expiring_commitment.json",
        "fragile_hotspot.py",
        "unoptimized_token_hotspots.json",
        "candidate_agentic_repo_pattern.json",
        "retrieval_surface_gap.json",
        "system_learning_gap.json",
        "evals_surface_gap.json",
    ):
        (trig_ws / trigger_name).write_text("{}", encoding="utf-8")

    res_sched = run_testing_gaps_assurance(ctx_sched, trig_ws, tmp_path / "runs" / "sched_trig")
    assert res_sched.terminal_outcome == "HITL_REQUIRED"
    assert res_sched.implementation_plan_path is not None

    res_inter = run_testing_gaps_assurance(ctx_inter, trig_ws, tmp_path / "runs" / "inter_trig", approval=approval)
    assert res_inter.terminal_outcome == "COMPLETE"

    assert dispatch_special_hygiene_cli(SimpleNamespace(
        promote_remaining_scopes=False,
        promote_five_projects_sources=False,
        promote_neet_sources=False,
        promote_sherpa_sources=False,
        promote_nexus_sources=False,
        promote_dociq_sources=False,
        promote_jarvis_sources=False,
    )) is None

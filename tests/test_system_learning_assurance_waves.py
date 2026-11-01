"""
tests/test_system_learning_assurance_waves.py

Counterfactual and regression tests for System Learning Assurance Wave 1 (GAP-01..GAP-04)
and Wave 2 (ENH-01..ENH-02).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from dual_pipeline.core import read_json, write_json
from dual_pipeline.learning_engine import ClosedLoopLearningEngine, verify_post_apply_canary_and_seal
from dual_pipeline.learning_engine_core.rollback import record_rollback_snapshot
from tools.scheduled_task_core.adapters_core.learning_assurance_engine import SystemLearningAssuranceEngine

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_gap02_assurance_engine_reports_nonzero_emitted_proposals(tmp_path: Path) -> None:
    """GAP-02: SystemLearningAssuranceEngine.evaluate() must report total_proposals_emitted matching generated proposals."""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    # Seed 2 telemetry failures so unified cycle generates at least 1 proposal
    tel_dir = ws / "telemetry"
    tel_dir.mkdir(parents=True, exist_ok=True)
    failures = [
        {"run_id": "r1", "stage": "L2", "status": "FAILED", "error_code": "ERR_TEST_COHORT"},
        {"run_id": "r2", "stage": "L2", "status": "FAILED", "error_code": "ERR_TEST_COHORT"},
    ]
    (tel_dir / "runs.jsonl").write_text("\n".join(json.dumps(x) for x in failures) + "\n", encoding="utf-8")

    run_dir = tmp_path / "run_out"
    engine = SystemLearningAssuranceEngine(workspace_root=ws, run_dir=run_dir)
    res = engine.evaluate()
    summary = res["unified_cycle_summary"]
    assert summary["surfaces_scanned"] == 8
    assert summary["total_proposals_emitted"] >= 1
    assert len(summary["proposal_ids"]) == summary["total_proposals_emitted"]


def test_gap03_cli_unified_cycle_storage_dir_isolation_and_corrections_surface(tmp_path: Path) -> None:
    """GAP-03: govern_learning_proposals.py unified-cycle --storage-dir must isolate proposals to <storage_dir>/proposals and include operator corrections."""
    storage_dir = tmp_path / "isolated_runtime"
    ledger_dir = storage_dir / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    corr_file = ledger_dir / "operator_corrections.jsonl"
    corrections = [
        {"correction_id": "corr-001", "category": "entity_resolution", "source_identifier": "person:jon", "corrected_identifier": "person:john_doe", "operator_id": "op1", "session_id": "sess-001"},
        {"correction_id": "corr-002", "category": "entity_resolution", "source_identifier": "person:jon", "corrected_identifier": "person:john_doe", "operator_id": "op1", "session_id": "sess-002"},
        {"correction_id": "corr-003", "category": "entity_resolution", "source_identifier": "person:jon", "corrected_identifier": "person:john_doe", "operator_id": "op2", "session_id": "sess-003"},
    ]
    corr_file.write_text("\n".join(json.dumps(c) for c in corrections) + "\n", encoding="utf-8")

    tracked_proposals_dir = REPO_ROOT / "reports" / "learning_proposals"
    before_files = set(tracked_proposals_dir.glob("*.json")) if tracked_proposals_dir.exists() else set()

    cmd = [
        sys.executable,
        str(REPO_ROOT / "tools" / "govern_learning_proposals.py"),
        "unified-cycle",
        "--storage-dir",
        str(storage_dir),
        "--min-occurrences",
        "2",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr

    after_files = set(tracked_proposals_dir.glob("*.json")) if tracked_proposals_dir.exists() else set()
    assert after_files == before_files, "CLI --storage-dir leaked proposal files into tracked reports/learning_proposals/"

    isolated_props = list((storage_dir / "proposals").glob("*.json"))
    assert len(isolated_props) >= 1, "Expected isolated proposals in <storage_dir>/proposals"


def test_gap04_canary_regression_restores_snapshot_and_seals_applied_receipt(tmp_path: Path) -> None:
    """GAP-04: verify_post_apply_canary_and_seal must pass proposals_dir so rollback restores snapshot and marks applied receipt ROLLED_BACK."""
    runtime_dir = tmp_path / "runtime"
    proposals_dir = runtime_dir / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)
    engine = ClosedLoopLearningEngine(runtime_root=runtime_dir, proposals_dir=proposals_dir)

    proposal_id = "lcp-canary-rollback-test"
    active_params_path = runtime_dir / "active_parameters.json"
    write_json(active_params_path, {"retrieval.hybrid_weight_bm25": 0.55})

    proposal = {
        "proposal_id": proposal_id,
        "proposal_type": "parameters",
    }
    record_rollback_snapshot(
        proposals_dir,
        proposal,
        {"parameter_name": "retrieval.hybrid_weight_bm25", "previous_value": 0.40},
    )
    applied_receipt_path = proposals_dir / f"{proposal_id}_applied_receipt.json"
    write_json(
        applied_receipt_path,
        {
            "receipt_id": "rcpt-apply-1",
            "proposal_id": proposal_id,
            "status": "APPLIED",
        },
    )

    res = verify_post_apply_canary_and_seal(
        engine,
        proposal_id=proposal_id,
        baseline_metrics={"mrr": 0.85, "ndcg": 0.82},
        canary_metrics={"mrr": 0.78, "ndcg": 0.75},
    )
    assert res["status"] == "ROLLED_BACK"
    applied_after = read_json(applied_receipt_path)
    assert applied_after["status"] == "ROLLED_BACK"
    assert "rolled_back_at" in applied_after
    restored_params = read_json(active_params_path)
    assert restored_params.get("retrieval.hybrid_weight_bm25") == 0.40


def test_enh01_unified_cycle_auto_simulation_and_veto_counts(tmp_path: Path) -> None:
    """ENH-01 (ROI_learn=3.43x): run_unified_learning_cycle returns simulated_pass_count, vetoed_count, and corrections_proposals_count."""
    runtime_dir = tmp_path / "runtime_enh01"
    ledger_dir = runtime_dir / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    corr_file = ledger_dir / "operator_corrections.jsonl"
    corrections = [
        {"correction_id": "corr-101", "category": "entity_resolution", "source_identifier": "person:al", "corrected_identifier": "person:alice", "operator_id": "op1", "session_id": "sess-101"},
        {"correction_id": "corr-102", "category": "entity_resolution", "source_identifier": "person:al", "corrected_identifier": "person:alice", "operator_id": "op1", "session_id": "sess-102"},
    ]
    corr_file.write_text("\n".join(json.dumps(c) for c in corrections) + "\n", encoding="utf-8")

    proposals_dir = runtime_dir / "proposals"
    engine = ClosedLoopLearningEngine(runtime_root=runtime_dir, proposals_dir=proposals_dir)
    res = engine.run_unified_learning_cycle(
        min_occurrences=2,
        include_audio_voice=False,
        include_reconciler=False,
        include_corrections=True,
    )
    assert res["status"] == "PASS"
    assert res["corrections_proposals_count"] == 1
    assert res["total_proposals_generated"] >= 1
    assert res["total_proposals_emitted"] == res["total_proposals_generated"]
    assert res["simulated_pass_count"] + res["vetoed_count"] == res["total_proposals_generated"]
    assert res["simulated_pass_count"] >= 1
    for pid in res["proposal_ids"]:
        assert (proposals_dir / f"{pid}_simulation_receipt.json").is_file()


def test_enh02_cli_verify_canary_ndcg_regression_restores_snapshot(tmp_path: Path) -> None:
    """ENH-02 (ROI_learn=3.15x): govern_learning_proposals.py verify-canary with --baseline-ndcg / --canary-ndcg triggers rollback and restores snapshot."""
    storage_dir = tmp_path / "canary_storage"
    proposals_dir = storage_dir / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)
    proposal_id = "lcp-ndcg-canary-cli"
    active_params_path = storage_dir / "active_parameters.json"
    write_json(active_params_path, {"retrieval.hybrid_weight_colbert": 0.45})

    record_rollback_snapshot(
        proposals_dir,
        {"proposal_id": proposal_id, "proposal_type": "parameters"},
        {"parameter_name": "retrieval.hybrid_weight_colbert", "previous_value": 0.25},
    )
    applied_path = proposals_dir / f"{proposal_id}_applied_receipt.json"
    write_json(applied_path, {"receipt_id": "rcpt-ndcg", "proposal_id": proposal_id, "status": "APPLIED"})

    cmd = [
        sys.executable,
        str(REPO_ROOT / "tools" / "govern_learning_proposals.py"),
        "verify-canary",
        "--proposal-id",
        proposal_id,
        "--baseline-mrr",
        "0.85",
        "--canary-mrr",
        "0.85",
        "--baseline-ndcg",
        "0.80",
        "--canary-ndcg",
        "0.72",
        "--storage-dir",
        str(storage_dir),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr
    assert "Status=ROLLED_BACK" in proc.stdout

    restored = read_json(active_params_path)
    assert restored.get("retrieval.hybrid_weight_colbert") == 0.25
    assert read_json(applied_path)["status"] == "ROLLED_BACK"


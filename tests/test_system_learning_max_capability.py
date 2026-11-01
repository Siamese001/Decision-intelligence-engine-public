"""Counterfactual, End-to-End, and Adversarial Verification for 8-Surface System Learning Max Capability."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from dual_pipeline.answer_ledger_core.feedback import synthesize_feedback_threshold_proposal
from dual_pipeline.learning_adapters.correction_feedback_ingestor import CorrectionFeedbackIngestor
from dual_pipeline.learning_adapters.live_pipeline_integrator import LivePipelineIntegrator
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.learning_loop_core.scanner import scan_feedback_for_proposals
from dual_pipeline.transcript_qa_core.reconciliation_agent import CrossArtifactReconciliationAgent
from tools.scheduled_task_core.adapters_core.learning_assurance_engine import (
    EIGHT_SYSTEM_LEARNING_SURFACES,
    SystemLearningAssuranceEngine,
    _inspect_proposals_dir,
)
from tools.scheduled_task_core.adapters_core.watchdogs import run_system_learning_assurance

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_gap_01_text_mode_reconciliation_streams_raw_alias_pair_and_scans_outcomes(tmp_path: Path) -> None:
    """Verify text-mode reconciliation repairs Output 02/03 owner drift, streams raw_alias_pair, and synthesizes alias proposals."""
    integrator = LivePipelineIntegrator(runtime_root=tmp_path)
    agent = CrossArtifactReconciliationAgent(integrator=integrator, stream_outcomes=True)
    for _ in range(2):
        res = agent.reconcile(
            output_01_text="**Owner:** Robert Smith will deliver the API.",
            output_02_text="**Owner:** Bob Smith will deliver the API.",
            output_03_text="**Owner:** Bobby Smith will deliver the API.",
        )
        assert res.output_01_immutable_verified is True
        assert "Robert Smith" in res.repaired_output_02

    engine = ClosedLoopLearningEngine(storage_dir=tmp_path)
    proposals = engine.scan_reconciler_outcomes(min_occurrences=2)
    assert len(proposals) >= 1
    ext = proposals[0]["extensions"]["com.partner_advisory.nee.learning"]
    assert ext["from_id"] == "Bob Smith"
    assert ext["to_id"] == "Robert Smith"
    assert ext["raw_alias_pair"] == "Bob Smith->Robert Smith"

    # Also verify fallback extraction when raw_alias_pair is omitted and only from_id/to_id exist
    fallback_log = tmp_path / "fallback_events.jsonl"
    fallback_rows = [
        {"from_id": "Mitch", "to_id": "Mitchell", "run_id": "run-01"},
        {"from_id": "Mitch", "to_id": "Mitchell", "run_id": "run-02"},
        {"from_id": "SingleOnly", "to_id": "TargetOnly", "run_id": "run-03"},
    ]
    fallback_log.write_text("\n".join(json.dumps(r) for r in fallback_rows) + "\n\n", encoding="utf-8")
    fb_props = engine.scan_reconciler_outcomes(events_log_path=fallback_log, min_occurrences=2)
    assert len(fb_props) == 1
    assert fb_props[0]["extensions"]["com.partner_advisory.nee.learning"]["from_id"] == "Mitch"

    # Non-existent log returns empty list
    assert engine.scan_reconciler_outcomes(events_log_path=tmp_path / "missing.jsonl") == []


def test_gap_02_negative_answer_feedback_proposal_signed_provenance_and_simulation(tmp_path: Path) -> None:
    """Verify negative answer feedback synthesizes a signed learning-change-proposal that passes simulation."""
    assert synthesize_feedback_threshold_proposal("ans-ok", "useful") is None

    prop = synthesize_feedback_threshold_proposal(
        answer_id="ans-neg-1",
        rating="wrong",
        note="unanchored claim",
        proposals_dir=tmp_path / "proposals",
        current_threshold=0.60,
        delta=0.05,
    )
    assert prop is not None
    assert prop["auto_apply"] is False
    assert prop["approval_required"] is True

    engine = ClosedLoopLearningEngine(storage_dir=tmp_path, proposals_dir=tmp_path / "proposals")
    ok_prov, reason = engine.verify_provenance(prop)
    assert ok_prov is True, reason

    sim = engine.simulate_proposal(prop)
    assert sim.status == "SIMULATED_PASS"
    assert sim.success is True


def test_gap_03_and_enh_03_governed_cli_audio_corrections_and_unified_cycle(tmp_path: Path) -> None:
    """Verify CLI subcommands audio-scan, corrections-scan, and unified-cycle execute deterministically."""
    ingestor = CorrectionFeedbackIngestor(runtime_root=tmp_path, min_confirmations=2)
    ingestor.record_entity_override("Bob S.", "Robert Smith", operator_id="op1", session_id="sess-01")
    ingestor.record_entity_override("Bob S.", "Robert Smith", operator_id="op2", session_id="sess-02")

    proposals_dir = tmp_path / "proposals"
    for cmd_args in (
        ["audio-scan", "--storage-dir", str(tmp_path), "--min-occurrences", "2", "--min-distinct-sources", "2"],
        ["corrections-scan", "--storage-dir", str(tmp_path), "--min-cohort-size", "2"],
        ["unified-cycle", "--storage-dir", str(tmp_path), "--min-occurrences", "2"],
    ):
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "tools" / "govern_learning_proposals.py"),
                "--proposals-dir",
                str(proposals_dir),
                *cmd_args,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"Command {cmd_args} failed: {proc.stderr}"

    written_proposals = list(proposals_dir.glob("*.json"))
    assert len(written_proposals) >= 1


def test_gap_04_all_five_cohort_scanner_proposal_types_pass_simulation(tmp_path: Path) -> None:
    """Verify all 5 cohort types emitted by scan_feedback_for_proposals pass ClosedLoopLearningEngine.simulate_proposal."""
    cohort_rows = []
    for ft in (
        "TEMPORAL_STALENESS",
        "OWNER_DRIFT",
        "GROUNDING_REJECTION",
        "JUDGE_RETRY_EXHAUSTION",
        "RETRIEVAL_MISS",
    ):
        for idx in range(3):
            cohort_rows.append({
                "failure_type": ft,
                "event_id": f"ev-{ft.lower()}-{idx}",
                "from_alias": "Jonathon",
                "to_canonical": "Jonathan",
                "suggested_decay_tau": 240.0,
            })

    jsonl_path = tmp_path / "feedback_cohorts.jsonl"
    jsonl_path.write_text("\n".join(json.dumps(r) for r in cohort_rows) + "\n", encoding="utf-8")

    proposals = scan_feedback_for_proposals(
        jsonl_path,
        min_cohort_size=3,
        proposals_dir=tmp_path / "proposals",
    )
    assert len(proposals) == 5

    engine = ClosedLoopLearningEngine(storage_dir=tmp_path, proposals_dir=tmp_path / "proposals")
    for prop in proposals:
        sim = engine.simulate_proposal(prop)
        assert sim.status == "SIMULATED_PASS", f"Failed simulation for {prop['proposal_id']}: {sim.details}"


def test_gap_05_and_enh_02_eight_surface_learning_assurance_engine_and_adversarial_proposals(tmp_path: Path) -> None:
    """Verify SystemLearningAssuranceEngine evaluates all 8 surfaces and catches unsafe or unsealed proposals."""
    assert len(EIGHT_SYSTEM_LEARNING_SURFACES) == 8

    ctx = SimpleNamespace(
        task_id="system-learning-assurance-agent",
        run_id="run-clean-01",
        approval=None,
        execution_mode="interactive",
        is_scheduled=False,
        trigger_origin="manual",
    )
    clean_res = run_system_learning_assurance(ctx, tmp_path, tmp_path / "run_clean")
    assert clean_res.terminal_outcome == "COMPLETE"
    assert clean_res.metadata["surfaces_evaluated"] == 8
    assert clean_res.metadata["unified_cycle_summary"]["surfaces_scanned"] == 8

    # Adversarial check: inject an unsafe auto_apply=True proposal without signed provenance
    bad_proposals_dir = tmp_path / "proposals"
    bad_proposals_dir.mkdir(parents=True, exist_ok=True)
    (bad_proposals_dir / "bad_prop.json").write_text(
        json.dumps({
            "proposal_id": "lcp-bad-unsafe-01",
            "auto_apply": True,
            "approval_required": False,
            "extensions": {},
        }),
        encoding="utf-8",
    )
    (bad_proposals_dir / "ignored_receipt.json").write_text("{}", encoding="utf-8")
    (bad_proposals_dir / "corrupt.json").write_text("{not-json", encoding="utf-8")
    (bad_proposals_dir / "non_proposal.json").write_text(json.dumps({"other": 1}), encoding="utf-8")

    findings = _inspect_proposals_dir(bad_proposals_dir)
    issues = {f["issue"] for f in findings}
    assert "UNSAFE_AUTO_APPLY_LEARNING_PROPOSAL" in issues
    assert "UNSEALED_LEARNING_PROPOSAL_PROVENANCE" in issues

    adv_engine = SystemLearningAssuranceEngine(workspace_root=tmp_path, run_dir=None)
    adv_eval = adv_engine.evaluate()
    assert adv_eval["status"] == "FINDINGS_SURFACED"
    assert len(adv_eval["actionable_findings"]) >= 2
    assert adv_eval["unified_cycle_summary"]["surfaces_scanned"] == 8

    # Also exercise system_learning_gap.json trigger and scheduled-mode HITL_REQUIRED branch
    (tmp_path / "system_learning_gap.json").write_text("{}", encoding="utf-8")
    sched_ctx = SimpleNamespace(
        task_id="system-learning-assurance-agent",
        run_id="run-sched-01",
        approval=None,
        execution_mode="scheduled",
        is_scheduled=True,
        trigger_origin="cron",
    )
    sched_res = run_system_learning_assurance(sched_ctx, tmp_path, tmp_path / "run_sched")
    assert sched_res.terminal_outcome == "HITL_REQUIRED"


def test_full_branch_and_diff_line_coverage_across_modified_modules(tmp_path: Path, monkeypatch: object) -> None:
    """Exercise remaining boundary branches across reconciliation, scanner, CLI main(), and readiness verifier."""
    import os
    from tools import govern_learning_proposals
    from tools.verify_agent_learning_readiness import check_fleet_and_stage_gate_learning

    # 1. Reconcile when output_01 has no Owner tags (clean no-drift path)
    agent = CrossArtifactReconciliationAgent(stream_outcomes=False)
    clean_rec = agent.reconcile(
        output_01_text="Alice: We completed the milestone on schedule.",
        output_02_text="**Owner:** Bob Smith will deliver the API.",
        output_03_text="**Owner:** Bob Smith will deliver the API.",
    )
    assert clean_rec.output_01_immutable_verified is True

    # 2. scan_feedback_for_proposals with an in-memory list
    list_props = scan_feedback_for_proposals(
        [
            {"failure_type": "OWNER_DRIFT", "from_alias": "Al", "to_canonical": "Alice"},
            {"failure_type": "OWNER_DRIFT", "from_alias": "Al", "to_canonical": "Alice"},
            {"failure_type": "OWNER_DRIFT", "from_alias": "Al", "to_canonical": "Alice"},
        ],
        min_cohort_size=3,
    )
    assert len(list_props) == 1

    # 3. In-process govern_learning_proposals.main() for corrections-scan (with proposals) and list (non-extended)
    ingestor = CorrectionFeedbackIngestor(runtime_root=tmp_path, min_confirmations=2)
    ingestor.record_entity_override("Rob", "Robert", operator_id="op1", session_id="sess-10")
    ingestor.record_entity_override("Rob", "Robert", operator_id="op2", session_id="sess-11")
    props_dir = tmp_path / "inproc_props"
    old_argv = sys.argv[:]
    try:
        sys.argv = [
            "govern_learning_proposals.py",
            "--proposals-dir",
            str(props_dir),
            "corrections-scan",
            "--storage-dir",
            str(tmp_path),
            "--min-cohort-size",
            "2",
        ]
        assert govern_learning_proposals.main() == 0
        sys.argv = ["govern_learning_proposals.py", "--proposals-dir", str(props_dir), "list"]
        assert govern_learning_proposals.main() == 0
    finally:
        sys.argv = old_argv

    # 4. Readiness verifier failure branches in isolated working directory
    prev_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        ok1, msg1 = check_fleet_and_stage_gate_learning()
        assert ok1 is False and "Missing prompt file" in msg1

        p_dir = tmp_path / "automations" / "prompts"
        p_dir.mkdir(parents=True, exist_ok=True)
        sys_p = p_dir / "system_learning_agent.md"
        sys_p.write_text("# Stub without runner token", encoding="utf-8")
        ok2, msg2 = check_fleet_and_stage_gate_learning()
        assert ok2 is False and "govern_learning_proposals.py" in msg2

        sys_p.write_text("# Stub with govern_learning_proposals.py", encoding="utf-8")
        ok3, msg3 = check_fleet_and_stage_gate_learning()
        assert ok3 is False and "Expected" in msg3
    finally:
        os.chdir(prev_cwd)


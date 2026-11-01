"""End-to-end verification of Wave 3 Cross-Artifact Owner Alias Learning and Bi-Temporal Decay Cadence Calibration."""
from __future__ import annotations

from pathlib import Path

from dual_pipeline.calibration import create_signed_calibration_approval_receipt
from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import read_json, utc_now, write_json
from dual_pipeline.learning_adapters.live_pipeline_integrator import LivePipelineIntegrator
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.learning_loop_core import (
    CATEGORY_COPILOT_CONTEXT,
    scan_answer_eval_receipts,
    synthesize_answer_learning_proposal,
)
from dual_pipeline.staleness_core.temporal_drift_agent import (
    TemporalDriftSupersessionAgent,
    compute_empirical_decay_step_seconds,
)
from dual_pipeline.transcript_qa_core.reconciliation_agent import CrossArtifactReconciliationAgent


def test_cross_artifact_owner_drift_to_governed_alias_registration_e2e(tmp_path: Path) -> None:
    """Verifies recurring Output 03 owner drift -> pair-keyed cohort -> alias_addition proposal -> approved registration."""
    integrator = LivePipelineIntegrator(runtime_root=tmp_path)
    reconciler = CrossArtifactReconciliationAgent(integrator=integrator)

    o1_text = (
        "# Verbatim Transcript\n"
        "Marcus Vance: We will finalize the vector index migration by Friday.\n"
        "Elena Rostova: Agreed, Marcus owns the migration sign-off.\n"
    )
    o2_text = "## Action Items\n- Finalize vector index migration (**Owner:** Marcus Vance)\n"
    o3_drifted = "## Executive Action Items\n- Finalize vector index migration (**Owner:** Mark Vance)\n"

    # Run 1 & Run 2 observe recurring "Mark Vance" -> "Marcus Vance" owner drift
    res1 = reconciler.reconcile(output_01_text=o1_text, output_02_text=o2_text, output_03_text=o3_drifted)
    res2 = reconciler.reconcile(output_01_text=o1_text, output_02_text=o2_text, output_03_text=o3_drifted)
    assert res1.output_01_immutable_verified is True
    assert res2.output_01_immutable_verified is True
    assert "**Owner:** Marcus Vance" in res1.repaired_output_03

    items = integrator.drain_to_cohort_items()
    engine = ClosedLoopLearningEngine(runtime_root=tmp_path, proposals_dir=tmp_path / "proposals")
    cohorts = engine.analyze_failure_cohorts(items, min_occurrences=2)

    alias_cohorts = [c for c in cohorts if "Mark Vance->Marcus Vance" in c["cohort_key"]]
    assert len(alias_cohorts) == 1
    cohort = alias_cohorts[0]

    # Synthesize proposal without needing manual target_override
    proposal = engine.generate_proposal_for_cohort(cohort)
    assert proposal["proposal_type"] == "alias_addition"
    ext = proposal["extensions"]["com.partner_advisory.nee.learning"]
    assert ext["from_id"] == "Mark Vance"
    assert ext["to_id"] == "Marcus Vance"
    assert ext["output_01_immutable_verified"] is True
    validate_artifact(proposal)

    sim_res = engine.simulate_proposal(proposal)
    assert sim_res.success is True
    assert sim_res.status == "SIMULATED_PASS"

    aliases_path = tmp_path / "learned_aliases.json"
    write_json(aliases_path, {"schema_version": "1.0.0", "aliases": {}})
    approval = create_signed_calibration_approval_receipt(
        proposal=proposal,
        approved_by="human_operator_lead",
        rationale="Approved recurring owner alias Mark Vance -> Marcus Vance",
    )
    apply_receipt = engine.apply_approved_proposal(proposal, approval, aliases_path=aliases_path)
    assert apply_receipt["status"] == "APPLIED"
    saved_aliases = read_json(aliases_path)
    assert saved_aliases["aliases"]["Mark Vance"] == "Marcus Vance"


def test_bitemporal_supersession_cadence_calibrates_copilot_decay_tau_e2e(tmp_path: Path) -> None:
    """Verifies TemporalDriftSupersessionAgent supersession intervals modulate copilot.context.decay_tau_seconds."""
    drift_agent = TemporalDriftSupersessionAgent(min_supersession_days=1)
    assertions = [
        {
            "assertion_id": "a_old_1",
            "subject_id": "workstream_alpha",
            "predicate": "HAS_STATUS",
            "object_id": "amber",
            "valid_from": "2026-03-01",
        },
        {
            "assertion_id": "a_new_1",
            "subject_id": "workstream_alpha",
            "predicate": "HAS_STATUS",
            "object_id": "green",
            "valid_from": "2026-03-16",
        },
        {
            "assertion_id": "a_conf_1",
            "subject_id": "workstream_beta",
            "predicate": "HAS_OWNER",
            "object_id": "owner_x",
            "valid_from": "2026-03-10",
        },
        {
            "assertion_id": "a_conf_2",
            "subject_id": "workstream_beta",
            "predicate": "HAS_OWNER",
            "object_id": "owner_y",
            "valid_from": "2026-03-10",
        },
    ]
    scan_res = drift_agent.scan_temporal_drift(assertions=assertions)
    assert scan_res.supersession_count == 1
    assert scan_res.concurrent_contradiction_count == 1

    emp_step = compute_empirical_decay_step_seconds(scan_res, base_step=150.0)
    assert emp_step > 150.0

    eval_dir = tmp_path / "evals"
    eval_dir.mkdir(parents=True, exist_ok=True)
    for i in (1, 2):
        write_json(
            eval_dir / f"copilot_eval_{i}.json",
            {
                "receipt_id": f"rec-cop-{i}",
                "pipeline": "copilot",
                "evaluated_at": utc_now(),
                "metrics": {"context_recall": 0.45},
            },
        )
    config_path = tmp_path / "parameters.json"
    write_json(
        config_path,
        {
            "schema_version": "1.0.0",
            "updated_at": utc_now(),
            "parameters": {"copilot": {"context": {"decay_tau_seconds": 300.0}}},
        },
    )

    cohorts_default = scan_answer_eval_receipts(eval_dir, min_occurrences=2, config_path=config_path)
    cohorts_empirical = scan_answer_eval_receipts(
        eval_dir, min_occurrences=2, config_path=config_path, temporal_drift_scan=scan_res
    )
    c_def = next(c for c in cohorts_default if c.category == CATEGORY_COPILOT_CONTEXT)
    c_emp = next(c for c in cohorts_empirical if c.category == CATEGORY_COPILOT_CONTEXT)

    assert c_def.proposed_value == 450.0
    assert c_emp.proposed_value == round(300.0 + emp_step, 4)
    assert 30.0 <= c_emp.proposed_value <= 1800.0

    prop = synthesize_answer_learning_proposal(c_emp, proposals_dir=tmp_path / "proposals")
    validate_artifact(prop)

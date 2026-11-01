"""tests/end_to_end/test_evals_surface_agent_e2e.py

End-to-end verification for the Evals Agent (`evals_surface_assurance`)
and its construct-validity remediation across the repository's evaluation surfaces:
1. Canonical Evals Agent specification (`automations/prompts/evals_surface_agent.md`) & SSOT pointer registration.
2. Surface 4 (`evals/copilot_quality_gate.py`): unclamped per-case relevance bootstrap CI,
   HOLDOUT_ASSERTIONS grounding binding, and end-to-end `run_copilot_quality_gate` execution.
3. Surface 6 (`evals/run_provider_fault_injection.py` & `evals/evaluate_metal_advisory_extraction.py`):
   bounded ThreadPoolExecutor timeout cancellation + fallback extraction and 4-gate Metal advisory evaluation.
4. Surface 1 & 2 (`PipelineCEvaluator`, `DeliverableStrategicEvaluator`, `compute_groundedness`):
   end-to-end scorecard qualification and negative-control rejection.
"""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.evals.answer_eval_core.metrics import compute_groundedness
from dual_pipeline.evals.pipelines.pipeline_c_evaluator import PipelineCEvaluator
from dual_pipeline.evals.strategic.deliverable_evaluator import (
    DeliverableStrategicEvaluator,
)
from evals.copilot_quality_gate import run_copilot_quality_gate
from evals.evaluate_metal_advisory_extraction import MetalAdvisoryExtractionEvaluator
from evals.run_provider_fault_injection import run_fault_injection
from tools.lint_prompt_ssot import EXPECTED_CANONICAL_PROMPTS, run_prompt_ssot_lint
from tools.scheduled_task_core.contracts import TWELVE_GOVERNED_TASK_IDS, get_task_definition
from tools.scheduled_task_core.launch_adapters import AntigravityHostLaunchAdapter
from tools.task_restoration_core.ssot_pointers import (
    TASK_SIDECAR_MAP,
    generate_ssot_pointer_prompt,
    is_valid_pointer_prompt,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_evals_agent_canonical_prompt_and_ssot_registration_e2e(tmp_path: Path) -> None:
    """Verify Evals Agent prompt specification, SSOT pointer registration, and launch adapter execution."""
    assert "evals_surface_assurance" in TASK_SIDECAR_MAP
    assert "evals_surface_assurance" in EXPECTED_CANONICAL_PROMPTS
    assert "evals_surface_assurance" in TWELVE_GOVERNED_TASK_IDS

    info = TASK_SIDECAR_MAP["evals_surface_assurance"]
    assert info["sidecar_dir"] == "evals-surface-assurance-agent"
    assert info["canonical_prompt"] == "automations/prompts/evals_surface_agent.md"
    assert info["title"] == "Evals Surface Agent"

    tdef = get_task_definition("evals_surface_assurance")
    assert tdef["prompt_path"] == "automations/prompts/evals_surface_agent.md"
    assert tdef["registration_status"]["status"] == "RECONCILED_ACTIVE"

    pointer = generate_ssot_pointer_prompt("evals_surface_assurance", REPO_ROOT)
    assert is_valid_pointer_prompt(pointer)

    prompt_path = REPO_ROOT / info["canonical_prompt"]
    assert prompt_path.is_file()
    content = prompt_path.read_text(encoding="utf-8")
    assert "Canonical Task: evals_surface_assurance" in content
    assert "INV-EVALS-CONSTRUCT-VALIDITY-GATE" in content
    assert "INV-EVALS-ZERO-TAUTOLOGY-GATE" in content
    assert "INV-EVALS-ROI-ENHANCEMENT-GATE" in content
    assert "UNCONDITIONAL_WAVE_PLAN_MANDATE" in content
    assert "INV-RUNTIME-PROOF-5POINT" in content

    ok, errors = run_prompt_ssot_lint(REPO_ROOT)
    assert ok, f"Prompt SSOT lint failed: {errors}"

    adapter = AntigravityHostLaunchAdapter(repo_root=REPO_ROOT)
    res_clean = adapter.launch_task("evals_surface_assurance", "scheduled", tmp_path, reject_empty_callback=False)
    assert res_clean.terminal_outcome == "COMPLETE"

    (tmp_path / "evals_surface_gap.json").write_text("{}", encoding="utf-8")
    res_gap = adapter.launch_task("evals_surface_assurance", "scheduled", tmp_path, reject_empty_callback=False)
    assert res_gap.terminal_outcome == "HITL_REQUIRED"
    assert res_gap.implementation_plan_path is not None
    assert Path(res_gap.implementation_plan_path).is_file()


def test_end_to_end_seven_surface_evals_construct_validity(tmp_path: Path) -> None:
    """Verify end-to-end execution across Copilot Quality Gate, Fault Injection, Pipeline C, Strategic Deliverable, and Advisory Eval."""
    # 1. Surface 4: Full Copilot Quality Gate over sealed holdout
    holdout_path = REPO_ROOT / "tests" / "evals" / "copilot_golden" / "v1" / "sealed_holdout.jsonl"
    receipt_out = tmp_path / "copilot_quality_receipt.json"
    rc = run_copilot_quality_gate(holdout_path, receipt_out)
    assert rc in (0, 1)
    assert receipt_out.is_file()
    receipt_data = json.loads(receipt_out.read_text(encoding="utf-8"))
    assert receipt_data["trap_invocations"] == 10
    assert receipt_data["ablation_report"]["local_composer_flagged"]["groundedness"] == 1.0
    assert len(receipt_data["ablation_report"]["relevance_bootstrap_95_ci"]) == 2

    # 2. Surface 6: Provider Fault Injection across all 4 fault scenarios (including real ThreadPoolExecutor timeout)
    fault_report = run_fault_injection(
        scenarios=["FAULT-429", "FAULT-503", "FAULT-TRUNC", "FAULT-TIMEOUT"],
        timeout_sim_seconds=0.02,
    )
    assert fault_report.is_overall_compliant is True
    assert fault_report.passed_scenarios == 4
    timeout_scenario = next(r for r in fault_report.results if r.scenario_id == "FAULT-TIMEOUT")
    assert timeout_scenario.details["timeout_error_caught"] is True
    assert timeout_scenario.details["recovered_nodes"] > 0

    # 3. Surface 1: Pipeline C Evaluator full evaluation (including mtime invalidation and FTS/Query API checks)
    pc_scorecard = PipelineCEvaluator().run_evaluation()
    assert pc_scorecard.passed is True
    assert pc_scorecard.vector_cache_invalidation_verified is True
    assert pc_scorecard.law6_quarantine_enforced is True
    assert pc_scorecard.law2_namespace_isolation_enforced is True

    # 4. Surface 2: DeliverableStrategicEvaluator + Answer Groundedness
    p1 = tmp_path / "01_recap.md"
    p2 = tmp_path / "02_notes.md"
    p3 = tmp_path / "03_decisions.json"
    p1.write_text("Clean enterprise architecture discussion.", encoding="utf-8")
    p2.write_text(
        "Meeting Objective: Review $500,000 cost avoidance tradeoff, governance sponsor endorsement, and next steps.\nAttendees: Lead Architect",
        encoding="utf-8",
    )
    p3.write_text(
        json.dumps(
            {
                "schema_version": "5.8",
                "meeting_id": "mtg_substations",
                "observed_at": "10/26/2026",
                "_strategic_analysis": "Platform consolidation and automation efficiency.",
                "evidence_index": {
                    "ev1": {"kind": "quote", "locator": {"value": "L1-L4"}},
                    "ev2": {"kind": "metric", "locator": {"value": "L5-L8"}},
                },
                "graph_mutations": {
                    "entity_upserts": [
                        {"id": "system:scada", "type": "system"},
                        {"id": "workstream:grid", "type": "workstream"},
                        {"id": "person:sponsor", "type": "person"},
                    ],
                    "decision_updates": [{"decision": "Approve SCADA consolidation"}],
                    "risk_updates": [{"risk": "Cutover schedule friction"}],
                    "relationship_signals": [
                        {"from_id": "a", "to_id": "b"},
                        {"from_id": "b", "to_id": "c"},
                        {"from_id": "c", "to_id": "d"},
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    strat_scorecard = DeliverableStrategicEvaluator().evaluate_deliverables(p1, p2, p3)
    assert strat_scorecard.passed is True
    assert strat_scorecard.overall_score >= 95.0
    assert strat_scorecard.dimension_scores["dim_4_dual_audience"].score == 100.0
    assert strat_scorecard.dimension_scores["dim_7_bitemporal"].score == 100.0

    # 5. Surface 6: MetalAdvisoryExtractionEvaluator full 4-gate run
    metal_res = MetalAdvisoryExtractionEvaluator(mock_mode=True).run_all()
    assert metal_res["status"] == "PASS"
    assert all(metal_res["gates"].values())

    # 6. Surface 2: Groundedness rejects unverified citations and accepts verified citations
    assert compute_groundedness({"response_text": "Claim [ast-fake].", "cited_assertion_ids": ["ast-real"]}) == 0.0
    assert compute_groundedness({"response_text": "Claim [ast-real].", "cited_assertion_ids": ["ast-real"]}) == 1.0

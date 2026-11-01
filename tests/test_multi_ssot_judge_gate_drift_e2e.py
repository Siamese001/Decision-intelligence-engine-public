"""End-to-end and adversarial edge-case test suite for Multi-SSOT Prompt, Judge, Contract & Gate Drift Auditor.

Covers:
1. Live repository E2E verification across all 5 drift vectors (V1-V5).
2. Boundary payload edge cases (empty directories, 0-byte prompt files, missing stage/judge files).
3. Malformed input edge cases (corrupted JSON evaluation records, missing tokens/archetypes/prefixes).
4. Invariant bypass attempts (C-25 pending owner drift, floor-breach PASS attempts, synthetic 100/PASS document mode).
5. Scheduled Task Adapter integration (run_ssot_drift_remediation with multi-SSOT findings).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tools.audit_multi_ssot_judge_gate_drift import (
    check_vector_1_prompt_to_judge_parity,
    check_vector_2_judge_to_code_contracts,
    check_vector_3_deterministic_gates_vs_judges,
    check_vector_4_runtime_judge_and_config_ssot,
    run_multi_ssot_drift_audit,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _setup_minimal_fixture(tmp_path: Path) -> Path:
    """Copy canonical stage prompts and judge prompts into an isolated sandbox."""
    stages_src = REPO_ROOT / "config" / "prompts" / "synthesis" / "stages"
    stages_dst = tmp_path / "config" / "prompts" / "synthesis" / "stages"
    stages_dst.mkdir(parents=True, exist_ok=True)
    for fname in ("01_forensic_normalizer.md", "02_executive_recap.md", "03_graph_extractor.md"):
        shutil.copy2(stages_src / fname, stages_dst / fname)

    gov_src = REPO_ROOT / "governance" / "decision_intelligence"
    gov_dst = tmp_path / "governance" / "decision_intelligence"
    gov_dst.mkdir(parents=True, exist_ok=True)
    for fname in (
        "FORENSIC_TRANSCRIPT_JUDGE_PROMPT.md",
        "EXECUTIVE_RECAP_JUDGE_PROMPT.md",
        "GRAPH_EXTRACTION_JUDGE_PROMPT.md",
    ):
        shutil.copy2(gov_src / fname, gov_dst / fname)
    return tmp_path


class TestMultiSSOTJudgeGateDriftE2E:
    """End-to-end verification of the 5-vector Multi-SSOT drift auditor."""

    def test_live_repository_passes_all_five_vectors(self) -> None:
        passed, report = run_multi_ssot_drift_audit(REPO_ROOT)
        assert passed is True, f"Expected 0 violations on live repo, got: {report['findings']}"
        assert report["overall_status"] == "PASS"
        assert report["total_violations"] == 0
        for vec_key in (
            "V1_PROMPT_TO_JUDGE",
            "V2_JUDGE_TO_CONTRACT",
            "V3_GATE_VS_JUDGE",
            "V4_CONFIG_AND_RUNTIME_JUDGE",
            "V5_GOVERNANCE_HARMONIZATION",
        ):
            assert report["vectors"][vec_key]["status"] == "PASS"

    def test_vector_2_and_vector_3_contract_and_gate_parity(self) -> None:
        v2_findings = check_vector_2_judge_to_code_contracts(REPO_ROOT)
        v3_findings = check_vector_3_deterministic_gates_vs_judges(REPO_ROOT)
        assert v2_findings == []
        assert v3_findings == []


class TestAdversarialDriftInjectionAndEdgeCases:
    """Adversarial and edge-case tests covering boundary payloads, malformed inputs, and bypass attempts."""

    def test_boundary_empty_directory_reports_missing_prompts(self, tmp_path: Path) -> None:
        """Category 1 (Boundary payload): Empty directory with missing prompt files fails closed."""
        findings = check_vector_1_prompt_to_judge_parity(tmp_path)
        assert len(findings) == 6
        codes = {f["code"] for f in findings}
        assert "MISSING_STAGE_01" in codes
        assert "MISSING_JUDGE_02" in codes

    def test_boundary_zero_byte_prompt_file_rejected(self, tmp_path: Path) -> None:
        """Category 1 (Boundary payload): 0-byte prompt file triggers immediate missing/empty finding."""
        sandbox = _setup_minimal_fixture(tmp_path)
        (sandbox / "config" / "prompts" / "synthesis" / "stages" / "02_executive_recap.md").write_text(
            "", encoding="utf-8"
        )
        findings = check_vector_1_prompt_to_judge_parity(sandbox)
        assert any(f["code"] == "MISSING_STAGE_02" for f in findings)

    def test_invariant_bypass_c25_pending_owner_drift_detected(self, tmp_path: Path) -> None:
        """Category 5 (Invariant bypass): Removing C-25 'Unassigned (Pending Owner Assignment)' from Stage 02 triggers drift."""
        sandbox = _setup_minimal_fixture(tmp_path)
        s2_path = sandbox / "config" / "prompts" / "synthesis" / "stages" / "02_executive_recap.md"
        s2_text = s2_path.read_text(encoding="utf-8").replace("Unassigned (Pending Owner Assignment)", "RemovedToken")
        s2_path.write_text(s2_text, encoding="utf-8")

        findings = check_vector_1_prompt_to_judge_parity(sandbox)
        assert any(f["code"] == "O2_C25_PENDING_OWNER_DRIFT" for f in findings)

    def test_malformed_archetype_or_prefix_drift_detected(self, tmp_path: Path) -> None:
        """Category 2 (Malformed input): Stripping a canonical archetype or governed entity prefix triggers V1 failure."""
        sandbox = _setup_minimal_fixture(tmp_path)
        j3_path = sandbox / "governance" / "decision_intelligence" / "GRAPH_EXTRACTION_JUDGE_PROMPT.md"
        j3_text = j3_path.read_text(encoding="utf-8").replace("LESSONS_LEARNED", "OBSOLETE_ARCHETYPE").replace("policy:", "obsolete:")
        j3_path.write_text(j3_text, encoding="utf-8")

        findings = check_vector_1_prompt_to_judge_parity(sandbox)
        codes = {f["code"] for f in findings}
        assert "O3_ARCHETYPE_ENUM_DRIFT" in codes
        assert "O3_ENTITY_PREFIX_DRIFT" in codes

    def test_judge_02_anti_boilerplate_drift_detected(self, tmp_path: Path) -> None:
        """Category 5 (Invariant bypass): Removing word-salad patterns from Judge 02 prompt triggers V3 gate-vs-judge drift."""
        sandbox = _setup_minimal_fixture(tmp_path)
        j2_path = sandbox / "governance" / "decision_intelligence" / "EXECUTIVE_RECAP_JUDGE_PROMPT.md"
        j2_text = j2_path.read_text(encoding="utf-8").replace("aspirational operating concept", "redacted")
        j2_path.write_text(j2_text, encoding="utf-8")

        findings = check_vector_3_deterministic_gates_vs_judges(sandbox)
        assert any(f["code"] == "O2_ANTI_BOILERPLATE_PROMPT_DRIFT" for f in findings)

    def test_runtime_judge_f2_floor_breach_in_sandbox_detected(self, tmp_path: Path) -> None:
        """Category 7 (Exact production regression): Unremediated F2 floor breach in output evaluation JSON is caught."""
        sandbox = _setup_minimal_fixture(tmp_path)
        out_run = sandbox / "output" / "project_test" / "10-15-2026"
        out_run.mkdir(parents=True, exist_ok=True)
        # Write a defective evaluation record that even after revalidation has an unfixable floor breach
        # (e.g. rationale explicitly says "floor failure" while overall_status is PASS and o3 dims look high)
        bad_eval = {
            "mode": "meeting",
            "status": "PASS",
            "output_1_evaluation": {"composite_score": 95.0, "verdict": "PASS"},
            "output_2_evaluation": {"composite_score": 95.0, "verdict": "PASS"},
            "output_3_evaluation": {
                "meeting_archetype": "ARCHITECTURE_DEEP_DIVE",
                "dimension_1_strategic_edge": 15.0,
                "dimension_2_knowledge_layer_density": 15.0,
                "dimension_3_semantic_atomicity": 20.0,
                "dimension_4_predicate_specificity": 15.0,
                "dimension_5_exclusion_of_action_items": 15.0,
                "dimension_6_evidence_grounding_and_schema": 15.0,
                "composite_score": 95.0,
                "verdict": "PASS",
                "forensic_rationale": "Detected floor failure in sub-criterion while passing overall.",
            },
        }
        (out_run / "llm_judge_evaluation.json").write_text(json.dumps(bad_eval), encoding="utf-8")

        findings = check_vector_4_runtime_judge_and_config_ssot(sandbox)
        assert any("JUDGE_DEFECT_F2_FLOOR_BREACH_COUNT" in f["code"] for f in findings)


    def test_scheduled_task_adapter_detects_prompt_judge_drift(self, tmp_path: Path) -> None:
        """Verify run_ssot_drift_remediation in scheduled mode detects prompt-to-judge drift and emits HITL_REQUIRED."""
        from tools.scheduled_task_core.adapters_core.audits import run_ssot_drift_remediation
        from tools.scheduled_task_core.authority import AuthorityContext
        from tools.scheduled_task_core.contracts import ExecutionMode

        sandbox = _setup_minimal_fixture(tmp_path)
        s2_path = sandbox / "config" / "prompts" / "synthesis" / "stages" / "02_executive_recap.md"
        s2_path.write_text(
            s2_path.read_text(encoding="utf-8").replace("Action & Governance Ledger", "Obsolete Ledger"),
            encoding="utf-8",
        )
        run_dir = tmp_path / "run_out"
        run_dir.mkdir(parents=True, exist_ok=True)
        ctx = AuthorityContext(
            task_id="ssot_drift_remediation",
            run_id="run-test-001",
            execution_mode=ExecutionMode.SCHEDULED,
            trigger_origin="cron_scheduler",
            actor="scheduler",
        )
        res = run_ssot_drift_remediation(ctx, sandbox, run_dir)
        assert res.terminal_outcome == "HITL_REQUIRED"
        assert any(f.get("issue") == "O2_ACTION_LEDGER_DRIFT" for f in res.findings)
        assert res.implementation_plan_path is not None and res.implementation_plan_path.is_file()

    def test_worktree_bypass_toggle_drift_detected_in_vector_5(self, tmp_path: Path) -> None:
        """Category 5 (Invariant bypass): Injecting BooleanOptionalAction or NEE_ENFORCE_WORKTREE into sandbox triggers V5 drift."""
        from tools.audit_multi_ssot_judge_gate_drift import check_vector_5_governance_and_prompt_ssot

        sandbox = _setup_minimal_fixture(tmp_path)
        cli_dir = sandbox / "dual_pipeline" / "cli"
        cli_dir.mkdir(parents=True, exist_ok=True)
        (cli_dir / "parser.py").write_text(
            'add_argument("--enforce-worktree", action=argparse.BooleanOptionalAction, default=True)\n',
            encoding="utf-8",
        )
        lc_dir = sandbox / "dual_pipeline" / "canonical_pipeline"
        lc_dir.mkdir(parents=True, exist_ok=True)
        (lc_dir / "lifecycle.py").write_text(
            'if os.getenv("NEE_ENFORCE_WORKTREE") == "0": enforce_wt = False\n',
            encoding="utf-8",
        )

        findings = check_vector_5_governance_and_prompt_ssot(sandbox)
        codes = {f["code"] for f in findings}
        assert "CLI_WORKTREE_BYPASS_TOGGLE_DRIFT" in codes
        assert "RUNTIME_WORKTREE_BYPASS_DRIFT" in codes


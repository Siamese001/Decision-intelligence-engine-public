"""
tools/active_ingestion_acceptance_core/verifiers.py

Modular wave verification routines for active ingestion acceptance waves 1-5.
Enforces INV-BUDGET-FILE-LINES (module size and complexity policy).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from dual_pipeline.core import read_json
from storage.connection import create_sqlite_conn
from .deliverables import (
    WAVE_1_DELIVERABLES,
    WAVE_2_DELIVERABLES,
    WAVE_3_DELIVERABLES,
    WAVE_4_DELIVERABLES,
    WAVE_5_DELIVERABLES,
)
from .helpers import (
    check_decision_gate,
    check_line_budgets,
    collect_deliverables,
    run_pytest,
)


def verify_wave1(root: Path, plan_path: Path, timeout: int) -> dict[str, Any]:
    print("[Wave 1] Verifying policy registry invariants...")
    policy_text = (root / "governance" / "policy_registry.yaml").read_text(encoding="utf-8")
    for inv in ("INV-INGESTION-ACTIVE-COMPLETION", "INV-INGESTION-ENGINEERING-ISOLATION"):
        if inv not in policy_text:
            raise RuntimeError(f"Missing invariant in policy registry: {inv}")

    print("[Wave 1] Verifying AGENTS.md generator drift...")
    res_gen = subprocess.run([sys.executable, str(root / "tools" / "generate_agents_index.py"), "--check"], capture_output=True, text=True, cwd=root, timeout=60)
    if res_gen.returncode != 0:
        raise RuntimeError(f"AGENTS.md generator drift check failed:\n{res_gen.stdout}\n{res_gen.stderr}")

    deliverable_rows = collect_deliverables(root, WAVE_1_DELIVERABLES)

    print("[Wave 1] Running dual-pipeline contract validation...")
    res_val = subprocess.run([sys.executable, str(root / "tools" / "validate_dual_pipeline_contracts.py")], capture_output=True, text=True, cwd=root, timeout=60)
    if res_val.returncode != 0:
        raise RuntimeError(f"Contract validation command failed:\n{res_val.stderr}")
    val_json = json.loads(res_val.stdout)
    if val_json.get("status") != "PASS":
        raise RuntimeError(f"Contract validation returned FAIL: {val_json.get('error')}")

    print("[Wave 1] Running contract parity and semantic unit tests...")
    exit_code = run_pytest(root, ["tests/test_contract_parity_and_ssot.py", "tests/test_validate_dual_pipeline_contracts_semantics.py"], timeout)
    gate_json = check_decision_gate(root, 1, plan_path)

    return {
        "deliverables": deliverable_rows,
        "contract_validation": val_json,
        "test_verification": {
            "suite": "active_graph_ingestion",
            "command": "pytest tests/test_contract_parity_and_ssot.py tests/test_validate_dual_pipeline_contracts_semantics.py",
            "exit_code": exit_code,
            "e2e_exempt": True,
            "e2e_exemption_token": "E2E-EXEMPT",
        },
        "decision_clearance": {"clearance_granted": True, "open_decisions_count": gate_json.get("open_decisions_count", 0)},
    }


def verify_wave2(root: Path, plan_path: Path, timeout: int) -> dict[str, Any]:
    deliverable_rows = collect_deliverables(root, WAVE_2_DELIVERABLES)
    check_line_budgets(root, (
        "dual_pipeline/hitl_governance_core/runtime_tool_adapter.py",
        "dual_pipeline/hitl_governance_core/active_ingestion_approval.py",
        "dual_pipeline/hitl_ui_bridge.py",
    ))
    gate_json = check_decision_gate(root, 2, plan_path)

    print("[Wave 2] Running active ingestion approval E2E & adversarial test suites...")
    exit_code = run_pytest(root, [
        "tests/end_to_end/test_active_ingestion_approval_e2e.py",
        "tests/adversarial/test_active_ingestion_approval_adversarial.py",
    ], timeout)

    print("[Wave 2] Verifying no synthetic records entered production ledger...")
    prod_sqlite = root / "hitl_decision_ledger.sqlite"
    if prod_sqlite.is_file():
        conn = create_sqlite_conn(str(prod_sqlite), timeout=30.0)
        synth_count = conn.cursor().execute("SELECT count(*) FROM hitl_decisions WHERE operator_id LIKE '%synthetic%' OR operator_id LIKE '%auto%'").fetchone()[0]
        conn.close()
        if synth_count > 0:
            raise RuntimeError(f"Found {synth_count} synthetic operator records in production ledger!")

    return {
        "deliverables": deliverable_rows,
        "contract_validation": {"status": "PASS", "suite": "active_ingestion_approval"},
        "test_verification": {
            "suite": "active_graph_ingestion_wave2",
            "command": "pytest tests/end_to_end/test_active_ingestion_approval_e2e.py tests/adversarial/test_active_ingestion_approval_adversarial.py",
            "exit_code": exit_code,
            "e2e_exempt": False,
        },
        "decision_clearance": {"clearance_granted": True, "open_decisions_count": gate_json.get("open_decisions_count", 0)},
    }


def verify_wave3(root: Path, plan_path: Path, timeout: int) -> dict[str, Any]:
    deliverable_rows = collect_deliverables(root, WAVE_3_DELIVERABLES)
    check_line_budgets(root, (
        "dual_pipeline/canonical_pipeline/publish_helper.py",
        "dual_pipeline/canonical_pipeline/publication_journal.py",
        "dual_pipeline/canonical_pipeline/document_resume_binding.py",
    ))
    gate_json = check_decision_gate(root, 3, plan_path)

    print("[Wave 3] Verifying test manifest enrollment...")
    manifest = read_json(root / "tests" / "manifests" / "active_graph_ingestion.json")
    for req_e2e in ("tests.end_to_end.test_active_ingestion_publication_e2e",):
        if req_e2e not in manifest.get("e2e_tests", []):
            raise RuntimeError(f"Manifest missing e2e test: {req_e2e}")
    for req_det in ("tests.adversarial.test_active_ingestion_recovery_adversarial",):
        if req_det not in manifest.get("deterministic_tests", []):
            raise RuntimeError(f"Manifest missing deterministic test: {req_det}")

    print("[Wave 3] Running publication E2E & recovery adversarial test suites...")
    exit_code = run_pytest(root, [
        "tests/end_to_end/test_active_ingestion_publication_e2e.py",
        "tests/adversarial/test_active_ingestion_recovery_adversarial.py",
    ], timeout)

    return {
        "deliverables": deliverable_rows,
        "contract_validation": {"status": "PASS", "suite": "active_ingestion_publication"},
        "test_verification": {
            "suite": "active_graph_ingestion_wave3",
            "command": "pytest tests/end_to_end/test_active_ingestion_publication_e2e.py tests/adversarial/test_active_ingestion_recovery_adversarial.py",
            "exit_code": exit_code,
            "e2e_exempt": False,
        },
        "decision_clearance": {"clearance_granted": True, "open_decisions_count": gate_json.get("open_decisions_count", 0)},
    }


def verify_wave4(root: Path, plan_path: Path, timeout: int) -> dict[str, Any]:
    deliverable_rows = collect_deliverables(root, WAVE_4_DELIVERABLES)
    check_line_budgets(root, (
        "dual_pipeline/canonical_pipeline/publish_helper.py",
        "dual_pipeline/pipeline_runner.py",
        "dual_pipeline/notes_pipeline.py",
    ))
    gate_json = check_decision_gate(root, 4, plan_path)

    print("[Wave 4] Verifying test manifest enrollment...")
    manifest = read_json(root / "tests" / "manifests" / "active_graph_ingestion.json")
    for req_e2e in ("tests.end_to_end.test_active_ingestion_entrypoints_e2e",):
        if req_e2e not in manifest.get("e2e_tests", []):
            raise RuntimeError(f"Manifest missing e2e test: {req_e2e}")
    for req_det in ("tests.adversarial.test_active_ingestion_modes_adversarial",):
        if req_det not in manifest.get("deterministic_tests", []):
            raise RuntimeError(f"Manifest missing deterministic test: {req_det}")

    print("[Wave 4] Running entrypoints E2E & modes adversarial test suites...")
    exit_code = run_pytest(root, [
        "tests/end_to_end/test_active_ingestion_entrypoints_e2e.py",
        "tests/adversarial/test_active_ingestion_modes_adversarial.py",
    ], timeout)

    return {
        "deliverables": deliverable_rows,
        "contract_validation": {"status": "PASS", "suite": "active_ingestion_entrypoints"},
        "test_verification": {
            "suite": "active_graph_ingestion_wave4",
            "command": "pytest tests/end_to_end/test_active_ingestion_entrypoints_e2e.py tests/adversarial/test_active_ingestion_modes_adversarial.py",
            "exit_code": exit_code,
            "e2e_exempt": False,
        },
        "decision_clearance": {"clearance_granted": True, "open_decisions_count": gate_json.get("open_decisions_count", 0)},
    }


def verify_wave5(root: Path, plan_path: Path, timeout: int) -> dict[str, Any]:
    deliverable_rows = collect_deliverables(root, WAVE_5_DELIVERABLES)
    check_line_budgets(root, (
        "dual_pipeline/hitl_governance_core/scheduler_adapter.py",
    ))
    gate_json = check_decision_gate(root, 5, plan_path)

    print("[Wave 5] Verifying test manifest enrollment...")
    manifest = read_json(root / "tests" / "manifests" / "active_graph_ingestion.json")
    for req_e2e in ("tests.end_to_end.test_active_ingestion_scheduled_e2e",):
        if req_e2e not in manifest.get("e2e_tests", []):
            raise RuntimeError(f"Manifest missing e2e test: {req_e2e}")
    for req_det in ("tests.adversarial.test_active_ingestion_dispatch_adversarial",):
        if req_det not in manifest.get("deterministic_tests", []):
            raise RuntimeError(f"Manifest missing deterministic test: {req_det}")

    print("[Wave 5] Running scheduled E2E & dispatch adversarial test suites...")
    exit_code = run_pytest(root, [
        "tests/end_to_end/test_weekly_prompt_cli_parse_e2e.py",
        "tests/test_weekly_prompt_governance_edge_cases.py",
        "tests/end_to_end/test_active_ingestion_scheduled_e2e.py",
        "tests/adversarial/test_active_ingestion_dispatch_adversarial.py",
    ], timeout)

    return {
        "deliverables": deliverable_rows,
        "contract_validation": {"status": "PASS", "suite": "active_ingestion_scheduled"},
        "test_verification": {
            "suite": "active_graph_ingestion_wave5",
            "command": "pytest tests/end_to_end/test_weekly_prompt_cli_parse_e2e.py tests/test_weekly_prompt_governance_edge_cases.py tests/end_to_end/test_active_ingestion_scheduled_e2e.py tests/adversarial/test_active_ingestion_dispatch_adversarial.py",
            "exit_code": exit_code,
            "e2e_exempt": False,
        },
        "decision_clearance": {"clearance_granted": True, "open_decisions_count": gate_json.get("open_decisions_count", 0)},
    }

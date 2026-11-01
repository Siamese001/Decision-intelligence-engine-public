"""Gated receipt profile for the PR Queue dual-mode refactor."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs/plans/docs_pr_queue_dual_mode_refactor_plan.md"
CONTRACT = ROOT / "config/scheduled_tasks/pr_queue_output_contract.json"
RECEIPT_VERSION = "1.0.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wave_inputs(wave: int) -> dict[str, str]:
    shared = [PLAN, CONTRACT, Path(__file__), ROOT / "tools/pr_queue_contract_check.py"]
    scoped = {
        1: [ROOT / "tests/test_pr_queue_refactor_contract.py", ROOT / "tests/manifests/pr_queue_dual_mode_refactor_manifest.json"],
        2: [
            ROOT / "tools/pr_governance_core/snapshot_facts.py", ROOT / "tools/pr_governance_core/queue_snapshot.py",
            ROOT / "tools/pr_governance_core/queue_snapshot_collector.py",
            ROOT / "tools/pr_governance_core/plan_ownership.py", ROOT / "tools/pr_governance_core/decision_api.py",
            ROOT / "tools/pr_governance_core/decision_graph.py", ROOT / "tools/pr_governance_core/queue_reconcile.py",
            ROOT / "tools/pr_governance_core/inspection.py", ROOT / "tools/pr_governance_core/queue_order.py",
            ROOT / "tools/pr_governance_core/outcome_metadata.py", ROOT / "config/pr_priority_rubric.json",
            ROOT / "tests/end_to_end/test_pr_queue_dual_mode_wave2_e2e.py",
            ROOT / "tests/adversarial/test_pr_queue_dual_mode_wave2_adversarial.py",
            ROOT / "tests/manifests/pr_queue_dual_mode_refactor_manifest.json",
        ],
        3: [
            ROOT / "tools/pr_governance_core/queue_observer_table.py", ROOT / "tools/pr_governance_core/render.py",
            ROOT / "tools/pr_governance_core/detail_artifact.py", ROOT / "tools/pr_queue_probe_core/html_builder.py",
            ROOT / "tools/pr_queue_probe_core/__init__.py", ROOT / "tools/pr_queue_render_probe.py",
            ROOT / ".agents/rules/pr-queue-summary.md", ROOT / ".agents/skills/nee-governance/SKILL.md",
            ROOT / "tests/test_pr_queue_render_contract.py", ROOT / "tests/end_to_end/test_pr_queue_dual_mode_wave3_e2e.py",
            ROOT / "tests/adversarial/test_pr_queue_dual_mode_wave3_adversarial.py",
            ROOT / "tests/manifests/pr_queue_dual_mode_refactor_manifest.json",
        ],
        4: [
            ROOT / "tools/pr_queue_observer.py", ROOT / "tools/pr_governance_core/observer.py",
            ROOT / "tools/pr_governance_core/observer_lock.py", ROOT / "tools/pr_governance_core/observer_artifacts.py",
            ROOT / "tools/pr_governance_core/queue_observer_table.py", ROOT / "tools/pr_governance_core/queue_snapshot.py",
            ROOT / "tools/pr_governance_core/queue_snapshot_collector.py",
            ROOT / "config/scheduled_tasks/task_registry.json", ROOT / "tests/end_to_end/test_pr_queue_dual_mode_wave4_e2e.py",
            ROOT / "tests/adversarial/test_pr_queue_dual_mode_wave4_adversarial.py",
            ROOT / "tests/manifests/pr_queue_dual_mode_refactor_manifest.json",
        ],
        5: [
            ROOT / "automations/prompts/pr_queue_observer_agent.md",
            ROOT / "artifacts/exports/scheduled_tasks_hardened/pr_queue_observer_scheduled_task.md",
            ROOT / "tools/pr_queue_observer.py", ROOT / "config/scheduled_tasks/task_registry.json",
            ROOT / "tests/end_to_end/test_pr_queue_dual_mode_wave5_e2e.py",
            ROOT / "tests/adversarial/test_pr_queue_dual_mode_wave5_adversarial.py",
            ROOT / "tests/manifests/pr_queue_dual_mode_refactor_manifest.json",
        ],
        6: [
            ROOT / "docs/plans/pr_queue_dual_mode_refactor/installation/rollback_package.json",
            ROOT / "docs/plans/pr_queue_dual_mode_refactor/installation/host_readback.json",
            ROOT / "docs/plans/pr_queue_dual_mode_refactor/installation/pr_queue_observer_prompt.repository_baseline.md",
            ROOT / "tests/end_to_end/test_pr_queue_dual_mode_wave6_e2e.py",
            ROOT / "tests/adversarial/test_pr_queue_dual_mode_wave6_adversarial.py",
            ROOT / "tests/manifests/pr_queue_dual_mode_refactor_manifest.json",
        ],
    }
    return {str(path.relative_to(ROOT)): _sha256(path) for path in [*shared, *scoped.get(wave, [])]}


def _check_contract() -> dict[str, Any]:
    expected = ["Priority", "Movement", "Worktree", "Next Action", "Reason", "Value", "Progress", "Evidence"]
    try:
        data = json.loads(CONTRACT.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"name": "contract_load", "status": "FAIL", "detail": str(exc)}
    checks = {
        "contract_identity": data.get("contract_id") == "urn:nee:pr-queue-observer:2.0.0",
        "dual_modes": data.get("modes") == ["interactive", "scheduled"],
        "exact_columns": data.get("output", {}).get("columns") == expected,
        "branch_identity_separate": data.get("output", {}).get("branch_identity") == "full_exact_name" and data["output"].get("branch_and_worktree_separate") is False,
        "theme_order": data.get("priority_themes") == ["cost_reduction", "performance_latency", "graph_query_integrity", "live_meetings_client_deliverables", "governance_housekeeping"],
        "truth_policy": data.get("merge_gates", {}).get("unknown_facts_fail_closed") is True and data["merge_gates"].get("priority_implies_eligibility") is False,
        "bounded_runtime": data.get("bounds", {}).get("pytest_timeout_seconds") == 60 and data["bounds"].get("snapshot_retries_max", 3) <= 2,
    }
    failures = [name for name, valid in checks.items() if not valid]
    return {"name": "contract_fields", "status": "FAIL" if failures else "PASS", "checks": checks, "detail": ",".join(failures) or "contract fields match accepted design"}


def _check_sources() -> dict[str, Any]:
    inventory_path = ROOT / "docs/plans/pr_queue_dual_mode_refactor/source_inventory.json"
    try:
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        members = inventory["members"]
        for member in members:
            source = ROOT / member["preserved_path"]
            if source.stat().st_size != member["bytes"] or _sha256(source) != member["sha256"]:
                raise ValueError(f"preserved source mismatch: {member['archive_member']}")
        if len(members) != 4:
            raise ValueError("expected four preserved source documents")
    except Exception as exc:
        return {"name": "source_preservation", "status": "FAIL", "detail": str(exc)}
    return {"name": "source_preservation", "status": "PASS", "detail": "four archived references match recorded byte counts and hashes"}


def _check_known_gaps() -> dict[str, Any]:
    prompt = (ROOT / "automations/prompts/pr_queue_observer_agent.md").read_text(encoding="utf-8")
    runner = (ROOT / "tools/pr_queue_observer.py").read_text(encoding="utf-8")
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    # Wave 1 records baseline findings as evidence; later waves may close them.
    documented = set(contract.get("known_baseline_gaps", []))
    markers = {
        "runner_lacks_mode_and_table_options": "--mode" not in runner and "--format" not in runner,
        "snapshot_defaults_git_state_without_measuring_it": "BranchSnapshot(" in (ROOT / "tools/pr_governance_core/queue_snapshot.py").read_text(encoding="utf-8"),
        "wave_progress_uses_declared_status": "declared_status == \"done\"" in (ROOT / "tools/pr_governance_core/queue_snapshot.py").read_text(encoding="utf-8"),
        "renderer_emits_ledger_and_footer_outside_table": "DIAGNOSTIC & NEXT ACTION LEDGER" in (ROOT / "tools/pr_governance_core/render.py").read_text(encoding="utf-8"),
        "registry_does_not_allow_interactive_mode": "\"allowed_modes\": [\n        \"scheduled\",\n        \"audit_only\"" in (ROOT / "config/scheduled_tasks/task_registry.json").read_text(encoding="utf-8"),
        "antigravity_live_registration_is_unverified": "UNVERIFIED" in (ROOT / "docs/plans/pr_queue_dual_mode_refactor/source_inventory.json").read_text(encoding="utf-8"),
    }
    unmatched = documented - markers.keys()
    return {"name": "baseline_gap_inventory", "status": "FAIL" if unmatched else "PASS", "checks": markers, "detail": "all six known baseline gaps are explicitly represented" if not unmatched else f"missing evidence for: {sorted(unmatched)}"}


def _check_parent(wave: int, parent: Path | None) -> tuple[bool, str, str | None]:
    if wave == 1:
        return (parent is None, "Wave 1 must not have a predecessor" if parent is None else "Wave 1 forbids a parent receipt", None)
    if parent is None or not parent.is_file():
        return False, "a validated predecessor receipt is required", None
    try:
        payload = json.loads(parent.read_text(encoding="utf-8"))
        if payload.get("profile") != "dual-mode-refactor" or payload.get("wave") != wave - 1 or payload.get("terminal_outcome") != "PASS":
            return False, "predecessor profile, wave, or outcome is invalid", None
        return True, "predecessor accepted", _sha256(parent)
    except Exception as exc:
        return False, f"predecessor receipt invalid: {exc}", None


def run_wave(wave: int, timeout: int, receipt: Path, parent: Path | None) -> tuple[int, dict[str, Any]]:
    checks = [_check_contract(), _check_sources()]
    if wave == 1:
        checks.append(_check_known_gaps())
    parent_ok, parent_detail, parent_digest = _check_parent(wave, parent)
    checks.append({"name": "predecessor_gate", "status": "PASS" if parent_ok else "FAIL", "detail": parent_detail})
    test_targets = {
        1: ["tests/test_pr_queue_refactor_contract.py"],
        2: ["tests/end_to_end/test_pr_queue_dual_mode_wave2_e2e.py", "tests/adversarial/test_pr_queue_dual_mode_wave2_adversarial.py"],
        3: ["tests/end_to_end/test_pr_queue_dual_mode_wave3_e2e.py", "tests/adversarial/test_pr_queue_dual_mode_wave3_adversarial.py", "tests/test_pr_queue_render_contract.py"],
        4: ["tests/end_to_end/test_pr_queue_dual_mode_wave4_e2e.py", "tests/adversarial/test_pr_queue_dual_mode_wave4_adversarial.py"],
        5: ["tests/end_to_end/test_pr_queue_dual_mode_wave5_e2e.py", "tests/adversarial/test_pr_queue_dual_mode_wave5_adversarial.py"],
        6: ["tests/end_to_end/test_pr_queue_dual_mode_wave6_e2e.py", "tests/adversarial/test_pr_queue_dual_mode_wave6_adversarial.py"],
    }
    command_targets = test_targets.get(wave)
    if command_targets:
        command = [sys.executable, "-B", "tools/run_tests_with_progress.py", "--timeout", str(timeout), *command_targets]
        try:
            process = subprocess.run(command, cwd=ROOT, check=False, timeout=timeout + 10)
            checks.append({"name": f"wave{wave}_acceptance_tests", "status": "PASS" if process.returncode == 0 else "FAIL", "detail": f"declared wave test scope exit={process.returncode}"})
        except subprocess.TimeoutExpired:
            checks.append({"name": f"wave{wave}_acceptance_tests", "status": "FAIL", "detail": "focused tests exceeded the configured timeout"})
    all_passed = all(check["status"] == "PASS" for check in checks)
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5, check=True).stdout.strip()
    except Exception:
        sha = "UNKNOWN"
    receipt_data = {
        "receipt_version": RECEIPT_VERSION,
        "plan_id": "docs_pr_queue_dual_mode_refactor_plan",
        "plan_sha256": _sha256(PLAN),
        "contract_sha256": _sha256(CONTRACT),
        "profile": "dual-mode-refactor",
        "wave": wave,
        "parent_receipt_sha256": parent_digest,
        "inputs": _wave_inputs(wave),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_sha": sha,
        "runtime_root": str(ROOT),
        "timeout_seconds": timeout,
        "terminal_outcome": "PASS" if all_passed else "FAIL",
        "checks": checks,
    }
    receipt.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{receipt.name}.", dir=receipt.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(receipt_data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary_name, receipt)
        os.unlink(temporary_name)
    except FileExistsError:
        return 2, {**receipt_data, "terminal_outcome": "FAIL", "error": "receipt path already exists; refusing overwrite"}
    return (0 if all_passed else 1), receipt_data

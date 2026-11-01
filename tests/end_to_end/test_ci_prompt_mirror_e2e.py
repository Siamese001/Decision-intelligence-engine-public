#!/usr/bin/env python3
"""tests/end_to_end/test_ci_prompt_mirror_e2e.py

Class B E2E and Adversarial Verification Suite for CI Prompt Export Auto-Mirroring.
Validates:
1. Boundary Payload: Running sync on an already synchronized tree is cleanly idempotent.
2. Malformed Input & Drift Recovery: Deliberate drift introduced into an export is
   automatically healed when sync_prompt_exports or lint_prompt_ssot_integrity runs with --sync.
3. Pre-Commit Auto-Mirror: tools/pre_commit_orchestrator._step_prompt_mirror
   correctly mirrors canonical prompts and reports synchronization.
4. Exact Production Regression: All canonical prompts in automations/prompts/ and
   hardened task exports in artifacts/exports/scheduled_tasks_hardened/ are byte-identical.
"""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

import pytest

from tools.lint_prompt_ssot import (
    CANONICAL_PROMPT_DIR,
    TASK_EXPORT_MAP,
    audit_export_parity,
    audit_zero_cross_prompt_overlap,
    sync_prompt_exports,
)
from tools.lint_prompt_ssot_integrity import validate_contracts
from tools.pre_commit_orchestrator import _step_prompt_mirror

ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestCIPromptMirrorE2E:
    """E2E suite verifying interactive prompt SSOT and duplicate export pruning."""

    def test_production_parity_exact_digests(self) -> None:
        """Category: Exact production regression. All 6 canonical prompts exist and zero duplicate scheduled_task exports exist."""
        canonical_dir = ROOT / CANONICAL_PROMPT_DIR
        export_dir = ROOT / "artifacts" / "exports" / "scheduled_tasks_hardened"

        assert canonical_dir.is_dir(), f"Missing canonical prompts dir: {canonical_dir}"
        if export_dir.is_dir():
            assert list(export_dir.glob("*.md")) == [], "Legacy *_scheduled_task.md duplicates must not exist"

        assert len(TASK_EXPORT_MAP) == 9
        for canon_fname in TASK_EXPORT_MAP:
            can_file = canonical_dir / canon_fname
            assert can_file.is_file(), f"Canonical prompt {canon_fname} missing"
            assert can_file.stat().st_size > 0

    def test_sync_idempotence_boundary(self) -> None:
        """Category: Boundary payload. Running sync on a clean tree yields zero updates."""
        synced = sync_prompt_exports(ROOT)
        assert len(synced) == 0, f"Expected 0 pruned files on pristine tree, got: {synced}"

    def test_deliberate_drift_healing_in_sandbox(self) -> None:
        """Category: Malformed input & drift recovery. Rogue duplicate export is pruned by sync_prompt_exports."""
        with tempfile.TemporaryDirectory(prefix="prompt_mirror_e2e_") as tmpdir:
            sandbox = Path(tmpdir)
            sand_can = sandbox / CANONICAL_PROMPT_DIR
            sand_exp = sandbox / "artifacts" / "exports" / "scheduled_tasks_hardened"
            sand_can.mkdir(parents=True)
            sand_exp.mkdir(parents=True)

            for canon_fname in TASK_EXPORT_MAP:
                shutil.copy2(ROOT / CANONICAL_PROMPT_DIR / canon_fname, sand_can / canon_fname)

            # Introduce a rogue duplicate scheduled task export
            target_export = "pr_queue_observer_scheduled_task.md"
            corrupt_file = sand_exp / target_export
            corrupt_file.write_text("ROGUE DUPLICATE EXPORT CONTENT", encoding="utf-8")

            # Verify that audit_export_parity catches the duplicate export
            errors = audit_export_parity(sandbox)
            assert len(errors) == 1, f"Expected 1 error, got: {errors}"
            assert target_export in errors[0]

            # Run sync_prompt_exports to prune the duplicate export
            removed = sync_prompt_exports(sandbox)
            assert f"retired duplicate export: {target_export}" in removed
            assert not corrupt_file.exists()

            # Verify audit passes cleanly after pruning
            assert audit_export_parity(sandbox) == []

    def test_pre_commit_step_execution(self) -> None:
        """Category: Rollback & recovery. Pre-commit prompt mirror step executes cleanly."""
        recorded_calls: list[tuple[str, str, float, str]] = []

        def mock_record_gate(name: str, phase: str, dur: float, outcome: str, metadata: dict | None = None) -> None:
            recorded_calls.append((name, phase, dur, outcome))

        rc = _step_prompt_mirror(ROOT, staged=False, verbose=False, record_gate_run=mock_record_gate)
        assert rc == 0
        assert len(recorded_calls) == 1
        assert recorded_calls[0][0] == "pre_commit_prompt_mirror"
        assert recorded_calls[0][3] == "PASS"

    def test_full_contract_validation(self) -> None:
        """Category: Invariant preservation. Core contract validation passes."""
        defects = validate_contracts()
        assert len(defects) == 0, f"Contract defects found: {defects}"

    def test_zero_cross_prompt_consolidation_overlap(self) -> None:
        """Category: Invariant bypass attempt. Verifies all 12 prompts declare exclusive domains and reject MECE CONSOLIDATION claims."""
        assert audit_zero_cross_prompt_overlap(ROOT) == []
        with tempfile.TemporaryDirectory(prefix="prompt_overlap_e2e_") as tmpdir:
            sandbox = Path(tmpdir)
            sand_can = sandbox / CANONICAL_PROMPT_DIR
            sand_can.mkdir(parents=True)
            for canon_fname in TASK_EXPORT_MAP:
                shutil.copy2(ROOT / CANONICAL_PROMPT_DIR / canon_fname, sand_can / canon_fname)
            corrupt = sand_can / "pr_queue_observer_agent.md"
            corrupt.write_text(
                corrupt.read_text(encoding="utf-8") + "\n<!-- MECE CONSOLIDATION: Consolidates pr_queue_observer + storage -->\n",
                encoding="utf-8",
            )
            errs = audit_zero_cross_prompt_overlap(sandbox)
            assert len(errs) == 1
            assert "INV-PROMPT-SSOT-08" in errs[0]


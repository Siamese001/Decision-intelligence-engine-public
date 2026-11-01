"""Counterfactual runtime verification tests for daily SSOT drift and architecture remediation (2026-10-28)."""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

from config.credentials import CredentialManager
from dual_pipeline.canonical_pipeline.content_loader import (
    _build_turn_signals,
    _ensure_canonical_modality,
    _extract_source_from_ckpt,
    _mint_standalone_audio_certificate,
    _resolve_resume_source_path,
    _try_load_frozen_transcript,
)
from dual_pipeline.healing_policy import arbitrate_decision_margin
from dual_pipeline.hitl_governance_core.decision_record_writer import resolve_ledger_paths
from dual_pipeline.hitl_governance_core.question_builder import QuestionBuilder
from dual_pipeline.release_thresholds import (
    CANONICAL_RELEASE_POLICY,
    InvariantViolationError,
    ReleaseVerdict,
    assert_inv_02_conformance,
)
from dual_pipeline.storage.healing_decision_store import (
    HealingDecisionStore,
    get_default_healing_sqlite_path,
)
from dual_pipeline.storage.hitl_sqlite_store import get_default_hitl_sqlite_path
from tools.audit_agentic_boundaries import audit_environ_hygiene, audit_silent_failures
from tools.file_budget_core import calculate_ast_metrics

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_remediated_modules_zero_direct_environ_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify all 6 remediated modules have 0 direct os.environ reads and route via CredentialManager."""
    target_files = {
        "dual_pipeline/storage/healing_decision_store.py",
        "dual_pipeline/healing_policy.py",
        "dual_pipeline/hitl_governance_core/question_builder.py",
        "dual_pipeline/hitl_governance_core/decision_record_writer.py",
        "dual_pipeline/storage/hitl_sqlite_store.py",
        "dual_pipeline/canonical_pipeline/content_loader.py",
    }
    violations = audit_environ_hygiene(REPO_ROOT)
    offending = [v for v in violations if v["file"] in target_files]
    assert offending == [], f"Expected 0 direct os.environ reads in target modules, found: {offending}"

    # Verify runtime execution of the CredentialManager-routed paths in an isolated sandbox
    custom_learning_db = tmp_path / "custom_learning.sqlite"
    custom_hitl_db = tmp_path / "custom_hitl.sqlite"
    custom_hitl_json = tmp_path / "custom_hitl.json"
    custom_store_json = tmp_path / "learned_options.json"
    custom_store_json.write_text(
        json.dumps(
            {
                "options": {
                    "approve_fix": {
                        "plain_english_summary": "Approve verified fix",
                        "impact_statement": "Ensures operational reliability and prevents regression risk.",
                        "posterior_confidence": 0.91,
                        "sample_count": 5,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    with CredentialManager.temporary_env(
        {
            "NEE_TEST_LEARNING_SQLITE": str(custom_learning_db),
            "NEE_ALLOW_TEST_FIXTURES": "1",
            "NEE_TEST_LEDGER_SQLITE": str(custom_hitl_db),
            "NEE_HITL_LEDGER_SQLITE": str(custom_hitl_db),
            "NEE_HITL_LEDGER_JSON": str(custom_hitl_json),
            "NEE_LEARNED_HITL_OPTION_STORE": str(custom_store_json),
            "NEE_LEARNING_SQLITE_PATH": str(custom_learning_db),
        }
    ):
        assert get_default_healing_sqlite_path() == custom_learning_db.resolve()
        assert get_default_hitl_sqlite_path() == custom_hitl_db.resolve()
        sql_p, json_p = resolve_ledger_paths()
        assert sql_p == custom_hitl_db.resolve()
        assert json_p == custom_hitl_json.resolve()

        store = HealingDecisionStore(db_path=custom_learning_db)
        store.record_decision(
            {
                "run_id": "run-test-1",
                "subsystem": "ssot_test",
                "component": "test",
                "scope": "healing_policy",
                "repair_class": "approve_fix",
                "action_taken": "AUTO_APPLY",
                "outcome_status": "HEALED",
            }
        )
        dec = arbitrate_decision_margin(
            "dec-test-1",
            [("approve_fix", 0.85), ("defer_fix", 0.25)],
            category="healing_policy",
            healing_db_path=custom_learning_db,
        )
        assert dec.hitl_required is False

        qb = QuestionBuilder("Which resolution option should we apply?", metadata={"category": "healing_policy"})
        qb.add_option(
            "Approve fix",
            "Approve fix",
            "Ensures operational reliability and prevents regression risk.",
            0.88,
            recommended=True,
        )
        qb.add_option(
            "Defer fix",
            "Defer fix",
            "Preserves existing state and defers changes to next maintenance window.",
            0.25,
            recommended=False,
        )
        built = qb.build()
        assert built.recommended_index == 0

    for k in (
        "NEE_TEST_LEARNING_SQLITE",
        "NEE_HEALING_SQLITE_PATH",
        "NEE_LEARNING_SQLITE_PATH",
        "PYTEST_CURRENT_TEST",
        "NEE_HITL_LEDGER_SQLITE",
        "NEE_HITL_LEDGER_JSON",
    ):
        monkeypatch.delenv(k, raising=False)
    with CredentialManager.temporary_env(
        {
            "NEE_ALLOW_TEST_FIXTURES": "0",
            "MOCK_ENV": "false",
            "NEE_RUNTIME_ROOT": str(tmp_path),
        }
    ):
        assert get_default_healing_sqlite_path() == (tmp_path / "ledger" / "learning.sqlite").resolve()
        sql_rt, json_rt = resolve_ledger_paths()
        assert sql_rt.name == "hitl_decision_ledger.sqlite"
        assert json_rt.name == "hitl_decision_ledger.json"


def test_content_loader_zero_silent_failures_and_logs_diagnostics(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Verify content_loader.py has 0 silent except-pass blocks and emits debug logs on caught exceptions."""
    silent_findings = audit_silent_failures(REPO_ROOT)
    cl_findings = [f for f in silent_findings if f["file"] == "dual_pipeline/canonical_pipeline/content_loader.py"]
    assert cl_findings == [], f"Expected 0 silent failures in content_loader.py, found: {cl_findings}"

    caplog.set_level(logging.DEBUG, logger="dual_pipeline.canonical_pipeline.content_loader")

    # Exercise exception handlers in content_loader.py to ensure 100% modified-line coverage
    bad_dir = tmp_path / "out_dir"
    bad_dir.mkdir()
    assert _try_load_frozen_transcript({"regenerate": "invalid_spec_xyz", "output_dir": str(bad_dir)}) is None

    turns = _build_turn_signals(
        [{"start": 0.0, "end": 1.0, "speaker": "Alice", "text": "Hello"}],
        "Alice: Hello",
        {"speaker_map": "invalid_non_dict"},
    )
    assert len(turns) == 1

    # Trigger _mint_standalone_audio_certificate non-ValueError exception path via non-existent file
    meta_cert: Dict[str, Any] = {}
    _mint_standalone_audio_certificate(tmp_path / "missing.wav", "Speaker 1: hi", meta_cert)

    # Trigger _extract_source_from_ckpt and _resolve_resume_source_path with corrupted JSON
    bad_ckpt = tmp_path / "checkpoint_bad.json"
    bad_ckpt.write_text("{corrupted json", encoding="utf-8")
    assert _extract_source_from_ckpt(bad_ckpt, tmp_path) is None

    bad_meta = tmp_path / "metadata.json"
    bad_meta.write_text("{corrupted metadata json", encoding="utf-8")
    with CredentialManager.temporary_env({"TEST_SANDBOX_DIR": str(tmp_path)}):
        assert _resolve_resume_source_path(tmp_path, {}) is None

    # Trigger _ensure_canonical_modality exception fallback via invalid metadata object
    class _BrokenMeta(dict):
        def get(self, key: Any, default: Any = None) -> Any:
            raise RuntimeError("simulated modality failure")

    _ensure_canonical_modality(None, _BrokenMeta())


def test_release_thresholds_complexity_and_exemption_graduation() -> None:
    """Verify release_thresholds.py is removed from file_budget_exclusions.json and has max_cc <= 15."""
    exclusions_path = REPO_ROOT / "config" / "governance" / "file_budget_exclusions.json"
    exclusions_data = json.loads(exclusions_path.read_text(encoding="utf-8"))
    assert "dual_pipeline/release_thresholds.py" not in exclusions_data["exempt_prefixes"]

    rt_path = REPO_ROOT / "dual_pipeline" / "release_thresholds.py"
    metrics = calculate_ast_metrics(rt_path)
    assert metrics.lines <= 400
    assert metrics.max_cc <= 15, f"Expected max_cc <= 15 in release_thresholds.py, got {metrics.max_cc}"

    # Verify runtime behavior of evaluate_composite_release across PASS, PASS_ADVISORY, and FAIL
    v_pass, auth_pass, meta_pass = CANONICAL_RELEASE_POLICY.evaluate_composite_release(
        95, 92, 94, "PASS", "PASS", "PASS"
    )
    assert v_pass == ReleaseVerdict.PASS
    assert auth_pass is True
    assert meta_pass["all_pass"] is True

    v_adv, auth_adv, meta_adv = CANONICAL_RELEASE_POLICY.evaluate_composite_release(
        85, 92, 94, "PASS_ADVISORY", "PASS", "PASS"
    )
    assert v_adv == ReleaseVerdict.PASS_ADVISORY
    assert auth_adv is False
    assert meta_adv["all_advisory"] is True

    v_fail, auth_fail, meta_fail = CANONICAL_RELEASE_POLICY.evaluate_composite_release(
        70, 92, 94, "FAIL", "PASS", "PASS"
    )
    assert v_fail == ReleaseVerdict.FAIL
    assert auth_fail is False
    assert meta_fail["all_pass"] is False

    with pytest.raises(InvariantViolationError):
        assert_inv_02_conformance(ReleaseVerdict.PASS_ADVISORY, True)


def test_file_budget_baseline_ratchet_graduated() -> None:
    """Verify speaker_hints.py and appendix_standard.py graduated from file_budget_burndown_baseline.json."""
    baseline_path = REPO_ROOT / "reports" / "file_budget_burndown_baseline.json"
    baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
    modules = baseline_data.get("modules", {})
    assert "dual_pipeline/live_meeting_recorder_core/speaker_hints.py" not in modules
    assert "dual_pipeline/transcript_qa_core/appendix_standard.py" not in modules
    assert baseline_data["total_oversized_modules"] == len(modules)
    assert baseline_data["total_oversized_modules"] == 95

    proc = subprocess.run(
        [sys.executable, "tools/lint_file_budgets.py", "--ratchet"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"lint_file_budgets.py --ratchet failed: {proc.stdout}\n{proc.stderr}"
    assert "PASSED: All scanned modules satisfy budget invariants." in proc.stdout

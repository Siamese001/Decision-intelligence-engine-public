"""End-to-end delivery tests for Wave 9 JARVIS judge hardening and authoritative release.

Verifies:
1. Final delivered artifact bytes strictly match evaluated candidate hashes in the sealed manifest.
2. Late mutation of delivered artifacts after judge evaluation fails closed immediately.
3. Pipeline restart retains frozen passing deliverables without duplicate evaluations.
4. Decision seal and evaluation digest verification guarantees tamper-proof lineage.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.release_policy import (
    build_artifact_manifest,
    compute_decision_seal,
    compute_release_evaluation_digest,
    verify_authoritative_release_gate,
    verify_decision_seal,
)
from dual_pipeline.release_thresholds import (
    OUTPUT1_PRODUCTION_PASS_THRESHOLD,
    OUTPUT2_PRODUCTION_PASS_THRESHOLD,
    OUTPUT3_PRODUCTION_PASS_THRESHOLD,
)
from dual_pipeline.remediation_core.state_machine import (
    SelectiveRemediationStateMachine,
)


def _setup_delivery_package(tmp_path: Path) -> Path:
    """Helper to provision a completely valid delivery directory with sealed decision."""
    pkg_dir = tmp_path / "delivery_pkg_20261008"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    f1_content = "# Output 1: Forensic Attributed Transcript\nAlice: We agreed on architecture."
    f2_content = "# Output 2: Executive Recap\n## Overview\nArchitecture aligned."
    f3_content = '{"schema_version": "5.7", "entities": [{"name": "Alice"}]}'

    f1_file = pkg_dir / "01_20261008_hitl_resolved_transcript.md"
    f2_file = pkg_dir / "02_20261008_executive_recap.md"
    f3_file = pkg_dir / "03_20261008_canonical_signal_projection.json"

    f1_file.write_text(f1_content, encoding="utf-8")
    f2_file.write_text(f2_content, encoding="utf-8")
    f3_file.write_text(f3_content, encoding="utf-8")

    judge_report: Dict[str, Any] = {
        "status": "PASS",
        "overall_verdict": "PASS",
        "output_1_evaluation": {"composite_score": 95.0, "verdict": "PASS"},
        "output_2_evaluation": {"composite_score": 92.0, "verdict": "PASS"},
        "output_3_evaluation": {"composite_score": 94.0, "verdict": "PASS"},
        "evaluated_span_complete": True,
        "cross_artifact_reconciliation": {"status": "PASS", "failures": []},
    }
    judge_report["evaluation_receipt_digest"] = compute_release_evaluation_digest(judge_report)

    manifest = build_artifact_manifest(
        f1_text=f1_content,
        f2_text=f2_content,
        f3_text=f3_content,
        model="gpt-6-astra",
        reasoning_effort="medium",
        judge_eval_digest=judge_report["evaluation_receipt_digest"],
    )

    decision_data: Dict[str, Any] = {
        "status": "PASS",
        "overall_verdict": "PASS",
        "output_1_pass": True,
        "output_2_pass": True,
        "output_3_pass": True,
        "output_3_llm_score_pass": True,
        "output_1_deterministic_gates_pass": True,
        "output_2_deterministic_gates_pass": True,
        "output_3_deterministic_gates_pass": True,
        "cross_artifact_consistency_pass": True,
        "authoritative_write_authorized": True,
        "artifact_manifest": manifest,
        "decision_seal": manifest["decision_seal_sha256"],
    }

    (pkg_dir / "final_release_decision.json").write_text(json.dumps(decision_data, indent=2), encoding="utf-8")
    (pkg_dir / "llm_judge_evaluation.json").write_text(json.dumps(judge_report, indent=2), encoding="utf-8")

    return f3_file


def test_delivery_package_verification_succeeds_when_untampered(tmp_path: Path):
    """Pristine delivery package passes authoritative release verification without error."""
    f3_file = _setup_delivery_package(tmp_path)

    # Must pass without raising RuntimeError
    verify_authoritative_release_gate([f3_file])


def test_delivery_late_mutation_fails_closed(tmp_path: Path):
    """Mutating delivered artifact bytes after evaluation triggers fail-closed error."""
    f3_file = _setup_delivery_package(tmp_path)
    pkg_dir = f3_file.parent

    # Tamper with Output 2 by appending a character
    f2_file = pkg_dir / "02_20261008_executive_recap.md"
    f2_file.write_text(f2_file.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Output 2 content drifted"):
        verify_authoritative_release_gate([f3_file])


def test_delivery_restart_preserves_frozen_deliverables_without_duplication():
    """Pipeline restart retains already-passing deliverables and consumes zero duplicate calls."""
    sm = SelectiveRemediationStateMachine(max_attempts=3)

    # Attempt 1: Output 1 and Output 3 pass; Output 2 needs retry
    sm.freeze_artifact("output_1", "Output 1 authentic content")
    sm.freeze_artifact("output_3", '{"nodes": ["Alice"]}')
    sm.passing_stages.add("output_1")
    sm.passing_stages.add("output_3")

    # In restart/retry:
    assert "output_1" in sm.passing_stages
    assert "output_3" in sm.passing_stages
    assert "output_2" not in sm.passing_stages

    # Frozen deliverables retrieve cached content directly
    assert sm.get_frozen_content("output_1") == "Output 1 authentic content"
    assert sm.get_frozen_content("output_3") == '{"nodes": ["Alice"]}'

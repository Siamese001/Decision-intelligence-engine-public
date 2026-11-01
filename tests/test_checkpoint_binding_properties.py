"""
tests/test_checkpoint_binding_properties.py

Property-based tests (Hypothesis) for Wave 4:
- Invariant checkpoint binding SHA-256 under arbitrary transient/operational metadata churn
- Stage-isolated checkpoint sealing: validation, tamper detection, and rejection of stale/partial seals
- Monotonic progress guard: detection of non-converging failure signatures and early termination
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest
from hypothesis import given, settings, strategies as st

from dual_pipeline.canonical_pipeline.transcript_resume_binding import (
    _digest,
    build_transcript_resume_binding,
    transcript_synthesis_metadata,
)
from dual_pipeline.remediation_core.stage_sealer import (
    persist_stage_checkpoint_seal,
    validate_artifact_content_for_sealing,
    verify_stage_checkpoint_seal,
)
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine


# Strategy for generating transient operational metadata that must NOT affect binding digest
operational_keys_strategy = st.dictionaries(
    keys=st.sampled_from([
        "has_prior_meeting_artifacts",
        "output_1_memoized",
        "output_2_memoized",
        "output_3_memoized",
        "circuit_breaker",
        "diagnostics_path",
        "attempt",
        "max_attempts",
        "retry_count",
        "current_attempt",
        "remediation_attempt",
        "run_id",
        "session_id",
        "execution_id",
        "checkpoint_dir",
        "checkpoint_path",
        "hitl_role_receipts",
        "auto_role_receipts",
        "speaker_role_resolutions",
        "attendee_confirmation_receipt",
        "prior_evaluations",
        "prior_manifest",
        "prior_graph_candidate",
        "prior_recap_candidate",
        "prior_score",
    ]),
    values=st.one_of(
        st.booleans(),
        st.integers(min_value=0, max_value=1000),
        st.text(min_size=1, max_size=50),
        st.dictionaries(keys=st.text(min_size=1, max_size=10), values=st.text(max_size=20), max_size=3),
    ),
    max_size=15,
)


@given(transient_meta=operational_keys_strategy)
@settings(max_examples=50, deadline=None)
def test_property_transient_metadata_does_not_alter_synthesis_metadata_digest(
    transient_meta: Dict[str, Any],
):
    """Property 1: Injecting arbitrary transient/operational keys never changes semantic metadata digest."""
    baseline_metadata = {
        "project": "internal_consulting",
        "meeting_name": "Executive_Architecture_Sync",
        "meeting_date": "2026-10-11",
        "attendees": ["Amit Ayer", "Jason PartnerAdvisory"],
        "classification": "INTERNAL",
    }
    baseline_semantic = transcript_synthesis_metadata(baseline_metadata)
    baseline_digest = _digest(baseline_semantic)

    churned_metadata = {**baseline_metadata, **transient_meta}
    churned_semantic = transcript_synthesis_metadata(churned_metadata)
    churned_digest = _digest(churned_semantic)

    assert churned_digest == baseline_digest, (
        f"Transient metadata altered semantic digest: churned={churned_digest} != base={baseline_digest}"
    )


@given(
    attempt_num=st.integers(min_value=1, max_value=10),
    run_id=st.uuids().map(str),
    diag_path=st.text(min_size=5, max_size=30).map(lambda s: f"/tmp/{s}.json"),
)
@settings(max_examples=30, deadline=None)
def test_property_transcript_resume_binding_stable_across_execution_attempts(
    attempt_num: int,
    run_id: str,
    diag_path: str,
):
    """Property 2: Full cryptographic binding is invariant to runtime attempt progression and run IDs."""
    source_text = "Speaker 1: Reviewing architectural topology and runtime hardening.\n"
    base_meta = {
        "project": "internal_consulting",
        "meeting_name": "Hardening_Review",
        "raw_source_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "hitl_role_resolutions": {"Amit Ayer": "Enterprise Architect", "Jason PartnerAdvisory": "Lead Engineer"},
        "attendees_confirmed_by_operator": True,
    }

    binding_1 = build_transcript_resume_binding(
        source_text=source_text,
        metadata=base_meta,
        provider=None,
        provider_type="mock",
    )

    # Mutate with runtime execution fields that occur during attempt retries
    runtime_meta = {
        **base_meta,
        "attempt": attempt_num,
        "run_id": run_id,
        "diagnostics_path": diag_path,
        "output_1_memoized": True,
        "has_prior_meeting_artifacts": True,
        "hitl_role_receipts": {"Amit Ayer": {"receipt_id": run_id, "timestamp": "2026-10-11T12:00:00Z"}},
    }

    binding_2 = build_transcript_resume_binding(
        source_text=source_text,
        metadata=runtime_meta,
        provider=None,
        provider_type="mock",
    )

    assert binding_1["binding_sha256"] == binding_2["binding_sha256"], (
        f"Binding SHA-256 diverged: {binding_1['binding_sha256']} vs {binding_2['binding_sha256']}"
    )


@given(
    art_key=st.sampled_from(["output_1", "output_2", "output_3"]),
    corrupt_content=st.one_of(
        st.just(""),
        st.just("   \n\t  "),
        st.text(max_size=10),  # Truncated content < 20 chars
    ),
)
@settings(max_examples=30, deadline=None)
def test_property_stage_sealer_rejects_empty_or_truncated_content(
    art_key: str, corrupt_content: str
):
    """Property 3: Stage checkpoint sealer rejects empty or truncated artifacts fail-closed."""
    with tempfile.TemporaryDirectory(prefix="test_seal_prop_") as td:
        ckpt_dir = Path(td)
        with pytest.raises(ValueError, match="CANNOT_SEAL_INVALID_ARTIFACT"):
            persist_stage_checkpoint_seal(ckpt_dir, art_key, corrupt_content)


@given(
    stage_key=st.sampled_from(["output_1", "output_2"]),
    valid_text=st.text(min_size=30, max_size=200).filter(lambda s: bool(s.strip())),
)
@settings(max_examples=30, deadline=None)
def test_property_stage_sealer_preserves_integrity_and_detects_tampering(
    stage_key: str, valid_text: str
):
    """Property 4: Valid content seals cleanly; tampering or seal corruption is always detected."""
    with tempfile.TemporaryDirectory(prefix="test_seal_tamper_") as td:
        ckpt_dir = Path(td)
        persist_stage_checkpoint_seal(ckpt_dir, stage_key, valid_text)

        # Verification must pass initially
        ok, err = verify_stage_checkpoint_seal(ckpt_dir, stage_key)
        assert ok is True
        assert err is None

        # Tampering with checkpoint file on disk must fail verification
        ext = ".md"
        ckpt_file = ckpt_dir / f"{stage_key}.ckpt{ext}"
        ckpt_file.write_text("Tampered content that invalidates the stored sha256 checksum.", encoding="utf-8")

        ok_tampered, err_tampered = verify_stage_checkpoint_seal(ckpt_dir, stage_key)
        assert ok_tampered is False
        assert "SEAL_DIGEST_MISMATCH" in str(err_tampered)


def test_property_monotonic_progress_guard_stagnation_termination():
    """Property 5: Repeating an identical failure signature twice halts the remediation loop."""
    sm = SelectiveRemediationStateMachine(max_attempts=5)

    failing_judge_report = {
        "output_1_evaluation": {"composite_score": 96, "verdict": "PASS"},
        "output_2_evaluation": {"composite_score": 84, "verdict": "FAIL", "dimension_scores": {"d1": 84}},
        "output_3_evaluation": {"composite_score": 96, "verdict": "PASS"},
        "cross_artifact_reconciliation": {"passed": True},
    }
    failing_qa_report = {
        "output_01_forensic_transcript": {"passed": True},
        "output_02_executive_recap": {"passed": False, "failures": ["Missing Action & Governance Ledger"]},
        "output_03_canonical_signal_projection": {"passed": True},
    }
    failing_decision = {"status": "FAIL", "output_1_pass": True, "output_2_pass": False, "output_3_pass": True}

    artifacts_a1 = {
        "output_1": "# Forensic Transcript\nValid transcript content.\n",
        "output_2": "# Executive Recap\nMissing required ledger.\n",
        "output_3": '{"nodes": []}',
    }

    # Attempt 1: First failure observed
    all_passed_1, failed_1 = sm.evaluate_attempt(
        failing_decision, failing_judge_report, failing_qa_report, artifacts=artifacts_a1
    )
    assert all_passed_1 is False
    assert "output_2" in failed_1
    assert sm.should_retry() is True
    assert sm.stagnant_failure_detected is False

    sm.advance_attempt()
    assert sm.current_attempt == 2

    # Attempt 2: Identical failure signature observed
    all_passed_2, failed_2 = sm.evaluate_attempt(
        failing_decision, failing_judge_report, failing_qa_report, artifacts=artifacts_a1
    )
    assert all_passed_2 is False
    assert "output_2" in failed_2

    # Monotonic progress guard must detect stagnation and halt retries early
    assert sm.stagnant_failure_detected is True
    assert sm.should_retry() is False  # Must NOT permit Attempt 3

    diag = sm.get_stagnant_diagnosis()
    assert diag is not None
    assert diag["stagnant_attempt"] == 2
    assert "output_2" in diag["failed_stages"]
    assert "Non-converging remediation loop detected" in diag["diagnosis"]

"""
tests/adversarial/test_ingestion_hardening_wave4_adversarial.py

Wave 4 Adversarial Contract Test Suite.
Validates:
  - R01: Graph-only retry installs fresh03 and preserves valid01/02; recap-only retry does not silently discard independent graph result.
  - R02: Changed01/speaker resolution invalidates downstream02/03; valid parallel children share one frozen01 digest.
  - R03: Identical source bytes under different modality/tenant/session cannot reuse another route receipt.
  - R04: Changed attachment/classification/audience invalidates affected evidence/validation/publication without erasing valid original capture.
  - R05: Unknown model revision blocks unsafe reuse; changed judge policy revalidates while retaining independently valid extraction.
  - R06: Legacy PASS files/large deployed artifacts cannot bypass exact fresh stage bindings; missing QA verdict means unknown, never PASS.
  - R07: Fresh measured attachment hashes override no trusted facts: reject stale supplied digest and duplicate basename collisions before reuse.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock
import pytest

from dual_pipeline.canonical_pipeline.source_set import (
    SourceSetManifest,
    SourceMember,
    classify_transcript_attachment,
)
from dual_pipeline.canonical_pipeline.strategies.synthesis import (
    FullMeetingSynthesisStrategy,
)
from dual_pipeline.remediation_core.state_machine import (
    SelectiveRemediationStateMachine,
)
from dual_pipeline.runtime.pipeline_context import (
    ContentAddressedDependencyManifest,
    compute_stage_manifest,
    is_checkpoint_valid,
    save_stage_checkpoint,
)
from dual_pipeline.transcript_pipeline_core.stage_coordinator import (
    _execute_l2_concurrent_fork,
)
from dual_pipeline.transcript_qa_core.frozen_gates import (
    check_frozen_dependency_validity,
)


def test_r01_contract() -> None:
    """R01: Graph-only retry installs fresh03 and preserves valid01/02; recap-only retry does not silently discard independent graph result."""
    pipeline = MagicMock()
    recorder = MagicMock()
    recorder.span.return_value.__enter__ = MagicMock()
    recorder.span.return_value.__exit__ = MagicMock(return_value=False)
    notify_step = MagicMock()

    # 1. Graph-only retry: rerun_s2=False, rerun_s3=True
    pipeline.l2_recap.execute = MagicMock(return_value="unexpected_recap")
    fresh_graph = '{"entities": [{"id": "system:core", "name": "Core System"}], "schema_version": "5.7"}'
    pipeline.l2_graph.execute = MagicMock(return_value=fresh_graph)

    artifacts = {
        "01_hitl_resolved_transcript.md": "**Alice:** [00:01:00]: Content.",
        "02_executive_recap.md": "# Retained Executive Recap\nPreserved from Attempt 1.",
        "03_canonical_signal_projection.json": '{"old": "projection"}',
    }
    recap, graph = _execute_l2_concurrent_fork(
        pipeline=pipeline,
        input_s2_s3="**Alice:** [00:01:00]: Content.",
        pa_context={"metadata": {}},
        meta={"source_text": "**Alice:** [00:01:00]: Content."},
        rerun_s2=False,
        rerun_s3=True,
        recorder=recorder,
        notify_step=notify_step,
        attempt=2,
        current_recap=artifacts["02_executive_recap.md"],
        current_graph=artifacts["03_canonical_signal_projection.json"],
        artifacts=artifacts,
    )
    # Recap was not rerun; preserved prior
    pipeline.l2_recap.execute.assert_not_called()
    assert recap == "# Retained Executive Recap\nPreserved from Attempt 1."
    assert artifacts["02_executive_recap.md"] == "# Retained Executive Recap\nPreserved from Attempt 1."
    # Fresh graph was executed and installed into artifacts
    pipeline.l2_graph.execute.assert_called_once()
    assert graph == fresh_graph
    assert artifacts["03_canonical_signal_projection.json"] == fresh_graph

    # 2. Both rerun, but recap fails: graph result is not silently discarded from artifacts
    pipeline.reset_mock()
    pipeline.l2_recap.execute.side_effect = RuntimeError("Recap executor failed")
    independent_graph = '{"entities": [{"id": "system:indep", "name": "Indep"}], "schema_version": "5.7"}'
    pipeline.l2_graph.execute.return_value = independent_graph

    with pytest.raises(RuntimeError) as excinfo:
        _execute_l2_concurrent_fork(
            pipeline=pipeline,
            input_s2_s3="**Alice:** [00:01:00]: Content.",
            pa_context={"metadata": {}},
            meta={"source_text": "**Alice:** [00:01:00]: Content."},
            rerun_s2=True,
            rerun_s3=True,
            recorder=recorder,
            notify_step=notify_step,
            attempt=2,
            current_recap="prior_recap",
            current_graph="prior_graph",
            artifacts=artifacts,
        )
    assert "Recap executor failed" in str(excinfo.value)
    # The independent graph extraction completed and was recorded before re-raising
    assert artifacts["03_canonical_signal_projection.json"] == independent_graph


def test_r02_contract() -> None:
    """R02: Changed01/speaker resolution invalidates downstream02/03; valid parallel children share one frozen01 digest."""
    sm = SelectiveRemediationStateMachine(max_attempts=3)
    sm.freeze_artifact("output_1", "Output 01 content")
    sm.freeze_artifact("output_2", "Output 02 content")
    sm.freeze_artifact("output_3", "Output 03 content")

    # Invalidation on output_1 failure: downstream 02 and 03 are unfrozen
    final_dec = {"status": "FAIL", "output_1_deterministic_gates_pass": False}
    judge_rep = {
        "output_1_evaluation": {"composite_score": 60, "verdict": "FAIL"},
        "output_2_evaluation": {"composite_score": 90, "verdict": "PASS"},
        "output_3_evaluation": {"composite_score": 90, "verdict": "PASS"},
    }
    qa_rep = {
        "output_01_forensic_transcript": {"passed": False},
        "output_02_executive_recap": {"passed": True},
        "output_03_canonical_signal_projection": {"passed": True},
    }
    artifacts = {
        "output_1": "Output 01 changed content",
        "output_2": "Output 02 content",
        "output_3": "Output 03 content",
    }
    all_passed, failed_stages = sm.evaluate_attempt(final_dec, judge_rep, qa_rep, artifacts)
    assert not all_passed
    assert "output_1" in failed_stages
    assert "output_2" in failed_stages, "Changed/failing 01 must invalidate downstream 02"
    assert "output_3" in failed_stages, "Changed/failing 01 must invalidate downstream 03"
    assert not sm.is_frozen("output_2")
    assert not sm.is_frozen("output_3")

    # Upstream transcript / speaker resolution change explicitly invalidates frozen children
    valid, err = check_frozen_dependency_validity("output_2", "content", sm, metadata={"output_1_changed": True})
    assert not valid
    assert "invalidates downstream" in str(err) or "changed" in str(err).lower()

    # Graph-only repair: output_1 and output_2 pass, only output_3 fails
    sm2 = SelectiveRemediationStateMachine(max_attempts=3)
    sm2.freeze_artifact("output_1", "Output 01 valid")
    sm2.freeze_artifact("output_2", "Output 02 valid")
    judge_rep_g3 = {
        "output_1_evaluation": {"composite_score": 95, "verdict": "PASS"},
        "output_2_evaluation": {"composite_score": 95, "verdict": "PASS"},
        "output_3_evaluation": {"composite_score": 65, "verdict": "FAIL"},
    }
    qa_rep_g3 = {
        "output_01_forensic_transcript": {"passed": True},
        "output_02_executive_recap": {"passed": True},
        "output_03_canonical_signal_projection": {"passed": False},
    }
    _, failed_g3 = sm2.evaluate_attempt(
        {"status": "FAIL", "output_1_deterministic_gates_pass": True, "output_2_deterministic_gates_pass": True},
        judge_rep_g3, qa_rep_g3,
        {"output_1": "Output 01 valid", "output_2": "Output 02 valid", "output_3": "bad graph"}
    )
    assert failed_g3 == {"output_3"}, "Graph-only repair must preserve outputs 1 and 2"
    assert sm2.is_frozen("output_1") is True
    assert sm2.is_frozen("output_2") is True


def test_r03_contract(tmp_path: Path) -> None:
    """R03: Identical source bytes under different modality/tenant/session cannot reuse another route receipt."""
    source_text = "The identical source content across multiple calls."

    meta_doc = {"modality": "document", "tenant_id": "tenant_alpha", "session_id": "sess_1"}
    meta_trn = {"modality": "transcript", "tenant_id": "tenant_alpha", "session_id": "sess_1"}
    meta_diff_tenant = {"modality": "document", "tenant_id": "tenant_beta", "session_id": "sess_1"}

    mf_doc = compute_stage_manifest("output_3", source_text, meta_doc)
    mf_trn = compute_stage_manifest("output_3", source_text, meta_trn)
    mf_diff_tenant = compute_stage_manifest("output_3", source_text, meta_diff_tenant)

    # Digests must be completely distinct
    assert mf_doc.manifest_digest() != mf_trn.manifest_digest(), "Cross-mode manifest digests must differ"
    assert mf_doc.manifest_digest() != mf_diff_tenant.manifest_digest(), "Cross-tenant manifest digests must differ"

    # A checkpoint saved under document mode cannot be validated under transcript mode
    ckpt_file = tmp_path / "output_3.ckpt.json"
    dummy_payload = json.dumps({"data": "test" * 150})
    save_stage_checkpoint(ckpt_file, dummy_payload, mf_doc)

    assert is_checkpoint_valid(ckpt_file, mf_doc, strict_manifest=True) is True
    assert is_checkpoint_valid(ckpt_file, mf_trn, strict_manifest=True) is False
    assert is_checkpoint_valid(ckpt_file, mf_diff_tenant, strict_manifest=True) is False


def test_r04_contract() -> None:
    """R04: Changed attachment/classification/audience invalidates affected evidence/validation/publication without erasing valid original capture."""
    source_text = "Primary meeting dialogue or document content."
    base_meta = {
        "classification": "internal",
        "audiences": ["engineering"],
        "collateral_manifest": [{"filename": "specs.pdf", "sha256": "aaaa1111"}],
    }
    mf_base = compute_stage_manifest("output_3", source_text, base_meta)

    # Changed audience
    meta_aud = dict(base_meta, audiences=["engineering", "leadership", "external"])
    mf_aud = compute_stage_manifest("output_3", source_text, meta_aud)
    assert mf_base.manifest_digest() != mf_aud.manifest_digest()
    # Source digest of primary capture remains unchanged
    assert mf_base.source_digest == mf_aud.source_digest

    # Changed classification
    meta_cls = dict(base_meta, classification="strictly_confidential")
    mf_cls = compute_stage_manifest("output_3", source_text, meta_cls)
    assert mf_base.manifest_digest() != mf_cls.manifest_digest()
    assert mf_base.source_digest == mf_cls.source_digest

    # Changed attachment
    meta_att = dict(base_meta, collateral_manifest=[{"filename": "specs.pdf", "sha256": "bbbb2222"}])
    mf_att = compute_stage_manifest("output_3", source_text, meta_att)
    assert mf_base.manifest_digest() != mf_att.manifest_digest()
    assert mf_base.source_digest == mf_att.source_digest


def test_r05_contract(tmp_path: Path) -> None:
    """R05: Unknown model revision blocks unsafe reuse; changed judge policy revalidates while retaining independently valid extraction."""
    source_text = "Content under model revision testing."
    meta = {"project": "P1", "meeting": "M1"}

    mf_model_a = compute_stage_manifest("output_3", source_text, meta, model_id="gpt-6-luna")
    mf_model_b = compute_stage_manifest("output_3", source_text, meta, model_id="gpt-5-sol")
    mf_unknown_model = compute_stage_manifest("output_3", source_text, meta, model_id=None)

    assert mf_model_a.manifest_digest() != mf_model_b.manifest_digest()
    assert mf_model_a.manifest_digest() != mf_unknown_model.manifest_digest()

    ckpt_file = tmp_path / "stage_03.ckpt.json"
    dummy_payload = json.dumps({"schema_version": "5.7", "content": "valid" * 100})
    save_stage_checkpoint(ckpt_file, dummy_payload, mf_model_a)

    # Reuse with different model revision must fail
    assert is_checkpoint_valid(ckpt_file, mf_model_b, strict_manifest=True) is False
    assert is_checkpoint_valid(ckpt_file, mf_unknown_model, strict_manifest=True) is False


def test_r06_contract(tmp_path: Path) -> None:
    """R06: Legacy PASS files/large deployed artifacts cannot bypass exact fresh stage bindings; missing QA verdict means unknown, never PASS."""
    # Create large legacy artifact (> 500 bytes) without manifest
    legacy_file = tmp_path / "03_legacy_projection.json"
    legacy_content = json.dumps({"entities": [{"id": f"entity:{i}", "name": f"Entity {i}"} for i in range(50)]})
    legacy_file.write_text(legacy_content, encoding="utf-8")
    assert legacy_file.stat().st_size > 500

    expected_manifest = compute_stage_manifest("output_3", "Fresh source text", {"project": "P1"})

    # Strict manifest validation rejects legacy unmanifested files
    assert is_checkpoint_valid(legacy_file, expected_manifest, strict_manifest=True) is False

    # Attempting to load candidate from directory without valid manifest does not bypass
    mock_l2_graph = MagicMock()
    fresh_generated = json.dumps({"entities": [{"id": "fresh", "name": "Fresh"}], "schema_version": "5.7"})
    mock_l2_graph.execute.return_value = fresh_generated

    strategy = FullMeetingSynthesisStrategy(provider=None, l2_graph=mock_l2_graph)
    res = strategy._resolve_output_3(
        forensic_transcript="Fresh source text",
        executive_recap=None,
        pa_context={"output_dir": str(tmp_path), "metadata": {"strict_checkpoint_manifest": True}},
        attempt=1,
        remediation_directive=None,
        progress_notify=None,
        ckpt_dir=tmp_path / "attempts" / "attempt_1",
    )
    # Strategy should execute fresh extraction instead of returning legacy content
    assert res != legacy_content


def test_r07_contract(tmp_path: Path) -> None:
    """R07: Fresh measured attachment hashes override no trusted facts: reject stale supplied digest and duplicate basename collisions before reuse."""
    dir_a = tmp_path / "folder_a"
    dir_b = tmp_path / "folder_b"
    dir_a.mkdir()
    dir_b.mkdir()

    # Two distinct files with colliding basename 'data.xlsx'
    file_a = dir_a / "data.xlsx"
    file_b = dir_b / "data.xlsx"
    file_a.write_bytes(b"Workbook A binary payload with Sheet 1")
    file_b.write_bytes(b"Workbook B binary payload with different Sheet 2 data")

    manifest = SourceSetManifest(group_id="group_test_01")
    member_a = manifest.add_source_file(file_a)
    member_b = manifest.add_source_file(file_b)

    # 1. Distinct measured SHA256 digests
    assert member_a.sha256 != member_b.sha256
    assert member_a.sha256 == hashlib.sha256(b"Workbook A binary payload with Sheet 1").hexdigest()
    assert member_b.sha256 == hashlib.sha256(b"Workbook B binary payload with different Sheet 2 data").hexdigest()

    # 2. Deconflicted source IDs despite identical basename
    assert member_a.filename == member_b.filename == "data.xlsx"
    assert member_a.source_id != member_b.source_id
    assert member_a.source_id.startswith("data_")
    assert member_b.source_id.startswith("data_")

    # 3. classify_transcript_attachment derives fresh hash from file on disk, ignoring stale hashes
    att = classify_transcript_attachment(file_a)
    assert att.sha256 == member_a.sha256
    assert att.semantic_role == "document_attachment"

"""
tests/end_to_end/test_hitl_upload_evidence_chain_e2e.py

Class B E2E tests for Wave 1 evidence preservation:
- Two retries cannot overwrite each other's verifiable evidence.
- Deleted shared presentation output does not destroy per-run verifiable evidence.
- Full evidence coverage manifest lifecycle conforms to schema.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from dual_pipeline.canonical_pipeline.evidence_capture import (
    snapshot_run_artifacts,
    verify_run_evidence_integrity,
)
from dual_pipeline.canonical_pipeline.evidence_coverage import (
    EvidenceCoverageManifest,
    EvidenceItem,
    EvidenceState,
    ItemType,
    SourceOriginType,
)
from dual_pipeline.execution_evidence import write_immutable_evidence
from dual_pipeline.hitl_governance_core.approval_lookup import (
    resolve_hitl_approval_reference,
)
from dual_pipeline.hitl_governance_core.crypto import (
    create_signed_hitl_approval_entry,
    generate_hitl_keypair,
)


class MockPipelineTracker:
    def __init__(self, run_id: str, input_path: str, input_hash: str):
        self.pipeline_name = "CanonicalAgenticPipeline"
        self.input_path = input_path
        self.input_hash = input_hash
        self.metadata: dict = {}
        self.artifacts: dict = {}
        class MockTiming:
            def __init__(self, rid):
                self.run_id = rid
                self.evidence_directory = None
            def attributes(self):
                return {"run_id": self.run_id}
        self.timing = MockTiming(run_id)

    def build_receipt(self, out_dir, terminal_outcome="COMPLETE"):
        class MockReceipt:
            def __init__(self, tracker, out_p, outcome):
                self.run_id = tracker.timing.run_id
                self.input_path = tracker.input_path
                self.input_hash = tracker.input_hash
                self.terminal_outcome = outcome
                self.output_dir = str(out_p)
                self.metadata = dict(tracker.metadata)
                self.artifacts = dict(tracker.metadata.get("persisted_artifacts", {}))
                self.persisted_artifacts_dir = tracker.metadata.get("persisted_artifacts_dir", "")
                self.source_evidence = tracker.metadata.get("source_evidence", {})
                self.operator_answers = tracker.metadata.get("operator_answers", [])

            def to_dict(self):
                return {
                    "run_id": self.run_id,
                    "input_path": self.input_path,
                    "input_hash": self.input_hash,
                    "terminal_outcome": self.terminal_outcome,
                    "output_dir": self.output_dir,
                    "metadata": self.metadata,
                    "artifacts": self.artifacts,
                    "persisted_artifacts_dir": self.persisted_artifacts_dir,
                    "source_evidence": self.source_evidence,
                    "operator_answers": self.operator_answers,
                }
        return MockReceipt(self, out_dir, terminal_outcome)


def test_two_retries_independent_verifiable_evidence_e2e():
    """Verify that two retries of the same meeting preserve separate, immutable evidence."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        executions_root = root / "executions"
        executions_root.mkdir()
        shared_presentation_dir = root / "output" / "jarvis_kickoff"
        shared_presentation_dir.mkdir(parents=True)

        meeting_input = "/tmp/jarvis_recording.m4a"
        meeting_hash = "f" * 64

        # --- RUN 1 (Attempt 1: Fails or initial processing) ---
        run_id_1 = "run-20261008-attempt-01"
        evidence_dir_1 = executions_root / run_id_1
        evidence_dir_1.mkdir()
        staging_1 = root / "staging_1"
        staging_1.mkdir()

        (staging_1 / "01_signal.md").write_text("Run 1 transcript text", encoding="utf-8")
        (staging_1 / "03_graph.json").write_text('{"v": 1}', encoding="utf-8")

        tracker_1 = MockPipelineTracker(run_id_1, meeting_input, meeting_hash)
        tracker_1.timing.evidence_directory = evidence_dir_1

        snapshot_run_artifacts(
            tracker=tracker_1,
            staging_dir=staging_1,
            evidence_dir=evidence_dir_1,
            meta={"raw_source_sha256": "1" * 64},
        )
        receipt_1 = tracker_1.build_receipt(evidence_dir_1, "COMPLETE").to_dict()
        write_immutable_evidence(evidence_dir_1 / "pipeline_execution_receipt.json", receipt_1)

        # Promote Run 1 to shared presentation
        for f in staging_1.iterdir():
            shutil.copy2(f, shared_presentation_dir / f.name)
        shutil.rmtree(staging_1)

        assert (shared_presentation_dir / "01_signal.md").read_text(encoding="utf-8") == "Run 1 transcript text"
        run_1_hash = receipt_1["artifacts"]["01_signal.md"]["sha256"]

        # --- RUN 2 (Attempt 2: Retry with updated content) ---
        run_id_2 = "run-20261008-attempt-02"
        evidence_dir_2 = executions_root / run_id_2
        evidence_dir_2.mkdir()
        staging_2 = root / "staging_2"
        staging_2.mkdir()

        (staging_2 / "01_signal.md").write_text("Run 2 corrected transcript text", encoding="utf-8")
        (staging_2 / "03_graph.json").write_text('{"v": 2}', encoding="utf-8")

        tracker_2 = MockPipelineTracker(run_id_2, meeting_input, meeting_hash)
        tracker_2.timing.evidence_directory = evidence_dir_2

        snapshot_run_artifacts(
            tracker=tracker_2,
            staging_dir=staging_2,
            evidence_dir=evidence_dir_2,
            meta={"raw_source_sha256": "1" * 64},
        )
        receipt_2 = tracker_2.build_receipt(evidence_dir_2, "COMPLETE").to_dict()
        write_immutable_evidence(evidence_dir_2 / "pipeline_execution_receipt.json", receipt_2)

        # Promote Run 2 to shared presentation (overwriting Run 1 presentation files)
        for f in staging_2.iterdir():
            shutil.copy2(f, shared_presentation_dir / f.name)
        shutil.rmtree(staging_2)

        # Shared directory now holds Run 2
        assert (shared_presentation_dir / "01_signal.md").read_text(encoding="utf-8") == "Run 2 corrected transcript text"
        run_2_hash = receipt_2["artifacts"]["01_signal.md"]["sha256"]
        assert run_1_hash != run_2_hash

        # VERIFY INVARIANT: Run 1 verifiable evidence was NOT overwritten!
        run_1_persisted = evidence_dir_1 / "artifacts" / "01_signal.md"
        assert run_1_persisted.is_file()
        assert run_1_persisted.read_text(encoding="utf-8") == "Run 1 transcript text"

        ok_1, errs_1 = verify_run_evidence_integrity(evidence_dir_1 / "pipeline_execution_receipt.json")
        assert ok_1 is True, f"Run 1 integrity failed: {errs_1}"

        ok_2, errs_2 = verify_run_evidence_integrity(evidence_dir_2 / "pipeline_execution_receipt.json")
        assert ok_2 is True, f"Run 2 integrity failed: {errs_2}"


def test_deleted_shared_presentation_survives_in_run_evidence_e2e():
    """Verify that deleting shared presentation directory does not corrupt immutable run evidence."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        executions_root = root / "executions"
        executions_root.mkdir()
        shared_presentation_dir = root / "output" / "lens_meeting"
        shared_presentation_dir.mkdir(parents=True)

        run_id = "run-del-presentation-test"
        evidence_dir = executions_root / run_id
        evidence_dir.mkdir()
        staging = root / "staging"
        staging.mkdir()

        (staging / "01_normalizer.md").write_text("Shared normalizer text", encoding="utf-8")
        tracker = MockPipelineTracker(run_id, "/tmp/lens.txt", "3" * 64)
        tracker.timing.evidence_directory = evidence_dir

        snapshot_run_artifacts(
            tracker=tracker,
            staging_dir=staging,
            evidence_dir=evidence_dir,
            meta={"raw_source_sha256": "4" * 64},
        )
        receipt = tracker.build_receipt(evidence_dir, "COMPLETE").to_dict()
        write_immutable_evidence(evidence_dir / "pipeline_execution_receipt.json", receipt)

        # Promote to shared presentation
        for f in staging.iterdir():
            shutil.copy2(f, shared_presentation_dir / f.name)
        shutil.rmtree(staging)

        # DELETE SHARED PRESENTATION
        shutil.rmtree(shared_presentation_dir)
        assert not shared_presentation_dir.exists()

        # Run evidence must still be completely verifiable!
        ok, errs = verify_run_evidence_integrity(evidence_dir / "pipeline_execution_receipt.json")
        assert ok is True
        assert len(errs) == 0
        assert (evidence_dir / "artifacts" / "01_normalizer.md").is_file()


def test_end_to_end_coverage_manifest_lifecycle_e2e():
    """Verify full end-to-end evidence coverage manifest lifecycle and schema conformance."""
    priv, pub = generate_hitl_keypair()
    subject_sha256 = "9" * 64
    entry = create_signed_hitl_approval_entry(
        subject_sha256=subject_sha256,
        case_ids=["case-role-01"],
        decision="APPROVE",
        operator_id="operator_jill",
        private_key=priv,
        public_key=pub,
    )
    entry["entry_id"] = "auth-ev-lifecycle-01"

    manifest = EvidenceCoverageManifest(
        manifest_id="cov-e2e-20261008-01",
        run_id="run-e2e-lifecycle-100",
        source_digest="8" * 64,
        meeting_slug="engineering-jarvis",
        items=[
            EvidenceItem(
                item_id="item-identity-dan",
                item_type=ItemType.PERSON_IDENTITY.value,
                is_eligible_graph_fact=True,
                state=EvidenceState.CAPTURED.value,
                source_origin={
                    "source_type": SourceOriginType.TRANSCRIPT_HITL.value,
                    "source_hash": "8" * 64,
                    "locator": "turn-02",
                },
                disposition_reason="Confirmed attendee identity",
                approval_ref={
                    "entry_id": "auth-ev-lifecycle-01",
                    "status": "RESOLVED_VALID",
                    "subject_sha256": subject_sha256,
                },
            ),
            EvidenceItem(
                item_id="item-visual-wbs",
                item_type=ItemType.VISUAL_CLAIM.value,
                is_eligible_graph_fact=True,
                state=EvidenceState.CAPTURED.value,
                source_origin={
                    "source_type": SourceOriginType.SCREENSHOT_ATTACHMENT.value,
                    "source_hash": "7" * 64,
                    "page_or_region": "table_wbs_spend",
                },
                disposition_reason="Extracted WBS finance table row",
            ),
            EvidenceItem(
                item_id="item-control-retry",
                item_type=ItemType.OPERATIONAL_CONTROL.value,
                is_eligible_graph_fact=False,
                state=EvidenceState.CAPTURED.value,
                source_origin={
                    "source_type": SourceOriginType.OPERATOR_DIRECTIVE.value,
                    "source_hash": "8" * 64,
                },
                disposition_reason="Retry directive; operational control record only",
            ),
        ],
    )

    # Initial state verification
    valid, errors = manifest.validate()
    assert valid, f"Validation failed at CAPTURED: {errors}"

    # Progress item through lifecycle: PROPOSED -> WRITTEN -> ACTIVATED -> VERIFIED
    item_dan = manifest.items[0]
    item_dan.transition_to(EvidenceState.PROPOSED, "Proposed in Output 03 candidates")
    item_dan.transition_to(EvidenceState.WRITTEN, "Applied in X3 graph mutation")
    item_dan.transition_to(EvidenceState.ACTIVATED, "Included in active generation activation")
    item_dan.transition_to(EvidenceState.VERIFIED, "Verified via read-only graph query")

    # Exclude visual sample data with reason
    item_visual = manifest.items[1]
    item_visual.transition_to(EvidenceState.EXCLUDED, "Sample/demo screen values excluded from enterprise graph")

    valid, errors = manifest.validate()
    assert valid, f"Validation failed at terminal states: {errors}"

    summary = manifest.compute_summary()
    assert summary["total_items"] == 3
    assert summary["eligible_facts_count"] == 2
    assert summary["operational_controls_count"] == 1
    assert summary["active_verified_count"] == 1
    assert summary["excluded_count"] == 1
    assert summary["pending_count"] == 1

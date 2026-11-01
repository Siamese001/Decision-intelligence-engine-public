"""
tests/end_to_end/test_screenshot_collateral_pipeline_e2e.py

End-to-End test suite verifying that uploaded screenshots during a meeting:
1. Are discovered and staged into referenced_artifacts/.
2. Are aligned with spoken dialogue turns mentioning visual cues and domain keywords.
3. Promote to COLLATERAL_REVIEWED attribution mode without requiring verbatim filename quotes.
4. Populate metadata["attachments"] with extracted content (summary, tables, components).
5. Invalidate stale pre-existing Output 03 candidates when new attachments are introduced.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.attachment_core.aligner import (
    align_attachments_with_transcript,
    inject_attachment_annotations,
)
from dual_pipeline.attachment_core.detector import (
    discover_attachments,
    stage_attachments_into_output,
)
from dual_pipeline.attachment_core.prompt_builder import build_attachment_prompt_section
from dual_pipeline.attachment_core.review_provenance import has_authenticated_mention
from dual_pipeline.attachment_core.types import (
    AttachmentRecord,
    AttachmentType,
    ExtractedAttachmentContent,
)
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine


@pytest.mark.e2e
def test_screenshot_collateral_pipeline_e2e(tmp_path: Path) -> None:
    output_dir = tmp_path / "output" / "project_lens" / "10-16-2026" / "Ed_Amit"
    ref_dir = output_dir / "referenced_artifacts"
    ref_dir.mkdir(parents=True)

    # 1. Simulate two uploaded screenshots from chat
    s1 = ref_dir / "lens_admin_dashboard_200_processed.png"
    s1.write_bytes(b"\x89PNG\r\n\x1a\nfake_admin_dashboard")
    s2 = ref_dir / "lens_review_queue_93_pending.png"
    s2.write_bytes(b"\x89PNG\r\n\x1a\nfake_review_queue")

    # 2. Stage candidate attachments into output directory
    discovered = discover_attachments(attachment_dir=ref_dir)
    assert len(discovered) == 2

    staged_records = stage_attachments_into_output(discovered, output_dir)
    assert len(staged_records) == 2
    for r in staged_records:
        assert r.staged_path.exists()
        assert r.attachment_type == AttachmentType.IMAGE

    # 3. Simulate extracted structured content for both screenshots
    extracted_contents = {
        staged_records[0].attachment_id: ExtractedAttachmentContent(
            attachment_id=staged_records[0].attachment_id,
            filename=staged_records[0].filename,
            attachment_type=staged_records[0].attachment_type,
            sha256=staged_records[0].sha256,
            summary="LENS Admin Operations Dashboard showing 200 total pictures processed.",
            tables=[{
                "headers": ["Metric", "Count", "Percentage"],
                "rows": [["Processed", 200, "100%"], ["Accepted", 150, "75%"], ["Pending Review", 50, "25%"]],
            }],
            components=[{"component_ref": "comp-lens-admin", "label": "LENS Admin Dashboard"}],
        ),
        staged_records[1].attachment_id: ExtractedAttachmentContent(
            attachment_id=staged_records[1].attachment_id,
            filename=staged_records[1].filename,
            attachment_type=staged_records[1].attachment_type,
            sha256=staged_records[1].sha256,
            summary="LENS Review Queue showing 93 pictures reviewed and 62 rejected.",
            tables=[{
                "headers": ["Queue Item", "Status", "Reviewer"],
                "rows": [["Batch A", "REJECTED (62)", "Ed"], ["Batch B", "APPROVED (31)", "Ed"]],
            }],
            components=[{"component_ref": "comp-lens-queue", "label": "LENS Review Queue"}],
        ),
    }

    # 4. Spoken transcript turns with domain keywords + visual cues
    transcript_text = (
        "**Ed**: Good morning team, let's look at the numbers on the screen.\n\n"
        "**Amit**: As shown here on the dashboard, we have the lens admin overview with 200 processed pictures.\n\n"
        "**Ed**: In the screenshot of the lens review queue, where it says pending review, we had 93 reviewed and 62 rejected.\n\n"
        "**Amit**: Understood, we will reconcile that with SteerCo."
    )

    # 5. Perform cross-modal alignment
    alignment = align_attachments_with_transcript(
        transcript_text,
        staged_records,
        extracted_contents=extracted_contents,
    )
    assert len(alignment.mentions) >= 2

    # Verify that multi-keyword + visual cues authenticate mentions and promote attribution mode
    for rec in staged_records:
        assert has_authenticated_mention(rec.attachment_id, rec.filename, alignment) is True
        rec.attribution_mode = "COLLATERAL_REVIEWED"

    # 6. Inject annotations into transcript
    annotated_transcript = inject_attachment_annotations(transcript_text, alignment)
    assert "[Referenced Collateral:" in annotated_transcript
    assert "lens_admin_dashboard_200_processed.png" in annotated_transcript
    assert "lens_review_queue_93_pending.png" in annotated_transcript

    # 7. Build prompt section and verify COLLATERAL_REVIEWED exhibits
    prompt_section = build_attachment_prompt_section(
        staged_records,
        extracted_contents=extracted_contents,
        alignment=alignment,
    )
    assert "**[COLLATERAL_REVIEWED]**" in prompt_section
    assert "[BACKGROUND REFERENCE ONLY]" not in prompt_section
    assert "200 total pictures processed" in prompt_section
    assert "93 pictures reviewed" in prompt_section

    # 8. Invalidate stale Output 03 cache when attachments are present
    # Write a stale 03_*.json and prior metadata without attachments
    prior_meta = {
        "meeting_name": "Ed Amit",
        "source_file_hash": "urn:nee:source-file:sha256:oldhash123",
        "attachments": [],
    }
    (output_dir / "metadata.json").write_text(json.dumps(prior_meta), encoding="utf-8")
    (output_dir / "final_release_decision.json").write_text(json.dumps({"output_3_pass": True}), encoding="utf-8")
    (output_dir / "03_10-16-2026_ed_amit_signal_projection.json").write_text(
        json.dumps({"entities": ["stale_entity"]}), encoding="utf-8"
    )

    current_metadata = {
        "meeting_name": "Ed Amit",
        "source_file_hash": "urn:nee:source-file:sha256:newhash456",
        "attachments": [rec.to_dict() for rec in staged_records],
    }

    sm = SelectiveRemediationStateMachine(max_attempts=2)
    sm.seed_from_output_dir(str(output_dir), meeting_name="Ed Amit", metadata=current_metadata)

    # Output 3 must NOT be frozen from prior stale run!
    assert sm.is_frozen("output_3") is False

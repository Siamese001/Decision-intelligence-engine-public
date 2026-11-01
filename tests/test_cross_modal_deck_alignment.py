"""Unit tests for cross-modal presentation deck alignment and non-destructive inline annotation injection."""
from __future__ import annotations

from pathlib import Path

from dual_pipeline.attachment_core.aligner import (
    align_attachments_with_transcript,
    inject_attachment_annotations,
)
from dual_pipeline.attachment_core.types import (
    AttachmentRecord,
    AttachmentType,
)


def test_align_retroactive_deck_slides_to_dialogue_turns():
    transcript = (
        "### [00:00:16]\n\n"
        "**David Childers:** I'll show on the next slide. We're shooting for everything to get into UAT.\n\n"
        "### [00:02:00]\n\n"
        "**David Childers:** The red bar is the UAP and the red bar is the remediation part.\n\n"
        "### [00:11:52]\n\n"
        "**Andy Kilinskis:** Last thing I just wanted to show, which is a personal improvement that I like, personal advertising to the Input box.\n\n"
        "### [00:14:32]\n\n"
        "**Brandon Que:** Okay, so let's say I'm going to do the task of two things only, so I'm going to\n\n"
        "### [00:15:28]\n\n"
        "**Brandon Que:** And the end, just making me confirm that, say, yes, it confirms two things at once.\n\n"
        "### [00:20:48]\n\n"
        "**Egle Tamasevice:** Before he calculated the points now. I would like to show how many tickets.\n"
    )

    attachments = [
        AttachmentRecord(
            attachment_id="att_roadmap",
            filename="BAA_Delivery_Status_Jira_Plan_Roadmap.png",
            original_path=Path("BAA_Delivery_Status_Jira_Plan_Roadmap.png"),
            staged_path=Path("BAA_Delivery_Status_Jira_Plan_Roadmap.png"),
            attachment_type=AttachmentType.IMAGE,
            sha256="hash1",
            file_size_bytes=100,
        ),
        AttachmentRecord(
            attachment_id="att_safeguards",
            filename="BAA_Readiness_Report_UAT_Registration_Safeguards.png",
            original_path=Path("BAA_Readiness_Report_UAT_Registration_Safeguards.png"),
            staged_path=Path("BAA_Readiness_Report_UAT_Registration_Safeguards.png"),
            attachment_type=AttachmentType.IMAGE,
            sha256="hash2",
            file_size_bytes=100,
        ),
        AttachmentRecord(
            attachment_id="att_certify",
            filename="BAA_Streamlined_Certify_Flow_UX_Improvement.png",
            original_path=Path("BAA_Streamlined_Certify_Flow_UX_Improvement.png"),
            staged_path=Path("BAA_Streamlined_Certify_Flow_UX_Improvement.png"),
            attachment_type=AttachmentType.IMAGE,
            sha256="hash3",
            file_size_bytes=100,
        ),
        AttachmentRecord(
            attachment_id="att_devin",
            filename="Sprint_49_5_Devin_Integration_Team_Metrics.png",
            original_path=Path("Sprint_49_5_Devin_Integration_Team_Metrics.png"),
            staged_path=Path("Sprint_49_5_Devin_Integration_Team_Metrics.png"),
            attachment_type=AttachmentType.IMAGE,
            sha256="hash4",
            file_size_bytes=100,
        ),
    ]

    alignment = align_attachments_with_transcript(transcript, attachments)
    assert len(alignment.mentions) >= 4

    matched_filenames = {m.filename for m in alignment.mentions}
    assert "BAA_Delivery_Status_Jira_Plan_Roadmap.png" in matched_filenames
    assert "BAA_Readiness_Report_UAT_Registration_Safeguards.png" in matched_filenames
    assert "BAA_Streamlined_Certify_Flow_UX_Improvement.png" in matched_filenames
    assert "Sprint_49_5_Devin_Integration_Team_Metrics.png" in matched_filenames

    annotated = inject_attachment_annotations(transcript, alignment)
    # Verify non-destructive annotation
    assert "### [00:00:16]" in annotated
    assert "### [00:11:52]" in annotated
    assert "### [00:14:32]" in annotated
    assert "### [00:20:48]" in annotated
    assert "> *[Referenced Collateral: `BAA_Delivery_Status_Jira_Plan_Roadmap.png`" in annotated
    assert "> *[Referenced Collateral: `BAA_Readiness_Report_UAT_Registration_Safeguards.png`" in annotated
    assert "> *[Referenced Collateral: `BAA_Streamlined_Certify_Flow_UX_Improvement.png`" in annotated
    assert "> *[Referenced Collateral: `Sprint_49_5_Devin_Integration_Team_Metrics.png`" in annotated

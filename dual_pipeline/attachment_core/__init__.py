"""
dual_pipeline.attachment_core: Governed meeting attachment and multimodal collateral ingestion subsystem.
"""
from __future__ import annotations

from .aligner import (
    align_attachments_with_transcript,
    align_by_drop_timestamp,
    build_collateral_annotations,
    inject_attachment_annotations,
)
from .collateral_manifest import (
    build_collateral_manifest,
    is_collateral_grounded,
    load_collateral_manifest,
    write_collateral_manifest,
)
from .detector import (
    classify_attachment_type,
    discover_attachments,
    stage_attachments_into_output,
)
from .extractors import (
    extract_attachment_content,
    render_slide_thumbnails,
)
from .prompt_builder import build_attachment_prompt_section, serialize_attachment_records_with_content
from .review_provenance import (
    ReviewProvenanceResult,
    evaluate_attachment_review_provenance,
    has_authenticated_mention,
    is_authenticated_drop,
)
from .types import (
    AttachmentMention,
    AttachmentRecord,
    AttachmentType,
    CollateralAnnotation,
    CollateralDrop,
    CrossModalAttachmentAlignment,
    ExtractedAttachmentContent,
)

__all__ = [
    "AttachmentMention",
    "AttachmentRecord",
    "AttachmentType",
    "CollateralAnnotation",
    "CollateralDrop",
    "CrossModalAttachmentAlignment",
    "ExtractedAttachmentContent",
    "ReviewProvenanceResult",
    "align_attachments_with_transcript",
    "align_by_drop_timestamp",
    "build_attachment_prompt_section",
    "build_collateral_annotations",
    "build_collateral_manifest",
    "classify_attachment_type",
    "discover_attachments",
    "evaluate_attachment_review_provenance",
    "extract_attachment_content",
    "has_authenticated_mention",
    "inject_attachment_annotations",
    "is_authenticated_drop",
    "is_collateral_grounded",
    "load_collateral_manifest",
    "render_slide_thumbnails",
    "stage_attachments_into_output",
    "write_collateral_manifest",
]

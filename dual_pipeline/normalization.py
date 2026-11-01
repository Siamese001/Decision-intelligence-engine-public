"""Meeting and architecture adapters that emit source-bound discrete candidates.

Backward-compatibility facade delegating to `dual_pipeline.normalization_core`.
Preserves 100% symbol parity, adapter signatures, and contract validation.
"""
from __future__ import annotations

import base64
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .a2a_finalization import VerifiedA2AFinalization, verified_finalization_binding
from .contracts import create_artifact, validate_artifact
from .core import (
    digest,
    normalize_iso_date,
    normalize_space,
    sha256_bytes,
    stable_id,
    trace_id,
)
from .docx_source import extract_docx_markdown
from .html_source import extract_html_document
from .normalization_core import (
    AUTHORITY_RANKS,
    SEMANTIC_CORRECTION_CANDIDATE_KEY,
    SEMANTIC_CORRECTION_PACKAGE_KEY,
    _canonical_selected_span_locator,
    _context_extension,
    _extension,
    _markdown_semantic_correction,
    _mention,
    _normalize_meeting,
    _normalize_rendered_source,
    _render_markdown_for_retrieval,
    _require_aware_timestamp,
    _role_workstream,
    _selected_span_selection_bindings,
    _statement_semantics,
    _validated_a2a_finalization_binding,
    normalize_a2a_fact_vector_packet,
    normalize_architecture,
    normalize_copilot,
    normalize_docx_source,
    normalize_evidence_envelope,
    normalize_file,
    normalize_google_meet_gemini,
    normalize_html_source,
    normalize_markdown_source,
    normalize_pdf_source,
    normalize_pptx_source,
)
from .pdf_source import extract_pdf_pages, render_pdf_markdown, render_pdf_range
from .pptx_source import extract_pptx_markdown
from .project_status import (
    PROJECT_STATUS_CANDIDATE_PROFILES_KEY,
    PROJECT_STATUS_SNAPSHOT_KEY,
    validate_snapshot_profile,
)
from .source_spans import HUMAN_ATTESTATION, SELECTION_SCHEMA_VERSION
from .source_trust import assert_pinned_vendor_host
from .strategic_ingestion import (
    STRATEGIC_CANDIDATE_PROFILES_KEY,
    source_chunk_profiles,
)

__all__ = [
    "AUTHORITY_RANKS",
    "Any",
    "HUMAN_ATTESTATION",
    "PROJECT_STATUS_CANDIDATE_PROFILES_KEY",
    "PROJECT_STATUS_SNAPSHOT_KEY",
    "Path",
    "SELECTION_SCHEMA_VERSION",
    "SEMANTIC_CORRECTION_CANDIDATE_KEY",
    "SEMANTIC_CORRECTION_PACKAGE_KEY",
    "STRATEGIC_CANDIDATE_PROFILES_KEY",
    "VerifiedA2AFinalization",
    "_canonical_selected_span_locator",
    "_context_extension",
    "_extension",
    "_markdown_semantic_correction",
    "_mention",
    "_normalize_meeting",
    "_normalize_rendered_source",
    "_render_markdown_for_retrieval",
    "_require_aware_timestamp",
    "_role_workstream",
    "_selected_span_selection_bindings",
    "_statement_semantics",
    "_validated_a2a_finalization_binding",
    "assert_pinned_vendor_host",
    "base64",
    "create_artifact",
    "datetime",
    "digest",
    "extract_docx_markdown",
    "extract_html_document",
    "extract_pdf_pages",
    "extract_pptx_markdown",
    "normalize_a2a_fact_vector_packet",
    "normalize_architecture",
    "normalize_copilot",
    "normalize_docx_source",
    "normalize_evidence_envelope",
    "normalize_file",
    "normalize_google_meet_gemini",
    "normalize_html_source",
    "normalize_iso_date",
    "normalize_markdown_source",
    "normalize_pdf_source",
    "normalize_pptx_source",
    "normalize_space",
    "re",
    "render_pdf_markdown",
    "render_pdf_range",
    "sha256_bytes",
    "source_chunk_profiles",
    "stable_id",
    "trace_id",
    "validate_artifact",
    "validate_snapshot_profile",
    "verified_finalization_binding",
]

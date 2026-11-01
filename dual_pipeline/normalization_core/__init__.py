"""Candidate normalization subpackage.

Provides deterministic decomposition of meetings, documents, presentations,
and architecture diagrams into source-bound discrete candidate bundles.
"""
from __future__ import annotations

from .a2a import (
    _canonical_selected_span_locator,
    _require_aware_timestamp,
    _selected_span_selection_bindings,
    _validated_a2a_finalization_binding,
    normalize_a2a_fact_vector_packet,
)
from .architecture import (
    normalize_architecture,
    normalize_file,
)
from .constants import (
    AUTHORITY_RANKS,
    SEMANTIC_CORRECTION_CANDIDATE_KEY,
    SEMANTIC_CORRECTION_PACKAGE_KEY,
)
from .meeting import (
    _context_extension,
    _extension,
    _mention,
    _normalize_meeting,
    _role_workstream,
    _statement_semantics,
    normalize_copilot,
    normalize_evidence_envelope,
    normalize_google_meet_gemini,
)
from .meeting_entity_helpers import _markdown_semantic_correction
from .sources import (
    _normalize_rendered_source,
    _render_markdown_for_retrieval,
    normalize_docx_source,
    normalize_html_source,
    normalize_markdown_source,
    normalize_pdf_source,
    normalize_pptx_source,
)

__all__ = [
    "AUTHORITY_RANKS",
    "SEMANTIC_CORRECTION_CANDIDATE_KEY",
    "SEMANTIC_CORRECTION_PACKAGE_KEY",
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
    "normalize_a2a_fact_vector_packet",
    "normalize_architecture",
    "normalize_copilot",
    "normalize_docx_source",
    "normalize_evidence_envelope",
    "normalize_file",
    "normalize_google_meet_gemini",
    "normalize_html_source",
    "normalize_markdown_source",
    "normalize_pdf_source",
    "normalize_pptx_source",
]

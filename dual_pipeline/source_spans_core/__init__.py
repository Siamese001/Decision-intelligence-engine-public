"""Source spans subpackage for deterministic evidence-span proposals."""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from dual_pipeline.core import (
    assert_safe_mutable_path,
    normalize_space,
    sha256_bytes,
    sha256_file,
    slug,
    stable_id,
)
from dual_pipeline.exceptions import DualPipelineValueError

from .constants import (
    _HEADING_TAG,
    _HIDDEN_TAGS,
    _SENTENCE_END,
    _STOP_WORDS,
    _WORD,
    CONTEXT_CHARACTERS,
    EXTRACTOR_NAME,
    EXTRACTOR_VERSION,
    HUMAN_ATTESTATION,
    MAX_QUOTE_CHARACTERS,
    SELECTION_METHOD,
    SELECTION_SCHEMA_VERSION,
)
from .extractor import (
    _anchor_hint_matches,
    _candidate_spans,
    _heading_path,
    _located_headings,
    _score_span,
    _sentence_spans,
    _terms,
    decode_response,
    extract_visible_document,
    sections_for_evidence,
)
from .models import (
    Heading,
    Section,
    SourceSpanError,
    VisibleDocument,
    _VisibleStructureExtractor,
)
from .proposals import (
    build_span_proposals,
    validate_capture_document,
    validate_finalized_span,
    validate_span_proposal,
    verify_runtime_a2a_capture,
)

__all__ = [
    "Any",
    "CONTEXT_CHARACTERS",
    "DualPipelineValueError",
    "EXTRACTOR_NAME",
    "EXTRACTOR_VERSION",
    "HTMLParser",
    "HUMAN_ATTESTATION",
    "Heading",
    "MAX_QUOTE_CHARACTERS",
    "Path",
    "SELECTION_METHOD",
    "SELECTION_SCHEMA_VERSION",
    "Section",
    "SourceSpanError",
    "VisibleDocument",
    "_HEADING_TAG",
    "_HIDDEN_TAGS",
    "_SENTENCE_END",
    "_STOP_WORDS",
    "_VisibleStructureExtractor",
    "_WORD",
    "_anchor_hint_matches",
    "_candidate_spans",
    "_heading_path",
    "_located_headings",
    "_score_span",
    "_sentence_spans",
    "_terms",
    "annotations",
    "assert_safe_mutable_path",
    "build_span_proposals",
    "dataclass",
    "decode_response",
    "extract_visible_document",
    "html",
    "json",
    "normalize_space",
    "re",
    "sections_for_evidence",
    "sha256_bytes",
    "sha256_file",
    "slug",
    "stable_id",
    "validate_capture_document",
    "validate_finalized_span",
    "validate_span_proposal",
    "verify_runtime_a2a_capture",
]

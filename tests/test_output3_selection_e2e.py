import pytest
from pathlib import Path
from dual_pipeline.cli.parser import build_parser

def test_output3_only_transcript_metadata_e2e():
    """Verify that --output3-only does not coerce transcripts into document semantics."""
    parser = build_parser()
    args = parser.parse_args(["ingest-meeting", "--input", "transcript.vtt", "--output3-only", "--project", "test"])
    assert getattr(args, "output3_only", False) is True
    from dual_pipeline.cli.ingest import is_document_source
    from dual_pipeline.cli.ingest import build_canonical_metadata
    meta = build_canonical_metadata(args)
    is_doc = is_document_source("transcript.vtt", meta)
    assert is_doc is False

def test_document_metadata_e2e():
    """Verify that --document still correctly flags as document source."""
    parser = build_parser()
    args = parser.parse_args(["ingest-meeting", "--input", "transcript.vtt", "--document", "--project", "test"])
    assert getattr(args, "document", False) is True
    from dual_pipeline.cli.ingest import build_canonical_metadata, is_document_source
    meta = build_canonical_metadata(args)
    is_doc = is_document_source("transcript.vtt", meta)
    assert is_doc is True

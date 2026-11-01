import pytest
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.cli.ingest import is_document_source, build_canonical_metadata

def test_adversarial_contradictory_flags():
    """Test that contradictory flags fail cleanly."""
    parser = build_parser()
    args1 = parser.parse_args(["ingest-meeting", "--input", "foo.txt", "--document", "--notes", "--project", "test"])
    
    with pytest.raises(ValueError):
        build_canonical_metadata(args1)
        
    args2 = parser.parse_args(["ingest-meeting", "--input", "foo.txt", "--notes", "--output3-only", "--project", "test"])
    with pytest.raises(ValueError):
        build_canonical_metadata(args2)

def test_adversarial_file_extensions():
    """Ensure that output3-only does not bypass file extension checks when --document is NOT specified."""
    parser = build_parser()
    args = parser.parse_args(["ingest-meeting", "--input", "transcript.vtt", "--output3-only", "--project", "test"])
    meta = build_canonical_metadata(args)
    assert is_document_source("transcript.vtt", meta) is False
    
    args_pdf = parser.parse_args(["ingest-meeting", "--input", "report.pdf", "--output3-only", "--project", "test"])
    meta_pdf = build_canonical_metadata(args_pdf)
    assert is_document_source("report.pdf", meta_pdf) is True

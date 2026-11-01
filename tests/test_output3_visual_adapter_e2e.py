import pytest
from pathlib import Path
from dual_pipeline.document_pipeline_core.content_loader import load_document_content
from dual_pipeline.transcript_pipeline_core.format_parsers import parse_legacy_msg, parse_legacy_ppt
from dual_pipeline.attachment_core.image_extractor import extract_image_visual

class TestOutput3VisualAdapterE2E:
    def test_xlsx_adapter_loading(self, tmp_path):
        # We need a small valid xlsx or fallback csv
        import io
        import zipfile
        
        # We'll just write some bytes that trigger fallback to simulated csv because we don't have openpyxl
        # wait, fallback only happens if we try to load it. For fallback, it expects comma separated text.
        fake_xlsx = tmp_path / "fake.xlsx"
        fake_xlsx.write_bytes(b"Name,Age\nAlice,30\nBob,40")
        
        metadata = {}
        text, sha, ext = load_document_content(fake_xlsx, metadata=metadata)
        assert ext == "xlsx"
        assert "Row" in text
        assert "Alice" in text
        assert metadata.get("_source_coverage", {}).get("method") == "xlsx_data_only"

    def test_legacy_msg_adapter(self):
        # Test parse_legacy_msg
        text = parse_legacy_msg(b"fake msg data")
        assert "fake msg data" in text

    def test_legacy_ppt_adapter(self):
        # Test parse_legacy_ppt
        text = parse_legacy_ppt(b"fake ppt data")
        assert "fake ppt data" in text

    def test_visual_adapter_png(self, tmp_path):
        fake_png = tmp_path / "test_diagram.png"
        fake_png.write_bytes(b"fake png")
        result = extract_image_visual(fake_png, mock_mode=True)
        assert "Simulated Visual Text for test_diagram.png" in result["text"]
        assert len(result["connections"]) == 0
        assert result.get("_visual_evidence_only") is True

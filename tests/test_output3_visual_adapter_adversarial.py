import pytest
from pathlib import Path
from dual_pipeline.document_pipeline_core.content_loader import load_document_content

class TestOutput3VisualAdapterAdversarial:
    def test_image_rejected_as_document(self, tmp_path):
        # PNG should be rejected by document content loader
        fake_png = tmp_path / "fake.png"
        fake_png.write_bytes(b"fake png")
        with pytest.raises(ValueError, match="Binary image format"):
            load_document_content(fake_png)

    def test_unknown_format_rejected(self, tmp_path):
        fake_unknown = tmp_path / "fake.unknown"
        fake_unknown.write_bytes(b"fake")
        with pytest.raises(ValueError, match="Unknown document extension"):
            load_document_content(fake_unknown)

    def test_empty_xlsx(self, tmp_path):
        from dual_pipeline.exceptions import SourceIntegrityError
        fake_xlsx = tmp_path / "empty.xlsx"
        fake_xlsx.write_bytes(b"")
        metadata = {}
        with pytest.raises(SourceIntegrityError):
            load_document_content(fake_xlsx, metadata=metadata)

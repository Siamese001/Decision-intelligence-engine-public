import unittest
from dual_pipeline.document_pipeline_core.content_loader import _dispatch_file_loader
from pathlib import Path

class TestSourceAdapterParityAdversarial(unittest.TestCase):
    def test_image_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            _dispatch_file_loader(".png", Path("test.png"), b"fake")
        self.assertIn("cannot be ingested as text document", str(ctx.exception))

    def test_malformed_docx(self):
        with self.assertRaises(ValueError) as ctx:
            _dispatch_file_loader(".docx", Path("test.docx"), b"not a zip")
        self.assertIn("valid word/document.xml", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()

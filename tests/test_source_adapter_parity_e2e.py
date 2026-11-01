import unittest
from pathlib import Path
from dual_pipeline.document_pipeline_core.content_loader import load_document_content
import tempfile
import json
import csv
import zipfile
import io

class TestSourceAdapterParityE2E(unittest.TestCase):
    def test_csv_coverage(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as f:
            writer = csv.writer(f)
            writer.writerow(["col1", "col2"])
            for i in range(30):
                writer.writerow([f"a{i}", f"b{i}"])
            f.flush()
            meta = {}
            text, h, fmt = load_document_content(f.name, metadata=meta)
            self.assertEqual(fmt, "csv")
            self.assertIn("| R30 |", text)
            self.assertTrue(meta["_source_coverage"]["complete"])
            self.assertEqual(meta["_source_coverage"]["total_records"], 30)

    def test_eml_coverage(self):
        eml = b"Message-ID: <123@abc.com>\nFrom: A\nTo: B\nSubject: Hello\n\nBody test."
        with tempfile.NamedTemporaryFile(suffix=".eml", delete=False) as f:
            f.write(eml)
            f.flush()
            meta = {}
            text, h, fmt = load_document_content(f.name, metadata=meta)
            self.assertEqual(fmt, "eml")
            self.assertIn("Message-ID: <123@abc.com>", text)
            self.assertIn("Body test.", text)

    def test_docx_coverage(self):
        xml = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Docx body text</w:t></w:r></w:p>
  </w:body>
</w:document>"""
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            with zipfile.ZipFile(f, "w") as z:
                z.writestr("word/document.xml", xml)
            meta = {}
            text, h, fmt = load_document_content(f.name, metadata=meta)
            self.assertEqual(fmt, "docx")
            self.assertIn("Docx body text", text)
            self.assertIn("_source_coverage", meta)
            self.assertEqual(meta["_source_coverage"]["paragraph_count"], 1)

if __name__ == "__main__":
    unittest.main()

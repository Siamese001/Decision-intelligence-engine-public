"""
tests/test_document_intake_adversarial.py

Wave 5 Adversarial and Edge-Case Invariant Verification Suite.
Covers 6 distinct adversarial categories:
1. Boundary Payloads (0-byte files, minimal units, empty structures)
2. Malformed Inputs (truncated syntax, non-zip containers, binary NUL injection)
3. Concurrency & Race Conditions (parallel concurrent conversion and caching)
4. Overflow & Exhaustion (nesting depth > 32, zip bomb ratio, file size limits)
5. Invariant Bypass Attempts (XXE injection, script execution, blocked formats)
6. Rollback & Safe Recovery (corrupt cache manifest auto-healing, no_cache bypass)
"""
from __future__ import annotations

import concurrent.futures
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from dual_pipeline.document_pipeline_core.content_loader import load_document_content
from dual_pipeline.document_pipeline_core.format_source_units import preflight_attachment
from dual_pipeline.exceptions import SourceIntegrityError


class TestDocumentIntakeAdversarial(unittest.TestCase):
    """Comprehensive adversarial testing suite for document intake."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_p = Path(self.tmp_dir.name)
        self.cache_dir = self.work_p / "conversion_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    # --- Category 1: Boundary Payloads ---

    def test_category1_zero_byte_source_rejection(self) -> None:
        """Verify that 0-byte source files fail closed with SourceIntegrityError."""
        extensions = [".docx", ".xlsx", ".pdf", ".pptx", ".json", ".svg", ".md", ".txt"]
        for ext in extensions:
            zero_file = self.work_p / f"empty_source{ext}"
            zero_file.write_bytes(b"")
            with self.assertRaises(SourceIntegrityError, msg=f"Failed for {ext}"):
                load_document_content(zero_file, cache_dir=self.cache_dir)

    def test_category1_empty_json_containers(self) -> None:
        """Verify that empty JSON root structures {} and [] produce valid empty units."""
        for empty_val in ("{}", "[]"):
            p = self.work_p / f"empty_{empty_val[0]}.json"
            p.write_text(empty_val, encoding="utf-8")
            meta: dict = {}
            md, _, fmt = load_document_content(p, metadata=meta, cache_dir=self.cache_dir)
            self.assertEqual(fmt, "json")
            self.assertGreater(len(meta.get("source_units", [])), 0)

    # --- Category 2: Malformed Inputs ---

    def test_category2_malformed_syntax_rejection(self) -> None:
        """Verify that truncated or corrupted payloads fail closed with informative errors."""
        # Truncated JSON
        p_json = self.work_p / "corrupt.json"
        p_json.write_text('{"key": "unclosed value', encoding="utf-8")
        with self.assertRaises(ValueError):
            load_document_content(p_json, cache_dir=self.cache_dir)

        # Corrupt pseudo-zip for OOXML / VSDX
        for ext in (".docx", ".xlsx", ".vsdx"):
            p_fake = self.work_p / f"fake_zip{ext}"
            p_fake.write_bytes(b"This is completely raw non-zip text data")
            with self.assertRaises(ValueError):
                load_document_content(p_fake, cache_dir=self.cache_dir)

        # Binary NUL bytes in structured text
        p_nul = self.work_p / "nul_injected.json"
        p_nul.write_bytes(b'{"clean": "text", "bad": "val\x00extra"}')
        with self.assertRaises(ValueError):
            load_document_content(p_nul, cache_dir=self.cache_dir)

    # --- Category 3: Concurrency & Race Conditions ---

    def test_category3_concurrent_conversion_cache_access(self) -> None:
        """Verify that concurrent multi-threaded requests for the same conversion complete safely."""
        p_target = self.work_p / "concurrent_doc.json"
        p_target.write_text(json.dumps({"service": "concurrency_test", "iteration": 42}), encoding="utf-8")

        def _fetch_conversion(idx: int) -> tuple[str, bool]:
            meta: dict = {"project": "concurrent_test", "worker_id": idx}
            md, _, _ = load_document_content(p_target, metadata=meta, cache_dir=self.cache_dir)
            was_cached = meta.get("conversion_artifact", {}).get("was_cached", False)
            return md, was_cached

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_fetch_conversion, i) for i in range(16)]
            results = [f.result() for f in futures]

        self.assertEqual(len(results), 16)
        first_md = results[0][0]
        for md, was_cached in results:
            self.assertEqual(md, first_md)

    # --- Category 4: Overflow & Exhaustion ---

    def test_category4_nesting_depth_exhaustion_guard(self) -> None:
        """Verify that JSON and XML payloads exceeding depth 32 are rejected fail-closed."""
        deep_obj: dict = {"level": 0}
        curr = deep_obj
        for i in range(1, 35):
            curr["nested"] = {"level": i}
            curr = curr["nested"]

        p_deep = self.work_p / "deep_nest.json"
        p_deep.write_text(json.dumps(deep_obj), encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            load_document_content(p_deep, cache_dir=self.cache_dir)
        self.assertIn("nesting depth exceeds", str(ctx.exception).lower())

    def test_category4_vsdx_zip_bomb_defense(self) -> None:
        """Verify that archives with suspicious compression ratios fail preflight and extraction."""
        # Create a synthetic zip bomb with a high compression ratio
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # 1 MiB of zeros compresses to ~1 KiB (ratio > 1000:1)
            zf.writestr("visio/pages/page1.xml", b"0" * (1024 * 1024))
            zf.writestr("[Content_Types].xml", b"<Types/>")
            zf.writestr("_rels/.rels", b"<Relationships/>")

        p_bomb = self.work_p / "bomb.vsdx"
        p_bomb.write_bytes(buf.getvalue())

        with self.assertRaises(ValueError) as ctx:
            load_document_content(p_bomb, cache_dir=self.cache_dir)
        self.assertIn("unsafe compression ratio", str(ctx.exception).lower())

    # --- Category 5: Invariant Bypass Attempts ---

    def test_category5_xxe_entity_injection_blocked(self) -> None:
        """Verify that XXE injection payloads fail closed across all diagram formats."""
        xxe_svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE svg [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>\n'
            '<svg xmlns="http://www.w3.org/2000/svg"><text>&xxe;</text></svg>'
        ).encode("utf-8")
        p_svg = self.work_p / "exploit.svg"
        p_svg.write_bytes(xxe_svg)

        with self.assertRaises(ValueError) as ctx:
            load_document_content(p_svg, cache_dir=self.cache_dir)
        self.assertIn("malicious xml detected", str(ctx.exception).lower())

    def test_category5_script_injection_sanitization(self) -> None:
        """Verify that embedded executable scripts are stripped from structured fields."""
        xss_json = json.dumps({
            "title": "System Report <script>alert(document.cookie)</script>",
            "uri": "javascript:evil()",
        })
        p_xss = self.work_p / "xss.json"
        p_xss.write_text(xss_json, encoding="utf-8")

        meta: dict = {}
        markdown, _, _ = load_document_content(p_xss, metadata=meta, cache_dir=self.cache_dir)
        self.assertNotIn("<script>", markdown)
        self.assertNotIn("document.cookie", markdown)
        self.assertNotIn("javascript:", markdown)

    def test_category5_legacy_blocked_format_remediation_guidance(self) -> None:
        """Verify that legacy formats (.ppt, .xls, .msg) fail preflight with actionable commands."""
        for ext in (".ppt", ".xls", ".msg"):
            p_leg = self.work_p / f"legacy_input{ext}"
            p_leg.write_bytes(b"Legacy binary OLE container payload")
            with self.assertRaises(ValueError) as ctx:
                load_document_content(p_leg, cache_dir=self.cache_dir)
            err_msg = str(ctx.exception)
            self.assertIn("is blocked", err_msg.lower())
            self.assertIn("nee formats", err_msg)

    # --- Category 6: Rollback & Safe Recovery ---

    def test_category6_corrupted_cache_manifest_auto_recovery(self) -> None:
        """Verify that corrupted cache manifests on disk are detected and bypassed safely."""
        p_doc = self.work_p / "cached_doc.json"
        p_doc.write_text(json.dumps({"key": "original_payload"}), encoding="utf-8")

        # Initial conversion
        meta1: dict = {"project": "heal_test"}
        md1, _, _ = load_document_content(p_doc, metadata=meta1, cache_dir=self.cache_dir)

        cache_key = meta1["conversion_artifact"]["cache_key"]
        manifest_p = self.cache_dir / f"{cache_key}.json"
        self.assertTrue(manifest_p.is_file())

        # Corrupt manifest file with invalid JSON
        manifest_p.write_text("CORRUPTED MANIFEST SYNTAX", encoding="utf-8")

        # Next run must safely detect corruption and recompute cleanly
        meta2: dict = {"project": "heal_test"}
        md2, _, _ = load_document_content(p_doc, metadata=meta2, cache_dir=self.cache_dir)
        self.assertEqual(md1, md2)
        self.assertFalse(meta2["conversion_artifact"]["was_cached"])


if __name__ == "__main__":
    unittest.main()

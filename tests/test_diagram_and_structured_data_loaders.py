"""
tests/test_diagram_and_structured_data_loaders.py

Wave 4 Verification Suite:
Text-First Diagram and Structured Data Loaders (.svg, .drawio, .vsdx, .json).
Verifies deterministic extraction of topology, shapes, field paths, and locators,
enforces XXE and script rejection, and verifies source unit integrity.
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.parse
import zipfile
import zlib

from dual_pipeline.document_pipeline_core.content_loader import load_document_content
from dual_pipeline.document_pipeline_core.format_source_units import preflight_attachment
from dual_pipeline.document_pipeline_core.structured_data_loader import (
    _check_nesting_depth,
    _flatten_json,
    _sanitize_string,
    build_json_source_units,
    extract_json_with_locators,
)
from dual_pipeline.document_pipeline_core.diagram_loader import (
    _assert_safe_xml_bytes,
    _decompress_drawio_payload,
    _validate_vsdx_zip_safety,
    build_diagram_source_units,
    extract_diagram_with_locators,
)


def _compress_drawio_payload(xml_str: str) -> str:
    """Helper to produce standard compressed Draw.io XML payloads."""
    quoted = urllib.parse.quote(xml_str)
    compressor = zlib.compressobj(level=9, wbits=-15)
    deflated = compressor.compress(quoted.encode("utf-8")) + compressor.flush()
    return base64.b64encode(deflated).decode("ascii")


def _build_test_vsdx(title: str = "Architecture Map") -> bytes:
    """Build a minimal valid VSDX OpenXML zip package in memory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
            '<Default Extension="xml" ContentType="application/xml"/>\n'
            '<Override PartName="/visio/document.xml" ContentType="application/vnd.ms-visio.drawing.main+xml"/>\n'
            '</Types>',
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
            '<Relationship Id="rId1" Type="http://schemas.microsoft.com/visio/2010/relationships/document" Target="visio/document.xml"/>\n'
            '</Relationships>',
        )
        zf.writestr(
            "visio/pages/pages.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Pages xmlns="http://schemas.microsoft.com/office/visio/2012/main">\n'
            f'  <Page ID="0" Name="{title}" ViewCentric="0"/>\n'
            '</Pages>',
        )
        zf.writestr(
            "visio/pages/page1.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main">\n'
            '  <Shapes>\n'
            '    <Shape ID="10" NameU="Process">\n'
            '      <Text>Ingestion Coordinator</Text>\n'
            '    </Shape>\n'
            '    <Shape ID="20" NameU="Database">\n'
            '      <Text>Canonical Knowledge Graph</Text>\n'
            '    </Shape>\n'
            '  </Shapes>\n'
            '  <Connects>\n'
            '    <Connect FromSheet="30" ToSheet="10" FromCell="BeginX" ToCell="PinX"/>\n'
            '    <Connect FromSheet="30" ToSheet="20" FromCell="EndX" ToCell="PinX"/>\n'
            '  </Connects>\n'
            '</PageContents>',
        )
    return buf.getvalue()


class TestJsonStructuredDataLoader(unittest.TestCase):
    """Test suite for hierarchical JSON extraction with JSONPath locators."""

    def test_json_flattening_and_locators(self) -> None:
        payload = {
            "title": "System Specification",
            "version": 2,
            "enabled": True,
            "metadata": {
                "author": "Engineering",
                "tags": ["core", "infra"],
            },
            "services": [
                {"id": "ingest", "port": 8080},
                {"id": "query", "port": 8081},
            ],
        }
        leafs = _flatten_json(payload)
        locators = [loc for loc, val, vtype in leafs]

        self.assertIn("$.title", locators)
        self.assertIn("$.version", locators)
        self.assertIn("$.enabled", locators)
        self.assertIn("$.metadata.author", locators)
        self.assertIn("$.metadata.tags[0]", locators)
        self.assertIn("$.metadata.tags[1]", locators)
        self.assertIn("$.services[0].id", locators)
        self.assertIn("$.services[0].port", locators)
        self.assertIn("$.services[1].id", locators)
        self.assertIn("$.services[1].port", locators)

    def test_json_script_sanitization_and_dangerous_uris(self) -> None:
        malicious_str = "Safe text <script>alert('xss')</script> and javascript:alert(1)"
        cleaned = _sanitize_string(malicious_str)
        self.assertNotIn("<script>", cleaned)
        self.assertNotIn("alert('xss')", cleaned)
        self.assertIn("[BLOCKED_URI]:", cleaned)

    def test_json_nesting_depth_guard(self) -> None:
        deep_dict: dict = {"level": 0}
        curr = deep_dict
        for i in range(1, 35):
            curr["child"] = {"level": i}
            curr = curr["child"]

        with self.assertRaises(ValueError) as ctx:
            _check_nesting_depth(deep_dict)
        self.assertIn("nesting depth exceeds", str(ctx.exception).lower())

    def test_json_content_loader_integration_and_units(self) -> None:
        data = {
            "deployment": "production",
            "replicas": 3,
            "endpoints": ["/api/v1/query", "/api/v1/ingest"],
        }
        raw_bytes = json.dumps(data).encode("utf-8")

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            f.write(raw_bytes)
            tmp_p = Path(f.name)

        try:
            meta: dict = {}
            markdown, s_hash, fmt = load_document_content(tmp_p, metadata=meta)
            self.assertEqual(fmt, "json")
            self.assertIn("Structured Data", markdown)
            self.assertIn("`$.deployment`", markdown)
            self.assertIn("`$.replicas`", markdown)
            self.assertIn("`$.endpoints[0]`", markdown)

            units = meta.get("source_units", [])
            self.assertGreaterEqual(len(units), 4)
            for u in units:
                self.assertEqual(u["format"], "json")
                self.assertTrue(u["locator"].startswith("$"))
                self.assertEqual(u["source_hash"], s_hash)

            coverage = meta.get("source_coverage", {})
            self.assertTrue(coverage.get("complete"))
            self.assertEqual(coverage.get("units_count"), len(units))
        finally:
            tmp_p.unlink(missing_ok=True)


class TestSvgDiagramLoader(unittest.TestCase):
    """Test suite for SVG diagram text, shapes, and security stripping."""

    def test_svg_extraction_with_locators(self) -> None:
        svg_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600">\n'
            '  <title>Service Architecture</title>\n'
            '  <desc>Distributed ingestion workflow</desc>\n'
            '  <rect id="node1" aria-label="Ingestion Engine" x="50" y="50" width="120" height="60"/>\n'
            '  <circle id="node2" aria-label="Vector Store" cx="300" cy="80" r="40"/>\n'
            '  <text id="t1" x="60" y="80">Pipeline A</text>\n'
            '  <line id="edge1" data-from="node1" data-to="node2" x1="170" y1="80" x2="260" y2="80"/>\n'
            '</svg>'
        )
        raw_bytes = svg_xml.encode("utf-8")

        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as f:
            f.write(raw_bytes)
            tmp_p = Path(f.name)

        try:
            meta: dict = {}
            markdown, s_hash, fmt = load_document_content(tmp_p, metadata=meta)
            self.assertEqual(fmt, "svg")
            self.assertIn("SVG Diagram", markdown)
            self.assertIn("Pipeline A", markdown)
            self.assertIn("Ingestion Engine", markdown)
            self.assertIn("Vector Store", markdown)

            units = meta.get("source_units", [])
            locators = [u["locator"] for u in units]
            self.assertIn("desc", locators)
            self.assertTrue(any("text" in loc for loc in locators))
            self.assertIn("shapenode1", locators)
            self.assertIn("connectoredge1", locators)

            coverage = meta.get("source_coverage", {})
            self.assertTrue(coverage.get("complete"))
            self.assertGreaterEqual(coverage.get("labels_extracted", 0), 1)
        finally:
            tmp_p.unlink(missing_ok=True)

    def test_svg_xxe_rejection(self) -> None:
        malicious_svg = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE svg [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>\n'
            '<svg xmlns="http://www.w3.org/2000/svg">\n'
            '  <text>&xxe;</text>\n'
            '</svg>'
        ).encode("utf-8")

        with self.assertRaises(ValueError) as ctx:
            _assert_safe_xml_bytes(malicious_svg, "SVG")
        self.assertIn("Malicious XML detected", str(ctx.exception))

    def test_svg_script_and_event_stripping(self) -> None:
        svg_with_script = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg">\n'
            '  <script>alert("evil")</script>\n'
            '  <rect id="box" onclick="alert(1)" aria-label="Clean Box"/>\n'
            '  <text>Clean Label</text>\n'
            '</svg>'
        )
        meta: dict = {}
        markdown = extract_diagram_with_locators(Path("test.svg"), svg_with_script.encode("utf-8"), meta, ext=".svg")
        self.assertNotIn("alert", markdown)
        self.assertIn("Clean Box", markdown)
        self.assertIn("Clean Label", markdown)


class TestDrawioDiagramLoader(unittest.TestCase):
    """Test suite for uncompressed and compressed Draw.io XML models."""

    def test_drawio_uncompressed_xml(self) -> None:
        drawio_xml = (
            '<mxfile host="Electron" modified="2026-10-12T12:00:00.000Z">\n'
            '  <diagram id="d1" name="Pipeline Architecture">\n'
            '    <mxGraphModel>\n'
            '      <root>\n'
            '        <mxCell id="0"/>\n'
            '        <mxCell id="1" parent="0"/>\n'
            '        <mxCell id="c1" value="Stage 1: Normalization" vertex="1" parent="1"/>\n'
            '        <mxCell id="c2" value="Stage 2: Extraction" vertex="1" parent="1"/>\n'
            '        <mxCell id="e1" value="Emits Tokens" edge="1" source="c1" target="c2" parent="1"/>\n'
            '      </root>\n'
            '    </mxGraphModel>\n'
            '  </diagram>\n'
            '</mxfile>'
        )
        raw_bytes = drawio_xml.encode("utf-8")

        with tempfile.NamedTemporaryFile(suffix=".drawio", delete=False) as f:
            f.write(raw_bytes)
            tmp_p = Path(f.name)

        try:
            meta: dict = {}
            markdown, s_hash, fmt = load_document_content(tmp_p, metadata=meta)
            self.assertEqual(fmt, "drawio")
            self.assertIn("Stage 1: Normalization", markdown)
            self.assertIn("Stage 2: Extraction", markdown)
            self.assertIn("Emits Tokens", markdown)

            units = meta.get("source_units", [])
            locators = [u["locator"] for u in units]
            self.assertTrue(any("cellc1" in loc for loc in locators))
            self.assertTrue(any("cellc2" in loc for loc in locators))
            self.assertTrue(any("connectore1" in loc for loc in locators))
        finally:
            tmp_p.unlink(missing_ok=True)

    def test_drawio_compressed_deflated_payload(self) -> None:
        inner_model = (
            '<mxGraphModel>\n'
            '  <root>\n'
            '    <mxCell id="0"/>\n'
            '    <mxCell id="1" parent="0"/>\n'
            '    <mxCell id="n10" value="Microservice Gateway" vertex="1" parent="1"/>\n'
            '    <mxCell id="n20" value="Event Bus" vertex="1" parent="1"/>\n'
            '    <mxCell id="edge99" value="Publishes Event" edge="1" source="n10" target="n20" parent="1"/>\n'
            '  </root>\n'
            '</mxGraphModel>'
        )
        compressed_b64 = _compress_drawio_payload(inner_model)
        outer_xml = f'<mxfile><diagram name="Overview">{compressed_b64}</diagram></mxfile>'

        meta: dict = {}
        markdown = extract_diagram_with_locators(Path("test.drawio"), outer_xml.encode("utf-8"), meta, ext=".drawio")
        self.assertIn("Microservice Gateway", markdown)
        self.assertIn("Event Bus", markdown)
        self.assertIn("Publishes Event", markdown)

        units = meta.get("source_units", [])
        self.assertEqual(len(units), 3)


class TestVsdxDiagramLoader(unittest.TestCase):
    """Test suite for Microsoft Visio (.vsdx) OpenXML diagram extraction."""

    def test_vsdx_openxml_extraction_and_locators(self) -> None:
        raw_vsdx = _build_test_vsdx(title="Data Engine Flow")

        with tempfile.NamedTemporaryFile(suffix=".vsdx", delete=False) as f:
            f.write(raw_vsdx)
            tmp_p = Path(f.name)

        try:
            meta: dict = {}
            markdown, s_hash, fmt = load_document_content(tmp_p, metadata=meta)
            self.assertEqual(fmt, "vsdx")
            self.assertIn("Visio Diagram", markdown)
            self.assertIn("Ingestion Coordinator", markdown)
            self.assertIn("Canonical Knowledge Graph", markdown)
            self.assertIn("Connection from shape 30", markdown)

            units = meta.get("source_units", [])
            locators = [u["locator"] for u in units]
            self.assertTrue(any("shape10" in loc for loc in locators))
            self.assertTrue(any("shape20" in loc for loc in locators))
            self.assertTrue(any("connector30" in loc for loc in locators))

            coverage = meta.get("source_coverage", {})
            self.assertTrue(coverage.get("complete"))
            self.assertGreaterEqual(coverage.get("total_shapes", 0), 2)
        finally:
            tmp_p.unlink(missing_ok=True)

    def test_vsdx_zip_safety_and_bomb_protection(self) -> None:
        with self.assertRaises(ValueError):
            _validate_vsdx_zip_safety(b"not a valid zip file")


class TestSourceUnitResolutionAndCoverageParity(unittest.TestCase):
    """Verify that all four formats satisfy SourceUnit resolution and coverage contracts."""

    def test_source_units_contract_integrity(self) -> None:
        formats_and_extractors = [
            ("json", lambda: build_json_source_units({"key": "val", "arr": [1, 2]}, "hash123")),
            ("svg", lambda: build_diagram_source_units(".svg", b'<svg xmlns="http://www.w3.org/2000/svg"><text id="t1">Title</text></svg>', "hash123")),
            ("drawio", lambda: build_diagram_source_units(".drawio", b'<mxfile><diagram name="P1"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/><mxCell id="a" value="Box" vertex="1" parent="1"/></root></mxGraphModel></diagram></mxfile>', "hash123")),
            ("vsdx", lambda: build_diagram_source_units(".vsdx", _build_test_vsdx(), "hash123")),
        ]

        for fmt, extractor in formats_and_extractors:
            units = extractor()
            self.assertGreater(len(units), 0, f"Expected non-zero units for format {fmt}")
            for u in units:
                self.assertEqual(u.format, fmt)
                self.assertTrue(bool(u.locator))
                self.assertTrue(bool(u.content))
                self.assertEqual(u.source_hash, "hash123")
                self.assertTrue(bool(u.digest))


class TestConversionCacheActivationForDiagramAndJson(unittest.TestCase):
    """Verify deterministic conversion caching and invalidation across all Wave 4 formats."""

    def test_cache_roundtrip_and_restoration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_cache_dir:
            payload = json.dumps({"service": "evaluator", "port": 9000}).encode("utf-8")
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
                f.write(payload)
                tmp_p = Path(f.name)

            try:
                meta1: dict = {}
                md1, hash1, fmt1 = load_document_content(tmp_p, metadata=meta1, cache_dir=tmp_cache_dir)
                self.assertFalse(meta1["conversion_artifact"]["was_cached"])
                self.assertEqual(len(meta1["source_units"]), 2)

                # Second call must hit cache and restore units
                meta2: dict = {}
                md2, hash2, fmt2 = load_document_content(tmp_p, metadata=meta2, cache_dir=tmp_cache_dir)
                self.assertTrue(meta2["conversion_artifact"]["was_cached"])
                self.assertEqual(meta1["conversion_artifact"]["cache_key"], meta2["conversion_artifact"]["cache_key"])
                self.assertEqual(len(meta2["source_units"]), 2)
                self.assertEqual(md1, md2)

                # Mutated content must cause cache miss
                mutated = json.dumps({"service": "evaluator", "port": 9001}).encode("utf-8")
                tmp_p.write_bytes(mutated)
                meta3: dict = {}
                md3, hash3, fmt3 = load_document_content(tmp_p, metadata=meta3, cache_dir=tmp_cache_dir)
                self.assertFalse(meta3["conversion_artifact"]["was_cached"])
                self.assertNotEqual(meta1["conversion_artifact"]["cache_key"], meta3["conversion_artifact"]["cache_key"])
            finally:
                tmp_p.unlink(missing_ok=True)


class TestPreflightAttachmentDiagramAndJson(unittest.TestCase):
    """Verify preflight attachment validation and fail-closed security invariants."""

    def test_preflight_valid_and_invalid_json(self) -> None:
        valid_res = preflight_attachment(b'{"key": "value"}', "config.json")
        self.assertTrue(valid_res.is_valid)

        invalid_res = preflight_attachment(b'{not valid json', "config.json")
        self.assertFalse(invalid_res.is_valid)
        self.assertEqual(invalid_res.error_code, "MALFORMED_JSON_SYNTAX")

    def test_preflight_xxe_rejection_in_diagrams(self) -> None:
        xxe_svg = b'<!DOCTYPE svg [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]><svg></svg>'
        res_svg = preflight_attachment(xxe_svg, "arch.svg")
        self.assertFalse(res_svg.is_valid)
        self.assertEqual(res_svg.error_code, "MALICIOUS_SVG_XXE_PROHIBITED")

        xxe_drawio = b'<!DOCTYPE mxfile [ <!ENTITY xxe SYSTEM "http://evil.com"> ]><mxfile></mxfile>'
        res_drawio = preflight_attachment(xxe_drawio, "flow.drawio")
        self.assertFalse(res_drawio.is_valid)
        self.assertEqual(res_drawio.error_code, "MALICIOUS_DRAWIO_XXE_PROHIBITED")

    def test_preflight_vsdx_container(self) -> None:
        valid_vsdx = _build_test_vsdx()
        res_valid = preflight_attachment(valid_vsdx, "network.vsdx")
        self.assertTrue(res_valid.is_valid)

        res_invalid = preflight_attachment(b"not a zip", "corrupt.vsdx")
        self.assertFalse(res_invalid.is_valid)
        self.assertEqual(res_invalid.error_code, "MALFORMED_VSDX_CONTAINER")


if __name__ == "__main__":
    unittest.main()


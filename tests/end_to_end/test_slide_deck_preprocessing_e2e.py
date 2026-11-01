"""
tests/end_to_end/test_slide_deck_preprocessing_e2e.py

End-to-End Test Suite for Slide Deck and Document Preprocessing (Wave 1):
1. Verifies uppercase tracking and kerning repair in PDF slides (e.g. THE BUSINESS OPPORTUNITY, MANAGED SERVICES).
2. Verifies line structure preservation (retains multiline blocks without collapsing to single-line blobs).
3. Verifies repetitive copyright footers and standalone copyright symbols are stripped.
4. Verifies Slide 15 pattern (© symbol + image) triggers visual escalation (ESCALATE_VISUAL) failing closed.
5. Verifies EML extraction prefers substantive plain-text over marketing HTML layout tables and strips tracking boilerplate.
6. Verifies deterministic conversion caching, cache hit replay, and cache invalidation on v1.1.0 converter upgrade.
7. Integration coverage on real 25-slide AMS deck and EML announcement when present on disk.
"""
from __future__ import annotations

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import io
import json
from pathlib import Path
import tempfile
import unittest

import pypdf
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from dual_pipeline.document_pipeline_core.content_loader import load_document_content
from dual_pipeline.document_pipeline_core.executor import (
    _ATTACHMENT_LOCATOR_RE,
    _build_document_user_content,
)
from dual_pipeline.document_pipeline_core.format_source_units import (
    generate_document_structural_index,
)
from dual_pipeline.document_pipeline_core.quality import validate_signal_claim_grounding
from dual_pipeline.pdf_source import (
    extract_pdf_pages,
    inspect_pdf_pages,
    normalize_pdf_page_content,
    repair_kerning,
)

_ONEDRIVE_DOCS = Path.home() / "Library" / "CloudStorage" / "OneDrive-PartnerAdvisory" / "Documents"
REAL_AMS_PDF = _ONEDRIVE_DOCS / "HR IT" / "AMS Training and Solution Materials - AMS 101 - Florida.pdf"
REAL_AMS_EML = (
    _ONEDRIVE_DOCS
    / "Agentic Managed Services- What It Is, Where We Are, and Where We Are Headed 3[65].eml"
)


def _build_test_pdf_with_stream(pages_spec: list[dict]) -> bytes:
    """Build a multi-page PDF with raw text streams and optional images."""
    writer = pypdf.PdfWriter()

    for spec in pages_spec:
        page = writer.add_blank_page(width=612, height=792)
        text_lines = spec.get("lines", [])
        has_image = spec.get("has_image", False)
        resources = DictionaryObject()

        if text_lines:
            font_obj = DictionaryObject({
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            })
            font_ref = writer._add_object(font_obj)
            resources[NameObject("/Font")] = DictionaryObject({NameObject("/F1"): font_ref})

            stream_ops = ["BT /F1 12 Tf 72 712 Td"]
            for idx, line in enumerate(text_lines):
                if idx == 0:
                    stream_ops.append(f"({line}) Tj")
                else:
                    stream_ops.append(f"0 -20 Td ({line}) Tj")
            stream_ops.append("ET")
            stream_bytes = " ".join(stream_ops).encode("latin1", errors="replace")

            t_stream = DecodedStreamObject()
            t_stream.set_data(stream_bytes)
            page[NameObject("/Contents")] = writer._add_object(t_stream)

        if has_image:
            img_obj = DecodedStreamObject()
            img_obj.set_data(b"\xff\x00\x00" * 16)
            img_obj.update({
                NameObject("/Type"): NameObject("/XObject"),
                NameObject("/Subtype"): NameObject("/Image"),
                NameObject("/Width"): NumberObject(4),
                NameObject("/Height"): NumberObject(4),
                NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
                NameObject("/BitsPerComponent"): NumberObject(8),
            })
            img_ref = writer._add_object(img_obj)
            resources[NameObject("/XObject")] = DictionaryObject({NameObject("/Im1"): img_ref})

        page[NameObject("/Resources")] = resources

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class TestSlideDeckPreprocessingE2E(unittest.TestCase):
    """End-to-end verification of slide deck preprocessing and lexical hardening."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory(prefix="e2e_slide_test_")
        self.tmppath = Path(self.tmp_dir.name)
        self.sandbox_out = self.tmppath / "output"
        self.sandbox_out.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.sandbox_out / "conversion_cache"

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_pdf_kerning_repair_and_line_preservation_e2e(self) -> None:
        """Verify wide-kerning headers are repaired and line structures preserved."""
        pdf_bytes = _build_test_pdf_with_stream([
            {
                "lines": [
                    "T H E  B U S I N E S S  O P P O R T U N I T Y",
                    "MA N A G E D  S E R VI C E S  FOR  E N T E R P R I S E",
                    "Key strategic pillars:",
                    "- First generation AI agents",
                    "- Human governance controls",
                    "PartnerAdvisory. All Rights Reserved. Proprietary and Confidential. 2026",
                ],
                "has_image": False,
            },
            {
                "lines": [
                    "SL A L O M  A G E N T I C  P L A T F O R M S",
                    "P R I C I N G  S T R U C T U R E  2 0 2 6",
                    "Core architectural modules:",
                    "1. Decision Intelligence Engine",
                    "2. Knowledge Graph Store",
                    "All Rights Reserved. Proprietary and Confidential.",
                ],
                "has_image": False,
            },
        ])
        pdf_file = self.tmppath / "strategic_deck.pdf"
        pdf_file.write_bytes(pdf_bytes)

        meta = {
            "project": "ams_strategy",
            "target_output_dir": str(self.sandbox_out),
            "conversion_cache_dir": str(self.cache_dir),
        }
        text, sha256_digest, fmt = load_document_content(pdf_file, metadata=meta)

        self.assertEqual(fmt, "pdf")
        self.assertIn("THE BUSINESS OPPORTUNITY", text)
        self.assertIn("MANAGED SERVICES FOR ENTERPRISE", text)
        self.assertIn("partner_advisory AGENTIC PLATFORMS", text)
        self.assertIn("PRICING STRUCTURE 2026", text)

        self.assertNotIn("All Rights Reserved", text)
        self.assertNotIn("Proprietary and Confidential", text)

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        self.assertGreaterEqual(len(lines), 8, "Must preserve multiline block structure")

    def test_slide15_visual_escalation_bypass_fix_e2e(self) -> None:
        """Verify Slide 15 pattern (copyright symbol + diagram image) triggers ESCALATE_VISUAL."""
        pdf_bytes = _build_test_pdf_with_stream([
            {
                "lines": ["Slide 1 Title", "Substantive text content"],
                "has_image": False,
            },
            {
                "lines": ["\xa9"],
                "has_image": True,
            },
            {
                "lines": ["Slide 3 Title", "Conclusion and summary metrics"],
                "has_image": False,
            },
        ])

        report = inspect_pdf_pages(pdf_bytes)
        self.assertEqual(report.total_pages, 3)
        self.assertEqual(report.text_native_pages, [1, 3])
        self.assertEqual(report.escalated_pages, [2])

        p2 = report.pages[1]
        self.assertEqual(p2.disposition, "ESCALATE_VISUAL")
        self.assertEqual(p2.char_count, 0)

        with self.assertRaises(ValueError) as ctx:
            extract_pdf_pages(pdf_bytes)
        self.assertIn("visual page with no extractable text", str(ctx.exception))

    def test_eml_clean_plain_text_and_boilerplate_stripping_e2e(self) -> None:
        """Verify EML extraction selects clean plain text and strips tracking footers."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Agentic Managed Services Architecture Update"
        msg["From"] = "lead@consulting.example.com"
        msg["To"] = "team@consulting.example.com"
        msg["Date"] = "Mon, 12 Oct 2026 10:00:00 -0400"

        plain_content = (
            "# Agentic Managed Services Overview\n\n"
            "Key architectural capabilities for 2026:\n"
            "* Autonomous triage agents\n"
            "* Dialectical evaluation loops\n"
            "* Canonical knowledge graph persistence\n\n"
            "Update your email preferences or unsubscribe: <https://emltrk.com/unsub>\n"
            "PartnerAdvisory processes your data in accordance with our Privacy Policy.\n"
        )
        html_content = (
            "<html><body>"
            "<table width='100%'><tr><td>"
            "<p>Agentic Managed Services Overview</p>"
            "<p><a href='http://internal.consulting.example.com/email-preference-center'>Preferences</a></p>"
            "</td></tr></table>"
            "</body></html>"
        )
        msg.attach(MIMEText(plain_content, "plain"))
        msg.attach(MIMEText(html_content, "html"))

        eml_file = self.tmppath / "ams_announcement.eml"
        eml_file.write_bytes(msg.as_bytes())

        meta = {
            "project": "ams_announcement",
            "target_output_dir": str(self.sandbox_out),
            "conversion_cache_dir": str(self.cache_dir),
        }
        text, sha256_digest, fmt = load_document_content(eml_file, metadata=meta)

        self.assertEqual(fmt, "eml")
        self.assertIn("# Agentic Managed Services Overview", text)
        self.assertIn("* Autonomous triage agents", text)
        self.assertIn("* Dialectical evaluation loops", text)

        self.assertNotIn("emltrk.com", text)
        self.assertNotIn("email-preference-center", text)
        self.assertNotIn("Update your email preferences", text)
        self.assertNotIn("Privacy Policy", text)

    def test_conversion_cache_invalidation_and_hit_e2e(self) -> None:
        """Verify cache miss on first load, cache hit on second load with matching source units."""
        pdf_bytes = _build_test_pdf_with_stream([
            {
                "lines": [
                    "T H E  B U S I N E S S  O P P O R T U N I T Y",
                    "Recurring revenue and enterprise transformation.",
                ],
                "has_image": False,
            }
        ])
        pdf_file = self.tmppath / "cached_deck.pdf"
        pdf_file.write_bytes(pdf_bytes)

        meta1 = {
            "project": "caching_test",
            "target_output_dir": str(self.sandbox_out),
            "conversion_cache_dir": str(self.cache_dir),
        }
        text1, sha1, fmt1 = load_document_content(pdf_file, metadata=meta1)
        self.assertFalse(meta1["conversion_artifact"]["was_cached"])
        cache_key = meta1["conversion_artifact"]["cache_key"]

        cached_md = self.cache_dir / f"{cache_key}.md"
        cached_json = self.cache_dir / f"{cache_key}.json"
        self.assertTrue(cached_md.is_file(), "Cached markdown must exist")
        self.assertTrue(cached_json.is_file(), "Cached manifest must exist")

        manifest = json.loads(cached_json.read_text(encoding="utf-8"))
        self.assertIn("v1.1.0", manifest.get("converter_version", ""))

        meta2 = {
            "project": "caching_test",
            "target_output_dir": str(self.sandbox_out),
            "conversion_cache_dir": str(self.cache_dir),
        }
        text2, sha2, fmt2 = load_document_content(pdf_file, metadata=meta2)
        self.assertTrue(meta2["conversion_artifact"]["was_cached"])
        self.assertEqual(text1, text2)
        self.assertEqual(sha1, sha2)
        self.assertEqual(meta1["source_units"], meta2["source_units"])

    @unittest.skipUnless(REAL_AMS_PDF.exists(), "Real AMS 101 PDF deck not available on host")
    def test_real_ams_25_slide_deck_e2e(self) -> None:
        """Verify real 25-slide AMS deck extracts clean headers, strips footers, and escalates Slide 15."""
        pdf_bytes = REAL_AMS_PDF.read_bytes()
        report = inspect_pdf_pages(pdf_bytes)

        self.assertEqual(report.total_pages, 25)
        self.assertIn(15, report.escalated_pages, "Slide 15 must be flagged for visual escalation")

        p4_text = report.pages[3].text
        self.assertIn("THE BUSINESS OPPORTUNITY", p4_text)

        p5_text = report.pages[4].text
        self.assertIn("AGENTIC MANAGED SERVICES", p5_text)

        for p in report.pages:
            self.assertNotIn("PartnerAdvisory. All Rights Reserved. Proprietary and Confidential.", p.text)
            self.assertNotIn("All Rights Reserved. Proprietary and Confidential.", p.text)

    @unittest.skipUnless(REAL_AMS_EML.exists(), "Real AMS leadership announcement EML not available on host")
    def test_real_ams_eml_e2e(self) -> None:
        """Verify real AMS EML extracts substantive text without marketing layout tables."""
        meta = {
            "project": "real_ams_eml",
            "target_output_dir": str(self.sandbox_out),
            "conversion_cache_dir": str(self.cache_dir),
        }
        text, sha256_digest, fmt = load_document_content(REAL_AMS_EML, metadata=meta)
        self.assertEqual(fmt, "eml")
        self.assertIn("Agentic Managed Services", text)
        self.assertNotIn("emltrk.com", text)

    def test_structural_index_generation_and_prompt_debloating_e2e(self) -> None:
        """Verify structural index generation and >= 25% prompt metadata context debloating."""
        pages = []
        for i in range(1, 26):
            pages.append({
                "lines": [
                    f"Slide {i} Heading Section",
                    f"Substantive content for slide {i} with key metrics: ${i*10}k budget and {i}% growth",
                ],
                "has_image": (i == 15),
            })
        pdf_bytes = _build_test_pdf_with_stream(pages)
        pdf_file = self.tmppath / "25_slide_deck.pdf"
        pdf_file.write_bytes(pdf_bytes)

        meta = {
            "project": "debloat_test",
            "target_output_dir": str(self.sandbox_out),
            "conversion_cache_dir": str(self.cache_dir),
            "5_agent_intelligence_report": {"bulky_agent_trace": "X" * 2000},
            "visual_extraction": {"components": ["comp" * 50] * 20},
        }
        text, _, fmt = load_document_content(pdf_file, metadata=meta)
        self.assertEqual(fmt, "pdf")

        # Verify structural index is populated and spans early, middle, and late slides
        struct_index = meta.get("document_structural_index", "")
        self.assertTrue(struct_index, "document_structural_index must be generated")
        self.assertIn("| p1 |", struct_index)
        self.assertIn("| p10 |", struct_index)
        self.assertIn("| p20 |", struct_index)
        self.assertIn("| p25 |", struct_index)

        # Build prompt user content
        raw_meta_dump = json.dumps(meta, indent=2, default=str)
        user_content = _build_document_user_content(text, meta)

        # Verify bulky duplicate arrays are excluded from <document_metadata>
        self.assertNotIn("source_units", user_content.split("<document_metadata>")[1].split("</document_metadata>")[0])
        self.assertNotIn("5_agent_intelligence_report", user_content.split("<document_metadata>")[1].split("</document_metadata>")[0])
        self.assertNotIn("visual_extraction", user_content.split("<document_metadata>")[1].split("</document_metadata>")[0])

        # Verify compact <document_structural_index> is injected
        self.assertIn("<document_structural_index>", user_content)
        self.assertIn("| p1 |", user_content)
        self.assertIn("| p25 |", user_content)

        # Verify context reduction is >= 25% compared to dumping raw metadata
        clean_meta_len = len(user_content.split("<document_metadata>")[1].split("</document_metadata>")[0])
        reduction_pct = (len(raw_meta_dump) - clean_meta_len) / len(raw_meta_dump) * 100.0
        self.assertGreaterEqual(reduction_pct, 25.0, f"Context reduction must be >= 25%, observed {reduction_pct:.1f}%")

    def test_early_middle_late_locator_coverage_and_provenance_e2e(self) -> None:
        """Verify extracted signal evidence covers early, middle, and late slides with valid locators."""
        pages = []
        for i in range(1, 26):
            if i == 4:
                lines = ["THE BUSINESS OPPORTUNITY", "A $400B enterprise managed services market."]
            elif i == 12:
                lines = ["PLATFORM RUN OPERATIONS", "Continuous 99.9% uptime SLA guarantee."]
            elif i == 22:
                lines = ["MULTI-YEAR CONTRACT ROADMAP", "$12.5M multi-year contract commitments."]
            else:
                lines = [f"Slide {i} Content", "Standard operational overview details."]
            pages.append({"lines": lines, "has_image": False})

        pdf_bytes = _build_test_pdf_with_stream(pages)
        pdf_file = self.tmppath / "locator_coverage_deck.pdf"
        pdf_file.write_bytes(pdf_bytes)

        meta = {"project": "locator_coverage"}
        text, _, _ = load_document_content(pdf_file, metadata=meta)

        # Construct projection citing early (p4), middle (p12), and late (p22) slides
        projection = {
            "evidence_index": {
                "EV_EARLY": {"locator": "p4", "exact_text": "THE BUSINESS OPPORTUNITY"},
                "EV_MID": {"locator": "p12", "exact_text": "PLATFORM RUN OPERATIONS"},
                "EV_LATE": {"locator": "p22", "exact_text": "MULTI-YEAR CONTRACT ROADMAP"},
            },
            "graph_mutations": {
                "relationship_signals": [
                    {
                        "signal_id": "SIG_EARLY",
                        "evidence_ids": ["EV_EARLY"],
                        "description": "Early slide opportunity",
                    },
                    {
                        "signal_id": "SIG_MID",
                        "evidence_ids": ["EV_MID"],
                        "description": "Middle slide platform SLA",
                    },
                    {
                        "signal_id": "SIG_LATE",
                        "evidence_ids": ["EV_LATE"],
                        "description": "Late slide multi-year commitments",
                    },
                ]
            },
        }

        # Verify all locators match grammar
        for ev in projection["evidence_index"].values():
            loc = ev["locator"]
            self.assertIsNotNone(_ATTACHMENT_LOCATOR_RE.match(loc), f"Locator {loc} must match grammar")

        # Verify early (1-8), middle (9-17), and late (18-25) coverage
        cited_pages = [int(ev["locator"].lstrip("p")) for ev in projection["evidence_index"].values()]
        has_early = any(1 <= p <= 8 for p in cited_pages)
        has_mid = any(9 <= p <= 17 for p in cited_pages)
        has_late = any(18 <= p <= 25 for p in cited_pages)
        self.assertTrue(has_early and has_mid and has_late, "Evidence must span early, middle, and late slides")

        # Verify 100% Gate 3 verbatim grounding
        is_grounded, errs = validate_signal_claim_grounding(projection, text)
        self.assertTrue(is_grounded, f"Gate 3 grounding must pass; errors: {errs}")
        self.assertEqual(len(errs), 0)

    def test_binary_document_prepass_safety_e2e(self) -> None:
        """Verify binary documents do not trigger UnicodeDecodeError during pre-pass inspection."""
        pdf_bytes = _build_test_pdf_with_stream([
            {"lines": ["Binary PDF Pre-Pass Test", "Substantive document content for 5-agent pre-pass."], "has_image": False}
        ])
        pdf_file = self.tmppath / "binary_prepass.pdf"
        pdf_file.write_bytes(pdf_bytes)

        # load_document_content safely loads binary file and preserves metadata
        meta = {"title": "agile delivery roles test", "enable_5_agent_intelligence": False}
        text, sha256_digest, fmt = load_document_content(pdf_file, metadata=meta)
        self.assertEqual(fmt, "pdf")
        self.assertIn("Binary PDF Pre-Pass Test", text)


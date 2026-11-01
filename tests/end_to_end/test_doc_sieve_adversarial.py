"""
Adversarial and edge-case tests for Wave 1: Document Archetype Classifier,
12-Class Unit Sieve, and PDF Visual Occlusion Purge.
Covers:
1. Boundary payloads (0-byte PDF, blank slides, empty strings).
2. Malformed inputs (overlapping/corrupted PDF text streams, broken kerning).
3. Invariant bypass attempts (copyright year 2026 injection on break/dinner/divider slides,
   hidden master-slide title spoofing, fictional demo bypass attempts).
"""
from __future__ import annotations

import unittest

from dual_pipeline.document_pipeline_core.archetype_classifier import (
    DocumentArchetype,
    classify_document_archetype,
)
from dual_pipeline.document_pipeline_core.unit_decomposition import (
    QUARANTINED_UNIT_CLASSES,
    UnitDispositionClass,
    classify_unit_sieve,
    extract_slide_and_section_units,
    partition_units_into_batches,
)
from dual_pipeline.pdf_source import (
    inspect_pdf_pages,
    normalize_pdf_page_content,
    purge_occluded_ghost_layers,
)


class TestDocSieveAdversarial(unittest.TestCase):
    """Adversarial edge-case verification for Stage L1/L1.5 document sieve."""

    def test_boundary_payloads_empty_and_zero_byte_inputs(self) -> None:
        # 0-byte PDF must fail closed with ValueError
        with self.assertRaises(ValueError):
            inspect_pdf_pages(b"")

        # Empty markdown / text yields empty unit list and default fallback archetype
        self.assertEqual(extract_slide_and_section_units("", "emptyhash", "md"), [])
        self.assertEqual(
            classify_document_archetype(""),
            DocumentArchetype.AUTHORITATIVE_MEMO_OR_SPEC,
        )

        # Blank slide with only whitespace and copyright footer must quarantine as Q7
        cls, mat, is_sub = classify_unit_sieve(
            "Slide 10",
            "   \n© 2026 PartnerAdvisory. All Rights Reserved. Proprietary and Confidential. 10\n  ",
        )
        self.assertEqual(cls, UnitDispositionClass.Q7_QUARANTINE_LOGISTICS_DIVIDER_SOCIAL.value)
        self.assertFalse(is_sub)

    def test_malformed_inputs_overlapping_streams_and_kerning(self) -> None:
        # Overlapping kerning + occluded master header + NUL/BOM characters
        corrupted_stream = (
            "\ufeff\x00RF P RESP ONSE AUG 20 26 TRM Lakehouse Implementation on Databricks\n"
            "M ANAG ED SE RVIC ES Infrastructure Automation Transformation\n"
            "© 2 0 2 6 PartnerAdvisory. All Rights Reserved. 14"
        )
        cleaned = normalize_pdf_page_content(corrupted_stream)
        self.assertNotIn("TRM Lakehouse", cleaned)
        self.assertIn("MANAGED SERVICES", cleaned)
        self.assertIn("Infrastructure Automation Transformation", cleaned)

        # Malformed PDF header raises deterministic ValueError
        with self.assertRaises(ValueError):
            inspect_pdf_pages(b"NOT_A_PDF_HEADER_PAYLOAD")

    def test_template_appendix_propagation_without_explicit_banner(self) -> None:
        # Once a 'READ ME THEN DELETE' slide appears, subsequent template slides without
        # client-specific Apex Global metrics must quarantine as Q1 even if they omit the banner.
        deck_md = (
            "## Slide 1: Geo-POD: WPB, Orl, Jax\n"
            "Apex Global Enterprises | $24M Target\n\n"
            "## Slide 50: Account Planning Template — READ ME THEN DELETE\n"
            "Instructions for completing the template.\n\n"
            "## Slide 52: Questions to Consider as you Approach Account Planning\n"
            "Customer Priorities & Decision-making\n\n"
            "## Slide 62: Customer Priority Buyer Business Unit\n"
            "[Link: Solutions Catalog] Reduce cost per claim\n"
        )
        units = extract_slide_and_section_units(deck_md, "tmplprophash", "md")
        self.assertEqual(len(units), 4)
        self.assertTrue(units[0].metadata["is_substantive"])
        for u in units[1:]:
            self.assertFalse(u.metadata["is_substantive"])
            self.assertEqual(
                u.metadata["sieve_class"],
                UnitDispositionClass.Q1_QUARANTINE_TEMPLATE_SCAFFOLD.value,
            )

    def test_invariant_bypass_attempts_footer_year_and_template_spoofing(self) -> None:
        # Attempt 1: Injecting 2026 copyright footer onto a divider/break slide to trick \b20\d\d\b numeric check
        deck_md = (
            "## Slide 1: Agenda\n"
            "© 2026 PartnerAdvisory. All Rights Reserved. 2026\n\n"
            "## Slide 2: Break!\n"
            "Be back at 10:45 AM\n"
            "© 2026 PartnerAdvisory. All Rights Reserved. 2026\n\n"
            "## Slide 3: Small Group Dinners\n"
            "Matsu | Wild Ginger | Purple Cafe | Il Bistro\n"
            "© 2026 PartnerAdvisory. All Rights Reserved. 2026\n\n"
            "## Slide 4: Illustrative Experience Demo\n"
            "ILLUSTRATIVE: Maria Delgado (EV553) submits ticket with $500K savings in 2026.\n"
            "All applications, NEECodes, people, and numbers are fictional.\n\n"
            "## Slide 5: READ ME THEN DELETE\n"
            "[Account Name] 2026 Revenue Target: $50M (+25% YoY)\n\n"
            "## Slide 6: Core NEE Architecture\n"
            "- Apex Global Enterprises IT4U and Project Beacon production deployment across 97 engineers in 2026.\n"
        )
        units = extract_slide_and_section_units(deck_md, "bypasshash123", "md")
        self.assertEqual(len(units), 6)

        # Slides 1..5 must ALL be non-substantive and quarantined
        for idx in range(5):
            u = units[idx]
            self.assertFalse(
                u.metadata["is_substantive"],
                f"Slide {idx+1} ({u.metadata['title']}) bypassed quarantine!",
            )
            self.assertIn(u.metadata["disposition"], QUARANTINED_UNIT_CLASSES)

        # Slide 6 is substantive C4
        self.assertTrue(units[5].metadata["is_substantive"])
        self.assertEqual(
            units[5].metadata["disposition"],
            UnitDispositionClass.C4_CORE_ARCHITECTURE_ROADMAP_POLICY.value,
        )

        # Batch partitioner must exclude all Q1-Q7 quarantined slides
        batches = partition_units_into_batches([u.to_dict() for u in units], batch_size=8)
        self.assertEqual(len(batches), 1)
        self.assertEqual(len(batches[0]), 1)
        self.assertEqual(batches[0][0]["locator"], "slide:6")


if __name__ == "__main__":
    unittest.main()

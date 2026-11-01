"""
Wave 1 Adversarial & Edge-Case Tests for Document High-Signal Sieve and Anti-Overfitting Linter.
Covers 3+ mandatory adversarial categories:
1. Boundary payloads (empty strings, 0-byte PDFs, whitespace/footer-only slides).
2. Malformed inputs (corrupted kerning, embedded NUL/BOM bytes, unparseable syntax in linter).
3. Invariant bypass attempts (module-level hardcoded entity tuple tables in dual_pipeline/,
   vendor customer-story / EMR compliance slides attempting to masquerade as substantive C4,
   and resume bio slides attempting to promote via 'Representative Team Profiles' headers).
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from dual_pipeline.document_pipeline_core.unit_decomposition import (
    QUARANTINED_UNIT_CLASSES,
    UnitDispositionClass,
    classify_unit_sieve,
    extract_slide_and_section_units,
    partition_units_into_batches,
)
from dual_pipeline.pdf_source import inspect_pdf_pages, normalize_pdf_page_content
from tools.lint_no_transcript_overfitting import check_code_content


class TestDocumentSieveAdversarial(unittest.TestCase):
    """Adversarial verification of Wave 1 structural sieve and derivation linter."""

    def test_boundary_payloads_empty_and_footer_only_slides(self) -> None:
        with self.assertRaises(ValueError):
            inspect_pdf_pages(b"")

        self.assertEqual(extract_slide_and_section_units("", "empty", "pdf"), [])

        s_cls, mat, is_sub = classify_unit_sieve(
            "Slide 99",
            "   \n© 2026 PartnerAdvisory. All Rights Reserved. Proprietary and Confidential.\n99\n",
        )
        self.assertEqual(s_cls, UnitDispositionClass.Q7_QUARANTINE_LOGISTICS_DIVIDER_SOCIAL.value)
        self.assertEqual(mat, "STRUCTURAL_DIVIDER")
        self.assertFalse(is_sub)

    def test_malformed_inputs_kerning_and_occluded_headers(self) -> None:
        raw = (
            "\ufeff\x00RFP RESPONSE AUG 2026 TRM Lakehouse Implementation on Databricks\n"
            "M ANAG ED SE RVIC ES Infrastructure Automation Transformation\n"
        )
        cleaned = normalize_pdf_page_content(raw)
        self.assertNotIn("TRM Lakehouse", cleaned)
        self.assertIn("MANAGED SERVICES", cleaned)

        with self.assertRaises(ValueError):
            inspect_pdf_pages(b"%PDF-corrupt-header")

    def test_invariant_bypass_module_level_entity_tuple_table_blocked_by_linter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_file = Path(tmpdir) / "fake_extractor.py"
            bad_file.write_text(
                "_HARDCODED_ENTITIES = (\n"
                "    ('org:pfizer', 'Pfizer', 102),\n"
                "    ('org:exelon', 'Exelon', 108),\n"
                "    ('org:comed', 'ComEd', 108),\n"
                "    ('org:cognizant', 'Cognizant', 107),\n"
                "    ('org:infosys', 'Infosys', 107),\n"
                "    ('metric:partner_advisory-emr-rates', 'EMR', 111),\n"
                ")\n",
                encoding="utf-8",
            )
            violations = check_code_content(bad_file)
            self.assertTrue(
                any("Prohibited module-level hardcoded entity ID table" in v for v in violations),
                f"Expected module-level entity ID table violation, got: {violations}",
            )

    def test_invariant_bypass_vendor_boilerplate_and_bio_slides_excluded_from_batches(self) -> None:
        deck_md = (
            "## Slide 1: Client References\n"
            "Client References RELEVANT EXPERIENCE McDonald's Corporation Bridgestone Americas USCIS\n\n"
            "## Slide 2: Experience Modification Rate (EMR)\n"
            "5. Experience Modification Rate (EMR) for Workers' Compensation Insurance: 2025 (0.53)\n\n"
            "## Slide 3: Representative Team Profiles - Kenny Jeckell\n"
            "Kenny Jeckell Senior Director, Software Engineering Kenny is a technology leader with over 20 years of experience\n\n"
            "## Slide 4: Provisioned Cloud Estate Architecture\n"
            "Request-to-Delivery Flow: IT4U -> Backstage -> InfraCodeBase -> Terraform -> GCP / AWS\n"
        )
        units = extract_slide_and_section_units(deck_md, "advhash", "pdf")
        self.assertEqual(len(units), 4)
        self.assertIn(units[0].metadata["sieve_class"], QUARANTINED_UNIT_CLASSES)
        self.assertFalse(units[0].metadata["is_substantive"])
        self.assertIn(units[1].metadata["sieve_class"], QUARANTINED_UNIT_CLASSES)
        self.assertFalse(units[1].metadata["is_substantive"])
        self.assertEqual(
            units[2].metadata["sieve_class"],
            UnitDispositionClass.CONTEXT_NARRATIVE_OR_RECAP.value,
        )
        self.assertFalse(units[2].metadata["is_substantive"])
        self.assertTrue(units[3].metadata["is_substantive"])

        batches = partition_units_into_batches([u.to_dict() for u in units], batch_size=8)
        self.assertEqual(len(batches), 1)
        self.assertEqual([b["locator"] for b in batches[0]], ["slide:4"])


if __name__ == "__main__":
    unittest.main()

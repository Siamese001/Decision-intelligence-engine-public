"""
Wave 1 E2E Test: High-Signal Structural Sieve & Hardcoded Bypass Retirement.
Verifies:
1. On the 111-page RFP Orals PDF, classify_unit_sieve promotes 24-42 high-signal
   anchor slides to SUBSTANTIVE (is_substantive == True), while resume bios, vendor
   boilerplate/surveys/EMR, and narrative recap slides are non-substantive.
2. extract_orals_proposal_projection produces 0 resume prior employers (org:pfizer,
   org:exelon, org:comed, org:cognizant, org:infosys), 0 external reference logos/contacts
   (org:mcdonalds, org:bridgestone-americas, org:uscis, person:sandro-seminara), 0 vanity
   survey/compliance metrics (metric:partner_advisory-emr-rates, metric:energy-survey-2025,
   metric:gcp-partner-stats, metric:aws-partner-stats), and consolidates SOW deliverables
   to the 10 canonical deliverables (deliverable:a1..a4, deliverable:b1..b6).
3. tools/lint_no_transcript_overfitting.py passes with 0 violations across dual_pipeline/.
"""
from __future__ import annotations

from pathlib import Path
import unittest

from dual_pipeline.document_pipeline_core.orals_proposal_extractor import (
    extract_orals_proposal_projection,
)
from dual_pipeline.document_pipeline_core.unit_decomposition import (
    UnitDispositionClass,
    classify_unit_sieve,
    extract_slide_and_section_units,
)
from dual_pipeline.pdf_source import inspect_pdf_pages
from tools.lint_no_transcript_overfitting import run_linter


class TestDocumentHighSignalSieveE2E(unittest.TestCase):
    """End-to-end verification of Wave 1 high-signal sieve and hardcoded table retirement."""

    def test_rfp_orals_111_slide_high_signal_sieve_and_zero_noise_extraction(self) -> None:
        pdf_rel = Path("inputs/documents/NEE_Infrastructure_Automation_Transformation_RFP_Orals.pdf")
        orals_pdf = next(
            (p / pdf_rel for p in Path(__file__).resolve().parents if (p / pdf_rel).exists()),
            Path.home() / "Downloads" / "NEE Infrastructure Automation Transformation RFP_Orals.pdf",
        )
        self.assertTrue(orals_pdf.exists(), f"Missing fixture PDF: {orals_pdf}")

        rep = inspect_pdf_pages(orals_pdf.read_bytes(), title=orals_pdf.name)
        units = extract_slide_and_section_units(rep.extracted_markdown, "orals_wave1_hash", "pdf")
        self.assertEqual(len(units), 111)

        substantive_units = [u for u in units if u.metadata.get("is_substantive")]
        self.assertGreaterEqual(len(substantive_units), 24)
        self.assertLessEqual(len(substantive_units), 42)

        # Resume bio pages (96..109) and compliance EMR page (111) must be non-substantive
        for bio_page in range(96, 110):
            u_bio = units[bio_page - 1]
            self.assertFalse(
                u_bio.metadata.get("is_substantive"),
                f"Resume bio slide p{bio_page} leaked as SUBSTANTIVE",
            )
            self.assertEqual(
                u_bio.metadata.get("sieve_class"),
                UnitDispositionClass.CONTEXT_NARRATIVE_OR_RECAP.value,
            )

        u_emr = units[110]
        self.assertFalse(u_emr.metadata.get("is_substantive"))
        self.assertEqual(
            u_emr.metadata.get("sieve_class"),
            UnitDispositionClass.Q8_QUARANTINE_VENDOR_BOILERPLATE.value,
        )

        # Verify consolidated Orals projection has zero bloated/noise nodes
        proj = extract_orals_proposal_projection(
            rep.extracted_markdown,
            {"slide_units": [u.to_dict() for u in units], "page_count": 111},
        )
        muts = proj["graph_mutations"]
        entity_ids = {e["id"] for e in muts["entity_upserts"]}

        forbidden_ids = {
            "org:pfizer",
            "org:exelon",
            "org:comed",
            "org:cognizant",
            "org:infosys",
            "org:mcdonalds",
            "org:bridgestone-americas",
            "org:uscis",
            "person:sandro-seminara",
            "person:brent-gade",
            "person:denise-johnson",
            "metric:partner_advisory-emr-rates",
            "metric:energy-survey-2025",
            "metric:gcp-partner-stats",
            "metric:aws-partner-stats",
            "metric:edo-brand-premium-45pct",
            "system:apex-evolution",
            "system:ion-endur",
            "system:gemini-enterprise",
            "deliverable:a1-baseline-vsm",
            "deliverable:a1-chokepoint-quick-wins",
        }
        self.assertEqual(entity_ids & forbidden_ids, set())

        # Verify 10 consolidated SOW deliverables (A1-A4, B1-B6) exist
        expected_deliverables = {
            f"deliverable:{code}"
            for code in ("a1", "a2", "a3", "a4", "b1", "b2", "b3", "b4", "b5", "b6")
        }
        self.assertTrue(expected_deliverables.issubset(entity_ids))

    def test_anti_overfitting_linter_passes_on_dual_pipeline(self) -> None:
        passed, violations = run_linter()
        self.assertTrue(passed, f"Linter failed with violations: {violations}")


if __name__ == "__main__":
    unittest.main()

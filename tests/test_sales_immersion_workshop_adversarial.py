"""
Wave 1 Adversarial & Edge-Case Tests: PartnerAdvisory Sales Immersion Workshop Extractor & Sieve.
Covers >= 3 mandatory edge-case categories:
1. Boundary payloads (empty document text, 0-page/1-page stub deck).
2. Malformed input (missing slide_units metadata, invalid locator strings, non-matching deck).
3. Invariant bypass attempts (citing quarantined Q6/Q7 slide in evidence_index triggers
   DOCUMENT_QUARANTINED_UNIT_LEAKAGE; generic '5 Lenses' text inside PartnerAdvisory Sales Immersion
   does not misroute to 'project_lens').
"""
from __future__ import annotations

import unittest

from dual_pipeline.document_pipeline_core.metadata_helper import scaffold_document_metadata
from dual_pipeline.document_pipeline_core.quality import (
    validate_signal_claim_grounding,
    validate_slide_deck_coverage_and_yield,
)
from dual_pipeline.document_pipeline_core.unit_decomposition import (
    UnitDispositionClass,
    extract_slide_and_section_units,
)
from dual_pipeline.document_pipeline_core.workshop_extractor import (
    extract_sparse_enablement_workshop_projection,
)


class TestSalesImmersionWorkshopAdversarial(unittest.TestCase):
    """Adversarial and boundary verification for workshop extraction and router disambiguation."""

    def test_boundary_empty_and_non_matching_payloads_return_empty_dict(self) -> None:
        self.assertEqual(extract_sparse_enablement_workshop_projection("", {}), {})
        self.assertEqual(
            extract_sparse_enablement_workshop_projection("## Page 1\nUnrelated Deck", {"page_count": 1}),
            {},
        )

    def test_malformed_metadata_without_slide_units_still_extracts_valid_projection(self) -> None:
        raw_text = (
            "## Page 1\nWELCOME TO SALES IMMERSION Q4 2026 PartnerAdvisory Sales Learning\n\n"
            "## Page 56\nPartnerAdvisory Sales Methodology Stage 1 - Prospect Stage 2 - Discover"
        )
        proj = extract_sparse_enablement_workshop_projection(raw_text, {"page_count": 112})
        self.assertIn("graph_mutations", proj)
        self.assertEqual(len(proj["graph_mutations"]["entity_upserts"]), 14)
        ok, issues = validate_signal_claim_grounding(proj, raw_text)
        self.assertTrue(ok, f"Expected grounding to pass on fallback quotes, got: {issues}")

    def test_invariant_bypass_quarantined_slide_citation_rejected_by_gate_3(self) -> None:
        blocks = []
        for p in range(1, 15):
            if p == 1:
                blocks.append("## Page 1\nWELCOME TO SALES IMMERSION Q4 2026 PartnerAdvisory Sales Learning")
            elif p == 8:
                blocks.append("## Page 8\nProgram Overview Logistics Break 15 minutes Lunch")
            else:
                blocks.append(f"## Page {p}\nPartnerAdvisory Sales Methodology Stage 1 - Prospect (10%)")
        raw_text = "\n\n".join(blocks)
        units = extract_slide_and_section_units(raw_text, "adv_hash", "pdf")
        self.assertEqual(len(units), 14)
        # Force page 8 to be quarantined logistics
        units[7].metadata["is_substantive"] = False
        units[7].metadata["sieve_class"] = UnitDispositionClass.Q7_QUARANTINE_LOGISTICS_DIVIDER_SOCIAL.value

        meta = {
            "document_archetype": "SPARSE_ENABLEMENT_WORKSHOP",
            "page_count": 14,
            "slide_units": [u.to_dict() for u in units],
        }
        proj = extract_sparse_enablement_workshop_projection(raw_text, meta)
        # Tamper with evidence_index to cite quarantined slide p8
        proj["evidence_index"]["ev-tampered-q7"] = {
            "source_type": "DOCUMENT",
            "locator": "Slide 8",
            "exact_text": "Program Overview Logistics Break 15 minutes Lunch",
        }
        cov_ok, cov_issues, _ = validate_slide_deck_coverage_and_yield(
            proj, raw_text, metadata=meta, mutation_count=20
        )
        self.assertFalse(cov_ok)
        self.assertTrue(any("DOCUMENT_QUARANTINED_UNIT_LEAKAGE" in msg for msg in cov_issues))

    def test_invariant_bypass_five_lenses_keyword_does_not_hijack_internal_consulting_routing(self) -> None:
        raw_text = (
            "## Page 1\nPartnerAdvisory Sales Immersion Q4 2026\n\n"
            "## Page 58\nIntroducing the 5 Lenses: Relationship Lens, Need Lens, Solution Lens, Value Lens, Risk Lens."
        )
        meta = scaffold_document_metadata(
            input_path="media_1792834580043_94dc38ea.pdf",
            title="PartnerAdvisory Sales Immersion Q4 2026 - Day 1",
            source_text=raw_text,
            metadata={"page_count": 112},
        )
        self.assertEqual(meta["project"], "internal_consulting")
        self.assertNotEqual(meta["project"], "project_lens")


if __name__ == "__main__":
    unittest.main()

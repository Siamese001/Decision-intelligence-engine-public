"""
Wave 3 E2E Test: Materiality-Weighted Gate 3.13, Logarithmic Budget & Ontological Triage.
Verifies:
1. resolve_duration_mutation_budget returns a sub-linear logarithmic budget in [120, 210]
   (never 450) for 111-page PURSUIT_ORALS_PROPOSAL decks.
2. validate_slide_deck_coverage_and_yield passes on the consolidated Orals projection
   (substantive_coverage_ratio >= 0.90 across the 36 anchor slides) while treating
   CONTEXT_NARRATIVE_OR_RECAP and Q8_QUARANTINE_VENDOR_BOILERPLATE slides as exempt/quarantined.
3. _trim_excess_signals in graph_healer evicts unlinked nodes when edges are trimmed so
   zero degree-0 orphan nodes survive budget triage.
"""
from __future__ import annotations

from pathlib import Path
import unittest

from dual_pipeline.document_pipeline_core.orals_proposal_extractor import (
    extract_orals_proposal_projection,
)
from dual_pipeline.document_pipeline_core.quality import (
    validate_slide_deck_coverage_and_yield,
)
from dual_pipeline.document_pipeline_core.unit_decomposition import (
    extract_slide_and_section_units,
)
from dual_pipeline.pdf_source import inspect_pdf_pages
from dual_pipeline.transcript_duration_helper import resolve_duration_mutation_budget
from dual_pipeline.transcript_qa_core.graph_healer import _trim_excess_signals


class TestDocumentBudgetAndCoverageGatesE2E(unittest.TestCase):
    """End-to-end verification of Wave 3 logarithmic budget, Gate 3.13, and ontological triage."""

    def test_logarithmic_document_budget_ceiling_never_450(self) -> None:
        meta = {
            "is_document": True,
            "document_archetype": "PURSUIT_ORALS_PROPOSAL",
            "_source_coverage": {"pages_count": 111},
        }
        budget = resolve_duration_mutation_budget(metadata=meta, text="## Page 1\n- Item\n" * 111)
        self.assertGreaterEqual(budget, 120)
        self.assertLessEqual(budget, 210)
        self.assertNotEqual(budget, 450)

    def test_gate_3_13_passes_on_consolidated_orals_without_synthetic_padding(self) -> None:
        pdf_rel = Path("inputs/documents/NEE_Infrastructure_Automation_Transformation_RFP_Orals.pdf")
        orals_pdf = next(
            (p / pdf_rel for p in Path(__file__).resolve().parents if (p / pdf_rel).exists()),
            Path.home() / "Downloads" / "NEE Infrastructure Automation Transformation RFP_Orals.pdf",
        )
        self.assertTrue(orals_pdf.exists())
        rep = inspect_pdf_pages(orals_pdf.read_bytes(), title=orals_pdf.name)
        units = [u.to_dict() for u in extract_slide_and_section_units(rep.extracted_markdown, "orals", "pdf")]
        meta = {"slide_units": units, "page_count": 111, "document_archetype": "PURSUIT_ORALS_PROPOSAL"}

        proj = extract_orals_proposal_projection(rep.extracted_markdown, meta)
        muts = proj["graph_mutations"]
        total_muts = len(muts["entity_upserts"]) + len(muts["relationship_signals"])

        passed, errors, tel = validate_slide_deck_coverage_and_yield(
            proj,
            rep.extracted_markdown,
            metadata=meta,
            mutation_count=total_muts,
        )
        self.assertTrue(passed, f"Expected Gate 3.13 to pass, got errors: {errors}")
        self.assertGreaterEqual(tel["substantive_coverage_ratio"], 0.90)
        self.assertGreaterEqual(len(tel["exempt_structural_units"]), 65)

    def test_ontological_triage_evicts_unlinked_nodes_on_signal_trim(self) -> None:
        entities = [
            {"id": "system:it4u", "type": "system", "name": "IT4U", "evidence_ids": ["ev-1"]},
            {"id": "system:backstage", "type": "system", "name": "Backstage", "evidence_ids": ["ev-1"]},
            {"id": "system:github", "type": "system", "name": "GitHub", "evidence_ids": ["ev-1"]},
            {"id": "system:terraform", "type": "system", "name": "Terraform", "evidence_ids": ["ev-1"]},
            {"id": "system:gcp", "type": "system", "name": "GCP", "evidence_ids": ["ev-1"]},
            {"id": "role:orphan-role", "type": "role", "name": "Orphan Role", "evidence_ids": ["ev-2"]},
            {"id": "metric:vanity-emr", "type": "metric", "name": "EMR 0.53", "evidence_ids": ["ev-3"]},
        ]
        signals = [
            {"signal_id": "s1", "from_id": "system:it4u", "to_id": "system:backstage", "relationship": "ROUTES_TO", "evidence_ids": ["ev-1"]},
            {"signal_id": "s2", "from_id": "system:backstage", "to_id": "system:github", "relationship": "TRIGGERS", "evidence_ids": ["ev-1"]},
            {"signal_id": "s3", "from_id": "system:github", "to_id": "system:terraform", "relationship": "EXECUTES", "evidence_ids": ["ev-1"]},
            {"signal_id": "s4", "from_id": "system:terraform", "to_id": "system:gcp", "relationship": "DEPLOYS_TO", "evidence_ids": ["ev-1"]},
            {"signal_id": "s5", "from_id": "role:orphan-role", "to_id": "metric:vanity-emr", "relationship": "HAS_METRIC", "evidence_ids": ["ev-2"]},
        ]
        data = {
            "evidence_index": {
                "ev-1": {"exact_text": "IT4U Backstage GitHub Terraform GCP"},
                "ev-2": {"exact_text": "Orphan Role"},
                "ev-3": {"exact_text": "EMR 0.53"},
            },
            "evidence_only_statement_ids": [],
        }
        trimmed = _trim_excess_signals(data, entities, signals, static_count=0, max_budget=11, is_doc=True)
        self.assertGreaterEqual(trimmed, 1)
        surviving_ids = {e["id"] for e in entities}
        connected_ids = {s["from_id"] for s in signals} | {s["to_id"] for s in signals}
        self.assertTrue(surviving_ids.issubset(connected_ids), f"Unlinked orphans survived: {surviving_ids - connected_ids}")


if __name__ == "__main__":
    unittest.main()

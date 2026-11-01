"""
tests/end_to_end/test_wave2_document_verbatim_grounding_e2e.py

Wave 2 End-to-End Test Suite: Verbatim Document Grounding and Overfitting Prevention.
Verifies that:
1. Every entity upsert and relationship signal cites text found word-for-word in the source PDF.
2. Grounding verification passes on two distinct real documents:
   - Apex Global Q1 2026 Earnings Script (.corpus/documents/apex_global_earnings_script.pdf)
   - HR360 Position Request Form (documents/hrit/HR360_PRF_Current_vs_Future_State.pdf)
3. Each cited text span is printed alongside its exact character offset in the source text.
4. Fabricated / hallucinated text citations fail closed with an explicit assertion error.
5. Healer and semantic linters maintain zero hardcoded company or meeting exemptions.
"""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List, Tuple

from dual_pipeline.core import ROOT
from dual_pipeline.document_pipeline_core.executor import _post_process_document_projection
from dual_pipeline.pdf_source import extract_pdf_pages, normalize_pdf_text
from dual_pipeline.transcript_qa_core.market_intel_healer import heal_market_intel_mutations


def verify_verbatim_grounding(
    projection: Dict[str, Any],
    source_text: str,
) -> List[Tuple[str, int, str]]:
    """
    Verify that every evidence item cited by entities and signals exists
    word-for-word in the source text.

    Returns a list of (item_id, offset, span) tuples.
    Raises AssertionError if any cited span is missing from source_text.
    """
    norm_source = normalize_pdf_text(source_text)
    evidence_index = projection.get("evidence_index", {})
    mutations = projection.get("graph_mutations", {})
    
    # Collect all evidence IDs cited by entities and signals
    cited_eids: set[str] = set()
    for ent in mutations.get("entity_upserts", []):
        for eid in ent.get("evidence_ids", []):
            cited_eids.add(eid)
    for sig in mutations.get("relationship_signals", []):
        for eid in sig.get("evidence_ids", []):
            cited_eids.add(eid)

    # If mutations have no explicit evidence IDs, check all evidence items
    if not cited_eids:
        cited_eids = set(evidence_index.keys())

    results: List[Tuple[str, int, str]] = []

    for eid in sorted(cited_eids):
        ev = evidence_index.get(eid)
        assert ev is not None, f"Cited evidence ID '{eid}' missing from evidence_index"
        
        span = ev.get("exact_text") or ev.get("text") or ""
        assert span, f"Evidence item '{eid}' has no text or exact_text"

        # Handle quotes with ellipses: each fragment must be verbatim in the source
        fragments = [f.strip() for f in span.split("...") if f.strip()]
        for frag in fragments:
            norm_frag = normalize_pdf_text(frag)
            offset = norm_source.find(norm_frag)
            results.append((eid, offset, norm_frag))
            
            # Print each cited span next to its source offset
            print(f"  [{eid}] (offset {offset:5d}): {norm_frag[:75]}...")
            
            assert offset != -1, (
                f"Verbatim grounding violation: cited text for '{eid}' not found in source:\n"
                f"  Missing: {norm_frag}\n"
                f"  Source length: {len(norm_source)} characters"
            )

    return results


class TestWave2DocumentVerbatimGroundingE2E(unittest.TestCase):
    """Wave 2 verification across two distinct source PDFs."""

    def test_apex_global_earnings_pdf_grounding(self) -> None:
        """Verify verbatim grounding on Apex Global Q1 2026 Earnings Script PDF."""
        pdf_path = ROOT / ".corpus" / "documents" / "apex_global_earnings_script.pdf"
        self.assertTrue(pdf_path.exists(), f"Missing PDF fixture: {pdf_path}")

        raw_pages = extract_pdf_pages(pdf_path.read_bytes())
        self.assertGreater(len(raw_pages), 0, "PDF extraction returned zero pages")
        source_text = "\n\n".join(raw_pages)

        # Authentic signal projection for Apex Global earnings conference call
        projection_payload = {
            "evidence_index": {
                "ev-01": {
                    "text": "Good morning, everyone, and thank you for joining our first-quarter 2026 financial results conference call for Apex Global Enterprises.",
                },
                "ev-02": {
                    "text": "with Basin Electric to develop a 1.5-gigawatt combined-cycle plant in North Dakota is a great example.",
                },
                "ev-03": {
                    "text": "terms and conditions with appropriate risk sharing mechanisms that limit our ultimate exposure.",
                },
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "org:apex_global_energy",
                        "type": "organization",
                        "name": "Apex Global Enterprises",
                        "evidence_ids": ["ev-01"],
                    },
                    {
                        "id": "org:basin_electric",
                        "type": "organization",
                        "name": "Basin Electric",
                        "evidence_ids": ["ev-02"],
                    },
                ],
                "relationship_signals": [
                    {
                        "signal_id": "sig-apex_global-basin-electric-develops",
                        "from_id": "org:apex_global_energy",
                        "to_id": "org:basin_electric",
                        "predicate": "DEVELOPS",
                        "evidence_ids": ["ev-02"],
                    },
                    {
                        "signal_id": "sig-apex_global-risk-sharing",
                        "from_id": "org:apex_global_energy",
                        "to_id": "org:apex_global_energy",
                        "predicate": "LIMITS_EXPOSURE",
                        "evidence_ids": ["ev-03"],
                    },
                ],
            },
        }

        metadata = {
            "project": "project_atlas",
            "is_document": True,
            "title": "Apex Global Q1 2026 Earnings Script",
            "date": "04-20-2026",
        }

        # Process through document post-processor
        healed_json = _post_process_document_projection(
            json.dumps(projection_payload),
            metadata=metadata,
            document_text=source_text,
        )
        healed_data = json.loads(healed_json)

        print("\nVerifying Apex Global Earnings PDF Grounding:")
        grounded_spans = verify_verbatim_grounding(healed_data, source_text)
        self.assertEqual(len(grounded_spans), 3)
        for _, offset, _ in grounded_spans:
            self.assertGreaterEqual(offset, 0)

    def test_hr360_prf_pdf_grounding(self) -> None:
        """Verify verbatim grounding on a different document: HR360 PRF Process PDF."""
        pdf_path = ROOT / "documents" / "hrit" / "HR360_PRF_Current_vs_Future_State.pdf"
        self.assertTrue(pdf_path.exists(), f"Missing PDF fixture: {pdf_path}")

        raw_pages = extract_pdf_pages(pdf_path.read_bytes())
        self.assertEqual(len(raw_pages), 4, "Expected 4 pages for HR360 PRF document")
        source_text = "\n\n".join(raw_pages)

        # Authentic signal projection for HR360 process documentation
        projection_payload = {
            "evidence_index": {
                "ev-01": {
                    "text": "Working process documentation for PartnerAdvisory scoping",
                },
                "ev-02": {
                    "text": "Current State — Records First, Approval Later",
                },
                "ev-03": {
                    "text": "Future State — Approval First, Automation Second",
                },
                "ev-04": {
                    "text": "HR360 manages the business request and approval before SuccessFactors creates or updates official records.",
                },
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "system:hr360",
                        "type": "system",
                        "name": "HR360",
                        "evidence_ids": ["ev-04"],
                    },
                    {
                        "id": "system:successfactors",
                        "type": "system",
                        "name": "SuccessFactors",
                        "evidence_ids": ["ev-04"],
                    },
                ],
                "relationship_signals": [
                    {
                        "signal_id": "sig-current-state-process",
                        "from_id": "system:successfactors",
                        "to_id": "system:successfactors",
                        "predicate": "RECORDS_FIRST",
                        "evidence_ids": ["ev-02"],
                    },
                    {
                        "signal_id": "sig-hr360-approval-workflow",
                        "from_id": "system:hr360",
                        "to_id": "system:successfactors",
                        "predicate": "MANAGES",
                        "evidence_ids": ["ev-03", "ev-04"],
                    },
                    {
                        "signal_id": "sig-partner_advisory-scoping",
                        "from_id": "org:partner_advisory",
                        "to_id": "system:hr360",
                        "predicate": "SCOPES",
                        "evidence_ids": ["ev-01"],
                    },
                ],
            },
        }

        metadata = {
            "project": "apex_trans",
            "is_document": True,
            "title": "HR360 PRF Current vs Future State",
            "date": "10-07-2026",
        }

        # Process through document post-processor
        healed_json = _post_process_document_projection(
            json.dumps(projection_payload),
            metadata=metadata,
            document_text=source_text,
        )
        healed_data = json.loads(healed_json)

        print("\nVerifying HR360 PRF PDF Grounding:")
        grounded_spans = verify_verbatim_grounding(healed_data, source_text)
        self.assertEqual(len(grounded_spans), 4)
        for _, offset, _ in grounded_spans:
            self.assertGreaterEqual(offset, 0)

    def test_adversarial_fabricated_claim_fails_grounding(self) -> None:
        """Adversarial test: fabricated quotes not present in source must fail closed."""
        pdf_path = ROOT / "documents" / "hrit" / "HR360_PRF_Current_vs_Future_State.pdf"
        source_text = "\n\n".join(extract_pdf_pages(pdf_path.read_bytes()))

        fabricated_projection = {
            "evidence_index": {
                "ev-fake": {
                    "text": "Apex Global Enterprises launched 500 orbital quantum communication satellites over Mars.",
                },
            },
            "graph_mutations": {
                "relationship_signals": [
                    {
                        "signal_id": "sig-fake",
                        "predicate": "LAUNCHED",
                        "evidence_ids": ["ev-fake"],
                    }
                ]
            },
        }

        with self.assertRaises(AssertionError) as ctx:
            verify_verbatim_grounding(fabricated_projection, source_text)

        self.assertIn("Verbatim grounding violation", str(ctx.exception))
        self.assertIn("ev-fake", str(ctx.exception))

    def test_healer_zero_overfitting(self) -> None:
        """Ensure healer applies only document-agnostic cleanup without hardcoded entities or decisions."""
        sample_data = {
            "meeting_id": "document-20261006-apex_global-earnings",
            "evidence_index": {
                "ev-01": {"text": "Transformers procurement lead times are 75 weeks."},
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "org:generic_corp", "name": "Generic Corp", "description": ""},
                ],
                "relationship_signals": [
                    {"signal_id": "sig-01", "relationship": "develops-solution"},
                ],
                "decision_updates": [],
            },
        }

        changes: List[str] = []
        heal_market_intel_mutations(sample_data, {"is_document": True}, changes)

        # Verify description was populated agnostically
        ent = sample_data["graph_mutations"]["entity_upserts"][0]
        self.assertEqual(ent["description"], "Generic Corp referenced in source document.")

        # Verify predicate was normalized agnostically
        sig = sample_data["graph_mutations"]["relationship_signals"][0]
        self.assertEqual(sig["relationship"], "DEVELOPS_SOLUTION")

        # Verify NO hardcoded advance procurement decision was injected
        decisions = sample_data["graph_mutations"]["decision_updates"]
        self.assertEqual(len(decisions), 0, "Overfitting detected: hardcoded decision was injected")

    def test_anti_overfitting_linter_clean(self) -> None:
        """Verify that the anti-overfitting linter reports 0 violations."""
        res = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "lint_no_transcript_overfitting.py")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Overfitting linter failed:\n{res.stdout}\n{res.stderr}")
        self.assertIn("0 meeting-specific files", res.stdout)


if __name__ == "__main__":
    unittest.main()

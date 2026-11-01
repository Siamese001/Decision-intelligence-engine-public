"""Unit tests for Domain Intelligence Data Models and Taxonomy Config."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools.pr_governance_core.domain_models import (
    ConfidenceTier,
    DomainArchetype,
    DomainClassificationEnvelope,
    ModalityScope,
)

ROOT = Path(__file__).resolve().parents[1]


class TestDomainModels(unittest.TestCase):
    def test_modality_scope_enum(self):
        self.assertEqual(ModalityScope.DOCUMENT_WRITTEN.value, "DOCUMENT_WRITTEN")
        self.assertEqual(ModalityScope.MEETING_SPOKEN.value, "MEETING_SPOKEN")
        self.assertEqual(ModalityScope.CODE_ARCHITECTURE.value, "CODE_ARCHITECTURE")
        self.assertEqual(ModalityScope.CODE_GOVERNANCE.value, "CODE_GOVERNANCE")
        self.assertEqual(ModalityScope.CODE_PLATFORM.value, "CODE_PLATFORM")
        self.assertEqual(ModalityScope.DATA_MIGRATION.value, "DATA_MIGRATION")
        self.assertEqual(ModalityScope.UNKNOWN.value, "UNKNOWN")

    def test_confidence_tier_calculation(self):
        env_high = DomainClassificationEnvelope(domain="Thought Leadership", confidence=0.95)
        self.assertEqual(env_high.confidence_tier, ConfidenceTier.HIGH)

        env_med = DomainClassificationEnvelope(domain="Thought Leadership", confidence=0.75)
        self.assertEqual(env_med.confidence_tier, ConfidenceTier.MEDIUM)

        env_low = DomainClassificationEnvelope(domain="Thought Leadership", confidence=0.55)
        self.assertEqual(env_low.confidence_tier, ConfidenceTier.LOW)

    def test_envelope_serialization_and_fields(self):
        env = DomainClassificationEnvelope(
            domain="Thought Leadership",
            sub_domain="Market Intelligence & Utilities",
            modality=ModalityScope.DOCUMENT_WRITTEN,
            confidence=0.92,
            rationale="Weekly market update on energy utilities and nuclear PPAs",
            evidence_citations=["Executive Summary (S1, S2)", "FPSC 2026-2029 rate settlement"],
            secondary_domain="Decision Intelligence",
            ambiguity_margin=0.22,
            raw_scores={"Thought Leadership": 0.92, "Decision Intelligence": 0.70},
        )
        data = env.model_dump()
        self.assertEqual(data["domain"], "Thought Leadership")
        self.assertEqual(data["sub_domain"], "Market Intelligence & Utilities")
        self.assertEqual(data["modality"], "DOCUMENT_WRITTEN")
        self.assertEqual(data["confidence_tier"], "HIGH")
        self.assertEqual(len(data["evidence_citations"]), 2)
        self.assertEqual(data["classified_by"], "domain_intelligence_agent")

    def test_domain_archetype_model(self):
        arch = DomainArchetype(
            label="Thought Leadership",
            description="Executive market briefings and strategic updates",
            modality=ModalityScope.DOCUMENT_WRITTEN,
            name_keywords=["thought", "leadership", "market", "intel"],
            path_prefixes=["documents/market_intelligence/"],
            semantic_anchors=["Weekly energy and utilities trends update"],
        )
        self.assertEqual(arch.label, "Thought Leadership")
        self.assertEqual(arch.modality, ModalityScope.DOCUMENT_WRITTEN)
        self.assertIn("market", arch.name_keywords)

    def test_taxonomy_json_integrity(self):
        taxonomy_path = ROOT / "config" / "pr_domain_taxonomy.json"
        self.assertTrue(taxonomy_path.exists(), "pr_domain_taxonomy.json must exist")
        with open(taxonomy_path, "r", encoding="utf-8") as f:
            tax = json.load(f)

        self.assertEqual(tax.get("version"), "2.0.0")
        deliverable_spec = tax.get("deliverable", {})
        self.assertIn("modality_labels", deliverable_spec)
        self.assertEqual(
            deliverable_spec["modality_labels"].get("DOCUMENT_WRITTEN"),
            "Thought Leadership"
        )
        self.assertEqual(
            deliverable_spec["modality_labels"].get("MEETING_SPOKEN"),
            "Meeting Deliverable"
        )

        domain_labels = [d["label"] for d in tax.get("domains", [])]
        self.assertIn("Thought Leadership", domain_labels)
        self.assertIn("Decision Intelligence", domain_labels)
        self.assertIn("Live Meeting", domain_labels)
        self.assertIn("Architecture", domain_labels)

        # Validate that each domain entry can instantiate a DomainArchetype
        for d in tax.get("domains", []):
            modality_str = d.get("modality", "UNKNOWN")
            arch = DomainArchetype(
                label=d["label"],
                description=d.get("description", ""),
                modality=ModalityScope(modality_str) if modality_str in ModalityScope.__members__ else ModalityScope.UNKNOWN,
                name_keywords=d.get("name_keywords", []),
                path_prefixes=d.get("path_prefixes", []),
                semantic_anchors=d.get("semantic_anchors", []),
            )
            self.assertEqual(arch.label, d["label"])


if __name__ == "__main__":
    unittest.main()

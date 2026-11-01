"""Unit tests for Dense Semantic Domain Classifier."""
from __future__ import annotations

import unittest

from tools.pr_governance_core.domain_models import ModalityScope
from tools.pr_governance_core.semantic_domain import SemanticDomainClassifier


class TestSemanticDomainClassifier(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.classifier = SemanticDomainClassifier()

    def test_thought_leadership_market_intel_classification(self):
        title = "Weekly Energy & Utilities Market Trends Update - 10/04/2026"
        summary = (
            "Apex Power & Grid maintains base rate stability across the 2026-2029 regulatory cycle "
            "following FPSC confirmation. Hyperscale AI data centers contracting direct clean power nuclear PPAs. "
            "FERC Order 1920-B transmission planning mandates."
        )
        res = self.classifier.classify(
            title=title,
            summary=summary,
            topics=["thought_leadership", "market_intelligence"],
            modality=ModalityScope.DOCUMENT_WRITTEN,
        )
        self.assertEqual(res.domain, "External Research")
        self.assertGreaterEqual(res.confidence, 0.80)
        self.assertNotEqual(res.domain, "Live Meeting")
        self.assertIn("External Research", res.rationale)

    def test_live_meeting_classification(self):
        title = "Live Sprint Sync Dylan PartnerAdvisory"
        summary = "Live audio recording and speech to text transcription with speaker diarization."
        res = self.classifier.classify(
            title=title,
            summary=summary,
            topics=["live_meeting", "audio_transcript"],
            modality=ModalityScope.MEETING_SPOKEN,
        )
        self.assertEqual(res.domain, "Live Meeting")
        self.assertGreaterEqual(res.confidence, 0.80)

    def test_modality_constraint_prevents_false_meeting_match(self):
        # A written market update that happens to contain the token 'live' in title
        title = "live energy utilities market antigravity briefing"
        summary = "Weekly utilities market trends and regulatory rate dockets update."
        res = self.classifier.classify(
            title=title,
            summary=summary,
            topics=["market_intelligence"],
            modality=ModalityScope.DOCUMENT_WRITTEN,
        )
        # Even though title has 'live', modality DOCUMENT_WRITTEN strictly prevents 'Live Meeting'
        self.assertNotEqual(res.domain, "Live Meeting")
        self.assertEqual(res.domain, "External Research")

    def test_knowledge_graph_classification(self):
        title = "bge m3 fact vector ingestion completeness"
        summary = "Knowledge graph SQLite schema, dense vector sidecar, and hybrid retrieval qualification."
        res = self.classifier.classify(
            title=title,
            summary=summary,
            topics=["knowledge_graph"],
            modality=ModalityScope.CODE_PLATFORM,
        )
        self.assertEqual(res.domain, "Knowledge Graph")

    def test_ambiguity_margin_gating(self):
        # Create text that overlaps between two domains
        title = "Strategic decisionframe modeling of weekly market intelligence"
        summary = "Evaluating option trade-off frontier alongside market trends."
        res = self.classifier.classify(
            title=title,
            summary=summary,
            topics=["decision_intelligence", "external_research"],
            modality=ModalityScope.DOCUMENT_WRITTEN,
        )
        self.assertIn(res.domain, ("External Research", "Decision Intelligence"))
        if res.ambiguity_margin is not None:
            self.assertLess(res.ambiguity_margin, 0.15)
            self.assertIsNotNone(res.secondary_domain)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Hypothesis property tests for Wave 5 — Diarized Ingestion & Source Grounding.

Invariants tested:
1. Normalization LLM Bypass: Diarized transcripts route through deterministic normalization
   with exactly 0 provider LLM calls in Stage 01 and L1.
2. Synthetic Assertion Quarantine: Fabricated/synthetic candidate assertions whose evidence
   does not exist in the source transcript are dropped and quarantined.
3. 100% Source Span Grounding: Every promoted assertion in Output 03 cites evidence with
   an exact character span that resolves verbatim in the source transcript.
4. Turn Attribution Resolution: Generic or missing speakers are resolved from enclosing
   transcript turn headers when source text is provided.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings, strategies as st

from dual_pipeline.canonical_pipeline.pipeline import CanonicalAgenticPipeline, CanonicalModality
from dual_pipeline.signal_extraction import SignalExtractor
from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer
from dual_pipeline.transcript_stages_core.l0_routing import L0DeterministicRouter
from dual_pipeline.transcript_stages import L1PlanContract


class TestSourceGroundingProperties(unittest.TestCase):
    """Deterministic and property-based verification of source grounding invariants."""

    def test_diarized_transcript_normalization_zero_llm_calls(self) -> None:
        """Verify that already-diarized input makes 0 LLM calls in Stage 01 forensic normalizer."""
        diarized_transcript = (
            "### [00:00]\n\n"
            "**Drew Oakley:** Good afternoon everyone. Let us review the architecture roadmap.\n\n"
            "### [00:30]\n\n"
            "**Christina Harris:** We have completed the Terraform infrastructure review.\n\n"
            "### [01:00]\n\n"
            "**Jeremy Cianella:** PartnerAdvisory is ready to proceed with the pattern catalog deployment.\n"
        )
        metadata = {
            "project": "infrastructure_automation",
            "meeting_name": "Architecture Review",
            "date": "10/02/2026",
            "diarization_status": "NATIVE",
            "roster": [
                {"name": "Drew Oakley", "org": "PartnerAdvisory"},
                {"name": "Christina Harris", "org": "Apex Global Enterprises"},
                {"name": "Jeremy Cianella", "org": "PartnerAdvisory"},
            ],
        }
        u0_context = {
            "clean_transcript": diarized_transcript,
            "metadata": metadata,
            "diarization_status": "NATIVE",
        }
        plan = L1PlanContract(
            source_type="DIARIZED_TEXT",
            diarization_status="NATIVE",
            roster_gate_needed=False,
            meeting_type_hint="ARCHITECTURE_DEEP_DIVE",
            fusion_dag="FUSION_A",
            semantic_enhancers=[],
            model_binding={},
            lexical_quality_score=1.0,
            acoustic_overlap_ratio=0.0,
            classification_tier="internal",
            sanitization_passed=True,
            quarantine_reasons=[],
        )

        mock_provider = MagicMock()
        mock_provider.synthesize_forensic = MagicMock(side_effect=RuntimeError("LLM call must not be made!"))

        normalizer = ForensicNormalizer()
        output_1 = normalizer.execute(u0_context, plan=plan, provider=mock_provider)

        # Assert zero LLM provider calls were made
        self.assertEqual(mock_provider.synthesize_forensic.call_count, 0)
        self.assertIn("Drew Oakley", output_1)
        self.assertIn("Christina Harris", output_1)
        self.assertIn("Jeremy Cianella", output_1)

    def test_negative_quarantine_of_synthetic_assertions(self) -> None:
        """Verify that assertions citing synthetic quotes not in source text are 100% dropped."""
        forensic_transcript = (
            "### [00:00]\n\n"
            "**Drew Oakley:** Good afternoon everyone. We are deploying AWS infrastructure via Terraform.\n\n"
            "### [00:30]\n\n"
            "**Christina Harris:** We have approved the bucket isolation strategy.\n"
        )

        raw_projection = {
            "schema_version": "5.7",
            "meeting_id": "meet-test-grounding-001",
            "classification": "internal",
            "audiences": ["nee-engineering"],
            "evidence_index": {
                "ev-grounded-01": {
                    "statement_id": "ev-grounded-01",
                    "text": "We have approved the bucket isolation strategy.",
                    "exact_text": "We have approved the bucket isolation strategy.",
                    "locator": {"kind": "timestamp", "value": "00:30"},
                    "speaker": "Christina Harris",
                },
                "ev-synthetic-02": {
                    "statement_id": "ev-synthetic-02",
                    "text": "This completely fabricated assertion was never spoken in any meeting.",
                    "exact_text": "This completely fabricated assertion was never spoken in any meeting.",
                    "locator": {"kind": "timestamp", "value": "99:99"},
                    "speaker": "Fabricated Speaker",
                },
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "person:christina-harris",
                        "type": "PERSON",
                        "name": "Christina Harris",
                        "mutation": "CONFIRM",
                        "evidence_ids": ["ev-grounded-01"],
                    },
                    {
                        "id": "person:fake-person",
                        "type": "PERSON",
                        "name": "Fake Person",
                        "mutation": "CREATE",
                        "evidence_ids": ["ev-synthetic-02"],
                    },
                ],
                "relationship_signals": [
                    {
                        "signal_id": "sig-01",
                        "from_id": "person:christina-harris",
                        "to_id": "org:apex_global-energy",
                        "predicate": "MEMBER_OF",
                        "relationship": "MEMBER_OF",
                        "evidence_ids": ["ev-grounded-01"],
                    },
                    {
                        "signal_id": "sig-fake-02",
                        "from_id": "person:fake-person",
                        "to_id": "org:fake-org",
                        "predicate": "MEMBER_OF",
                        "relationship": "MEMBER_OF",
                        "evidence_ids": ["ev-synthetic-02"],
                    },
                ],
                "decision_updates": [
                    {
                        "decision_id": "decision:bucket-isolation",
                        "status": "APPROVED",
                        "rationale": "Approved bucket isolation strategy for state storage.",
                        "evidence_ids": ["ev-grounded-01"],
                    },
                    {
                        "decision_id": "decision:fake-decision",
                        "status": "APPROVED",
                        "rationale": "Synthetic decision with zero grounding in transcript.",
                        "evidence_ids": ["ev-synthetic-02"],
                    },
                ],
                "proposal_updates": [],
                "action_updates": [],
                "risk_updates": [],
            },
            "evidence_only_statement_ids": ["ev-grounded-01", "ev-synthetic-02"],
        }

        extractor = SignalExtractor()
        promoted, receipt = extractor.extract_and_gate(
            raw_projection,
            forensic_transcript=forensic_transcript,
        )

        # Assert synthetic items were dropped from promoted mutations
        promoted_entities = promoted["graph_mutations"]["entity_upserts"]
        promoted_rels = promoted["graph_mutations"]["relationship_signals"]
        promoted_decisions = promoted["graph_mutations"]["decision_updates"]

        self.assertEqual(len(promoted_entities), 1)
        self.assertEqual(promoted_entities[0]["id"], "person:christina-harris")

        self.assertEqual(len(promoted_rels), 1)
        self.assertEqual(promoted_rels[0]["signal_id"], "sig-01")

        self.assertEqual(len(promoted_decisions), 1)
        self.assertEqual(promoted_decisions[0]["decision_id"], "decision:bucket-isolation")

        # Verify receipt dropped counts
        self.assertGreaterEqual(receipt["dropped_ungrounded_assertions"], 3)
        self.assertEqual(receipt["grounding_ratio"], 0.5)

        # Verify that 100% of remaining promoted assertions cite grounded evidence
        for cat, items in promoted["graph_mutations"].items():
            for it in items:
                for eid in it.get("evidence_ids", []):
                    ev = promoted["evidence_index"][eid]
                    self.assertEqual(ev.get("grounding_status"), "GROUNDED")
                    self.assertIn(ev["exact_text"], forensic_transcript)

    def test_speaker_and_org_resolved_from_transcript_turn(self) -> None:
        """Verify that unattributed speakers in evidence statements resolve from enclosing turn header."""
        forensic_transcript = (
            "### [00:15]\n\n"
            "**Pedro G. Martinez:** We require separate Terraform state locking for security compliance.\n"
        )
        raw_projection = {
            "schema_version": "5.7",
            "meeting_id": "meet-test-attribution-001",
            "classification": "internal",
            "audiences": ["nee-engineering"],
            "evidence_index": {
                "ev-01": {
                    "statement_id": "ev-01",
                    "text": "We require separate Terraform state locking for security compliance.",
                    "exact_text": "We require separate Terraform state locking for security compliance.",
                    "locator": {"kind": "timestamp", "value": "00:15"},
                    "speaker": "unattributed",  # Unattributed speaker input
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "person:pedro-martinez",
                        "type": "PERSON",
                        "name": "Pedro G. Martinez",
                        "mutation": "CONFIRM",
                        "evidence_ids": ["ev-01"],
                    }
                ],
                "relationship_signals": [],
                "decision_updates": [],
                "proposal_updates": [],
                "action_updates": [],
                "risk_updates": [],
            },
            "evidence_only_statement_ids": ["ev-01"],
        }

        extractor = SignalExtractor()
        promoted, _ = extractor.extract_and_gate(
            raw_projection,
            forensic_transcript=forensic_transcript,
        )

        ev_resolved = promoted["evidence_index"]["ev-01"]
        self.assertEqual(ev_resolved["speaker"], "Pedro G. Martinez")
        self.assertEqual(ev_resolved["grounding_status"], "GROUNDED")
        self.assertIsNotNone(ev_resolved.get("start_offset"))

    @settings(max_examples=15, deadline=5000)
    @given(
        quote_text=st.text(alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789", min_size=25, max_size=100),
        spk_name=st.sampled_from(["Drew Oakley", "Christina Harris", "Jeremy Cianella", "Luis Arencibia"]),
    )
    def test_hypothesis_arbitrary_quote_source_grounding_invariant(self, quote_text: str, spk_name: str) -> None:
        """Property: 100% of assertions surviving extract_and_gate MUST resolve verbatim in source text."""
        transcript = f"### [00:10]\n\n**{spk_name}:** {quote_text}\n"

        # Projection contains one grounded item and one random synthetic item
        synthetic_quote = f"NOT_IN_TRANSCRIPT_{quote_text[::-1]}_FABRICATED"
        raw_proj = {
            "schema_version": "5.7",
            "meeting_id": "prop-test",
            "classification": "internal",
            "audiences": ["nee-engineering"],
            "evidence_index": {
                "ev-real": {"statement_id": "ev-real", "exact_text": quote_text, "text": quote_text, "speaker": spk_name},
                "ev-fake": {"statement_id": "ev-fake", "exact_text": synthetic_quote, "text": synthetic_quote, "speaker": "Fake"},
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "person:real", "type": "PERSON", "name": spk_name, "evidence_ids": ["ev-real"]},
                    {"id": "person:fake", "type": "PERSON", "name": "Fake", "evidence_ids": ["ev-fake"]},
                ],
                "relationship_signals": [],
                "decision_updates": [],
                "proposal_updates": [],
                "action_updates": [],
                "risk_updates": [],
            },
            "evidence_only_statement_ids": ["ev-real", "ev-fake"],
        }

        extractor = SignalExtractor()
        promoted, receipt = extractor.extract_and_gate(raw_proj, forensic_transcript=transcript)

        # Invariant 1: ev-fake assertion was dropped
        promoted_ids = [e["id"] for e in promoted["graph_mutations"]["entity_upserts"]]
        self.assertIn("person:real", promoted_ids)
        self.assertNotIn("person:fake", promoted_ids)

        # Invariant 2: Exactly 1 synthetic assertion dropped
        self.assertEqual(receipt["dropped_ungrounded_assertions"], 1)

        # Invariant 3: 100% of surviving assertions resolve verbatim
        for item in promoted["graph_mutations"]["entity_upserts"]:
            for eid in item["evidence_ids"]:
                ev = promoted["evidence_index"][eid]
                self.assertEqual(ev["grounding_status"], "GROUNDED")
                start = ev["start_offset"]
                end = ev["end_offset"]
                self.assertEqual(transcript[start:end], ev["exact_text"])


if __name__ == "__main__":
    unittest.main()

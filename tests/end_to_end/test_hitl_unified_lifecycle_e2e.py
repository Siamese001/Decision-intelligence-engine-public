#!/usr/bin/env python3
"""End-to-End Test Suite for Unified HITL Governance Lifecycle across Outputs 01–03.

Exercises the full pipeline lifecycle:
1. Stage 01: Shared room microphone disambiguation, ASR phonetic acronyms, and 180s silence gaps.
2. Stage 03: Multi-candidate relationship predicate and entity typing disambiguation.
3. Judge Stage: Interception and operator clarification of borderline judge deductions.
4. Storage: Persists decisions to Merkle ledger and updates Bayesian prior calibrations.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.llm_judge_core.judge_clarification import evaluate_judge_clarification
from dual_pipeline.normalization_core.meeting_entities import process_projection_signals
from dual_pipeline.speaker_disambiguation import resolve_shared_room_microphones
from dual_pipeline.transcript_stages_core.acronym_disambiguator import disambiguate_asr_acronyms
from dual_pipeline.transcript_stages_core.edge_trait_evaluator import (
    evaluate_edge_traits_and_disambiguate,
)
from dual_pipeline.transcript_stages_core.lexical_normalizer import format_ambiguity_lock_register
from dual_pipeline.transcript_stages_core.silence_gap_classifier import classify_silence_gaps


@pytest.mark.e2e
class HitlUnifiedLifecycleE2ETests(unittest.TestCase):
    """End-to-End test suite verifying complete closed-loop HITL lifecycle."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.temp_dir.name) / "hitl_decision_ledger.json"
        self.store = HITLDecisionStore(self.ledger_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_full_pipeline_hitl_lifecycle_e2e(self) -> None:
        """Full end-to-end verification of Output 01, Output 03, and Judge HITL resolutions."""
        # 1. STAGE 01: Raw transcript with room mic, ASR acronym, and silence gap
        raw_transcript = (
            "--- START OF TRANSCRIPT ---\n"
            "[00:01:10] **NEA/APEX (room):** We must verify the BGD work orders.\n"
            "[Recording gap: 00:02:00 – 00:06:00]\n"
            "[00:06:05] **NEA/APEX (room):** Welcome back to the architecture review.\n"
        )
        metadata = {
            "meeting_name": "Apex Global Grid Modernization Architecture",
            "date": "10/12/2026",
            "platform": "Teams Enterprise",
            "shared_room_mics": [
                {
                    "mic_label": "NEA/APEX (room)",
                    "display_label": "NEA/APEX (room)",
                    "in_room_attendees": [
                        {"name": "Jen Pan", "person_id": "person:jen-pan", "role": "Lead Architect", "domain_topics": ["work orders"]},
                        {"name": "harper Hoffman", "person_id": "person:mandy-hopmann", "role": "VP", "domain_topics": ["architecture"]},
                    ],
                }
            ],
            "mock_disambiguation": True,
            "hitl_speaker_resolver": lambda env: "Jen Pan",
            "hitl_acronym_resolver": lambda env: "PGD",
            "hitl_gap_resolver": lambda env: "NATURAL_SESSION_PAUSE",
        }
        speaker_map = {"Speaker 1": "Jen Pan", "Speaker 2": "harper Hoffman"}

        # Disambiguate room mic
        stage1_text, metadata = resolve_shared_room_microphones(raw_transcript, metadata, speaker_map)
        # Disambiguate acronyms
        stage1_text, metadata = disambiguate_asr_acronyms(stage1_text, metadata, store=self.store, interactive_resolver=metadata["hitl_acronym_resolver"])
        # Classify silence gap
        stage1_text, metadata = classify_silence_gaps(stage1_text, metadata, store=self.store, interactive_resolver=metadata["hitl_gap_resolver"])

        self.assertIn("PGD work orders", stage1_text)
        self.assertIn("[Session pause: 00:02:00 – 00:06:00]", stage1_text)

        alr_table = format_ambiguity_lock_register(metadata, speaker_map)
        self.assertIn("Phonetic ASR ambiguity: 'BGD' vs 'PGD'", alr_table)
        self.assertIn("Unannotated silence interval: [00:02:00 – 00:06:00]", alr_table)

        # 2. STAGE 03: Multi-candidate edge traits & entity typing
        raw_projection = {
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "concept:voc",
                        "name": "Voice of Customer",
                        "type": "concept",
                        "candidate_types": [
                            {"type": "role", "confidence": 0.53},
                            {"type": "system", "confidence": 0.47},
                        ],
                    },
                    {"id": "system:nexus", "name": "Nexus", "type": "system"},
                    {"id": "system:sap", "name": "SAP ERP", "type": "system"},
                ],
                "relationship_signals": [
                    {
                        "signal_id": "rel-nexus-sap",
                        "from_id": "system:nexus",
                        "to_id": "system:sap",
                        "relationship": "INTEGRATES_WITH",
                        "candidate_predicates": [
                            {"predicate": "INTEGRATES_WITH", "confidence": 0.54},
                            {"predicate": "DEPENDS_ON", "confidence": 0.46},
                        ],
                    }
                ],
            }
        }
        metadata["hitl_edge_resolver"] = lambda env: "INTEGRATES_WITH" if "predicate" in env.category else "role"

        stage3_proj, metadata = evaluate_edge_traits_and_disambiguate(
            raw_projection,
            metadata,
            store=self.store,
            interactive_resolver=metadata["hitl_edge_resolver"],
        )

        rel = stage3_proj["graph_mutations"]["relationship_signals"][0]
        self.assertEqual(rel["relationship"], "INTEGRATES_WITH")
        ent = stage3_proj["graph_mutations"]["entity_upserts"][0]
        self.assertEqual(ent["type"], "role")

        # Process candidates and edge traits
        candidates: list = []
        intent_profiles, edge_traits = process_projection_signals(
            projection=stage3_proj["graph_mutations"],
            candidates=candidates,
            evidence=[{"evidence_id": "ev-01", "locator": {"kind": "line", "value": "1"}}],
            evidence_by_identifier={},
            run_id="run_e2e_01",
            meeting_title=metadata["meeting_name"],
            classification="INTERNAL",
            default_audiences=["core_team"],
        )
        self.assertGreater(len(edge_traits), 0)

        # 3. LLM JUDGE STAGE: Borderline score clarification
        judge_report = {
            "output_1_evaluation": {"composite_score": 95, "verdict": "PASS"},
            "output_2_evaluation": {"composite_score": 92, "verdict": "PASS"},
            "output_3_evaluation": {
                "composite_score": 87,
                "verdict": "HITL_REQUIRED",
                "forensic_rationale": "Nuanced interpretive dispute on edge predicate specificity (-3 pts).",
            },
        }
        final_decision = {
            "status": "FAIL",
            "output_1_pass": True,
            "output_2_pass": True,
            "output_3_pass": False,
            "output_3_llm_score_pass": False,
            "authoritative_write_authorized": False,
        }
        metadata["hitl_judge_resolver"] = lambda env: "AFFIRM_VALID"

        upgraded = evaluate_judge_clarification(
            judge_report=judge_report,
            final_decision=final_decision,
            metadata=metadata,
            store=self.store,
            interactive_resolver=metadata["hitl_judge_resolver"],
        )

        self.assertTrue(upgraded)
        self.assertEqual(final_decision["status"], "PASS")
        self.assertTrue(final_decision["authoritative_write_authorized"])

        # 4. STORE & BAYESIAN CALIBRATION INTEGRITY
        export_file = Path(self.temp_dir.name) / "hitl_prior_calibration.json"
        exported = self.store.export_learned_priors(export_file)
        self.assertGreater(exported["total_records_analyzed"], 0)
        self.assertIn("priors_digest", exported)


if __name__ == "__main__":
    unittest.main()

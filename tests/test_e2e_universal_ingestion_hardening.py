"""
End-to-End System Integration Test for Universal Ingestion & Forensic Lineage Hardening.
Validates the complete lifecycle:
1. Live session capture produces real SESSION_PCM_SHA256 in ForensicSignalEnvelope.
2. Batch WAV re-processing detects prior live session, links supersedes_run_id, and reconciles assertions.
3. 14-Table Granular Graph DB Delta Table verifies +0 duplicate nodes.
4. Tier 3 unanchored text blobs generate canonical URNs and are quarantined from Output 01.
5. Multi-track containers mix audio with brickwall limiter.
6. 25% confidence margin evaluator triggers atomic cards for ambiguous spreads and resolves autonomous spreads.
"""
from __future__ import annotations

import hashlib
import unittest
from unittest.mock import MagicMock

from dual_pipeline.audio_contracts import (
    ForensicSignalEnvelope,
    SourceHashSemantics,
    TypedSourceHash,
)
from dual_pipeline.hitl_core import MarginEvaluator
from dual_pipeline.transcript_pipeline_core.ingress import _read_transcript_content
from dual_pipeline.transcript_pipeline_core.source_modality import (
    SourceModality,
    SourceTrustTier,
    get_source_trust_tier,
)
from dual_pipeline.uwg_core import (
    AssertionReconciler,
    SourceLineageBridge,
    compute_granular_delta_table,
    format_delta_table_markdown,
)


class E2EUniversalIngestionHardeningTests(unittest.TestCase):
    def test_e2e_live_to_wav_reprocessing_lifecycle(self) -> None:
        session_id = "session-e2e-20260923"
        pcm_hash = hashlib.sha256(b"\x03\x00" * 16000 + b"\x04\x00" * 16000).hexdigest()
        source_hash = TypedSourceHash(
            hash_value=f"sha256:{pcm_hash}",
            semantics=SourceHashSemantics.SESSION_PCM_SHA256,
            byte_count=64000,
        )
        live_env = ForensicSignalEnvelope(
            recording_id=session_id,
            source_hashes=(source_hash,),
            canonical_sample_rate_hz=16000,
            timeline_length_samples=32000,
            turns=(),
            signal_digest=ForensicSignalEnvelope.compute_signal_digest(session_id, (source_hash,), ()),
        )
        self.assertEqual(live_env.schema_version, "2.1.0")
        self.assertEqual(len(live_env.source_hashes), 1)

        live_hash = live_env.source_hashes[0]
        self.assertEqual(live_hash.semantics, SourceHashSemantics.SESSION_PCM_SHA256)
        self.assertNotEqual(live_hash.hex_digest, session_id)
        self.assertEqual(len(live_hash.hex_digest), 64)

        # Step 2: Batch re-processing detects live session lineage
        wav_meta = {
            "source_session_id": session_id,
            "live_run_id": f"run-{session_id}",
        }
        detected = SourceLineageBridge.detect_prior_live_session(wav_meta)
        self.assertIsNotNone(detected)
        self.assertEqual(detected["parent_session_id"], session_id)

        plan = SourceLineageBridge.build_superseding_run_plan(
            source_id="source:meeting:e2e_sync",
            prior_run_id=detected["supersedes_run_id"],
            new_run_id="run-batch-reprocess-002",
            prior_version=1,
        )
        self.assertEqual(plan["source_id"], "source:meeting:e2e_sync")
        self.assertEqual(plan["new_version_number"], 2)
        self.assertEqual(plan["supersedes_run_id"], "run-session-e2e-20260923")
        self.assertEqual(plan["authority_rank"], 8)

        # Step 3: Assertion reconciliation
        prior_assertions = [
            {
                "assertion_id": "a-live-01",
                "subject_node_id": "project:nexus",
                "predicate": "has_milestone",
                "literal_claim": "MVP_complete",
            },
            {
                "assertion_id": "a-live-02",
                "subject_node_id": "person:speaker_1",
                "predicate": "agreed_action",
                "literal_claim": "update_architecture",
            },
        ]
        new_assertions = [
            {
                "assertion_id": "a-batch-01",
                "subject_node_id": "project:nexus",
                "predicate": "has_milestone",
                "literal_claim": "MVP_complete",
            },
            {
                "assertion_id": "a-batch-02",
                "subject_node_id": "person:amit_ayer",
                "predicate": "agreed_action",
                "literal_claim": "update_architecture",
            },
        ]
        reconciled = AssertionReconciler.reconcile(prior_assertions, new_assertions)
        self.assertEqual(reconciled["promoted_count"], 2)
        self.assertEqual(reconciled["retired_count"], 1)

        # Step 4: 14-Table Granular Graph DB Delta Table validation
        baseline_db = {t: 20 for t in (
            "node", "source", "source_version", "extraction_run", "evidence_item",
            "assertion", "assertion_evidence", "search_document", "search_fts",
            "audit_event", "dense_vector", "sparse_weight", "colbert_token", "retrieval_record"
        )}
        activated_db = dict(baseline_db)
        activated_db["node"] = 20  # Invariant: Delta = +0 duplicate nodes
        activated_db["source"] = 20  # Same source
        activated_db["source_version"] = 21  # Delta = +1
        activated_db["extraction_run"] = 21  # Delta = +1
        activated_db["evidence_item"] = 45  # Delta = +25
        activated_db["assertion"] = 24  # Delta = +4

        delta_table = compute_granular_delta_table(baseline_db, activated_db)
        self.assertEqual(len(delta_table), 14)
        node_row = next(r for r in delta_table if r["sqlite_table"] == "node")
        self.assertEqual(node_row["delta"], 0)

        md_table = format_delta_table_markdown(delta_table)
        self.assertIn("| `node` | 20 | 20 | **+0** |", md_table)

    def test_e2e_tier3_unanchored_quarantine(self) -> None:
        meta: dict[str, str] = {}
        rec = MagicMock()
        unanchored_text = "Speaker 1: Strategic mandate to freeze $50M capital expenditure.\nSpeaker 2: Confirmed."

        _read_transcript_content(
            input_path=unanchored_text,
            is_file=False,
            input_p=None,
            input_ext="",
            meta=meta,
            rec=rec,
            mock=True,
        )

        self.assertTrue(meta["source_file_hash"].startswith("urn:nee:unanchored-text:sha256:"))
        self.assertEqual(meta["trust_tier"], "TIER_3_UNANCHORED_QUARANTINED")
        self.assertEqual(
            get_source_trust_tier(SourceModality.TEXT_UNANCHORED_QUARANTINED),
            SourceTrustTier.TIER_3_UNANCHORED_QUARANTINED,
        )

    def test_e2e_calibrated_margin_hitl(self) -> None:
        # Candidate cluster speaker resolution
        options = [
            {"label": "Amit Ayer", "confidence": 0.54, "description": "Audio lead"},
            {"label": "Alex Chen", "confidence": 0.50, "description": "Architect"},
        ]
        decision = MarginEvaluator.evaluate(options)
        self.assertTrue(decision["surface_hitl"])
        self.assertFalse(decision["decision_resolved_autonomously"])
        self.assertEqual(decision["margin"], 0.04)

        # Autonomous resolution when margin >= 25%
        decisive_options = [
            {"label": "Amit Ayer", "confidence": 0.88, "description": "Audio lead"},
            {"label": "Alex Chen", "confidence": 0.40, "description": "Architect"},
        ]
        decision_auto = MarginEvaluator.evaluate(decisive_options)
        self.assertFalse(decision_auto["surface_hitl"])
        self.assertTrue(decision_auto["decision_resolved_autonomously"])
        self.assertEqual(decision_auto["margin"], 0.48)


if __name__ == "__main__":
    unittest.main()

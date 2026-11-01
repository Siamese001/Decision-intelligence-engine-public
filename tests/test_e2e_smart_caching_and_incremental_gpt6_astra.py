"""tests/test_e2e_smart_caching_and_incremental_gpt6_astra.py

End-to-End Integration & Proof Test Suite for:
1. Intelligent zero-token caching on identical transcripts and audio files (Feature 1).
2. Incremental-only graph mutation reconciliation and Output 02 Executive Recap sync (Feature 2).
3. Canonical model binding and parameter immunity under gpt-6-luna.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from config.credentials import (
    canonicalize_model_name,
    resolve_openai_credentials,
    sanitize_model_call_kwargs,
)
from dual_pipeline.model_registry import (
    ModelTier,
    resolve_tier_model,
)
from dual_pipeline.transcript_pipeline_core.caching import (
    cache_audio_transcript,
    compute_source_file_hash,
    get_cached_audio_transcript,
)
from dual_pipeline.transcript_pipeline_core.pipeline import RawTranscriptPipeline

logger = logging.getLogger(__name__)


def test_gpt6_astra_model_resolution_and_parameter_immunity():
    """Verify gpt-6-luna model binding, aliases, and reasoning parameter immunity."""
    # 1. Alias canonicalization
    assert canonicalize_model_name("gpt-6-luna") == "gpt-6-luna"
    assert canonicalize_model_name("gpt-astra-6") == "gpt-6-luna"
    assert canonicalize_model_name("astra") == "gpt-6-luna"
    assert canonicalize_model_name("astra-6") == "gpt-6-luna"

    # 2. Credential resolution
    creds = resolve_openai_credentials(allow_mock=True, model="gpt-6-luna")
    assert creds.model == "gpt-6-luna"
    assert creds.is_reasoning_model is True

    # 3. Parameter immunity (strips temperature, top_p, penalties for reasoning models)
    raw_kwargs = {
        "temperature": 0.8,
        "top_p": 0.95,
        "presence_penalty": 0.5,
        "reasoning_effort": "high",
        "messages": [{"role": "user", "content": "Analyze system architecture"}],
    }
    sanitized = sanitize_model_call_kwargs("gpt-6-luna", raw_kwargs)
    assert "temperature" not in sanitized
    assert "top_p" not in sanitized
    assert "presence_penalty" not in sanitized
    assert sanitized["reasoning_effort"] == "high"
    assert "messages" in sanitized

    # 4. Capability tier resolution
    model_id, effort = resolve_tier_model(ModelTier.COPILOT_FAST_SNAP)
    assert model_id == "gpt-6-luna"


@pytest.fixture
def mock_pipeline_suite():
    """Provides a mocked execution environment tracking calls across pipeline runs."""
    call_log = {
        "l1_calls": 0,
        "judge_calls": 0,
    }

    with patch("dual_pipeline.transcript_stages_core.l1_cognition.L1ForensicCognition.plan") as mock_l1, \
         patch("dual_pipeline.transcript_stages_core.l2_executors.L2ForensicNormalizerExecutor.execute") as mock_l2_forensic, \
         patch("dual_pipeline.transcript_pipeline_core.post_l2_review.TranscriptL1PostExecutionReviewer.review_execution") as mock_rev, \
         patch("dual_pipeline.transcript_pipeline_core.pipeline.evaluate_judge_and_release") as mock_judge, \
         patch("dual_pipeline.transcript_stages_core.x_gates.XExitGatesEvaluator.execute") as mock_xgates, \
         patch("dual_pipeline.transcript_pipeline_core.release_runner.prepare_package") as mock_prepare:

        def l1_side_effect(*args, **kwargs):
            call_log["l1_calls"] += 1
            return MagicMock(plan_id="plan-e2e-1", source_type="mock", diarization_status="NATIVE")

        mock_l2_forensic.return_value = "# HITL-Resolved Transcript\n\nSpeaker 1: Architecture discussion."

        def judge_side_effect(*args, **kwargs):
            call_log["judge_calls"] += 1
            return (
                {"status": "PASS", "passed": True},
                {"status": "PASS"},
                {
                    "status": "PASS",
                    "authoritative_write_authorized": True,
                    "output_1_pass": True,
                    "output_2_pass": True,
                    "output_3_pass": True,
                    "reasoning": "Release validated.",
                },
            )

        def prepare_side_effect(*args, **kwargs):
            out_path = kwargs.get("output_path")
            if out_path:
                Path(out_path).write_text(json.dumps({"contract": "evidence-envelope-intake:1.0.0"}))
            return {}

        mock_l1.side_effect = l1_side_effect
        mock_judge.side_effect = judge_side_effect
        mock_prepare.side_effect = prepare_side_effect
        mock_xgates.return_value = True
        mock_rev.return_value = MagicMock()
        mock_rev.return_value.to_dict.return_value = {"approved": True, "verdict": "PASS", "observations": []}

        yield call_log


def test_e2e_smart_caching_and_incremental_graph_flow(tmp_path, mock_pipeline_suite):
    """
    Complete End-to-End Test Proving Features 1 and 2:
    1. Initial run under gpt-6-luna generates Outputs 01, 02, 03.
    2. Identical re-run detects matching hash and bypasses all token-spending stages (0 new LLM calls).
    3. Incremental update re-run augments Output 02 and produces reconciled graph (CONFIRM + CREATE).
    """
    output_dir = tmp_path / "project_nexus" / "09-16-2026"
    transcript_file = tmp_path / "transcript_v1.txt"
    transcript_file.write_text(
        "Dave: We are setting up Kafka cluster for the data ingestion layer.\n"
        "Elena: Sounds good, let's target Q4.",
        encoding="utf-8",
    )

    metadata = {
        "meeting_name": "Nexus Architecture Review",
        "date": "09-16-2026",
        "time": "02:00 PM",
        "project_name": "project_nexus",
        "roster": [
            {"name": "Dave", "role": "Data Engineer", "org": "PartnerAdvisory"},
            {"name": "Elena", "role": "Solutions Architect", "org": "PartnerAdvisory"},
        ],
        "attendees": {
            "PartnerAdvisory": ["Dave (Data Engineer)", "Elena (Solutions Architect)"],
        },
    }

    pipeline = RawTranscriptPipeline(provider_type="offline", model_name="gpt-6-luna")

    # Mock graph responses for run 1 and incremental run 2
    run1_graph = json.dumps({
        "schema_version": "5.7",
        "meeting_id": "nexus-e2e-01",
        "evidence_index": {
            "ev-1": {
                "evidence_id": "ev-1",
                "text": "We are setting up Kafka cluster for the data ingestion layer.",
                "exact_text": "We are setting up Kafka cluster for the data ingestion layer.",
                "locator": {"kind": "timestamp", "value": "00:00:00"},
            }
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:kafka_cluster", "type": "system", "name": "Kafka Cluster", "mutation": "CREATE", "evidence_ids": ["ev-1"]},
                {"id": "person:dave", "type": "person", "name": "Dave", "mutation": "CREATE", "evidence_ids": ["ev-1"]},
            ],
            "relationship_signals": [
                {"signal_id": "sig-1", "from_id": "person:dave", "to_id": "system:kafka_cluster", "relationship": "OWNS", "evidence_ids": ["ev-1"]},
            ],
            "decision_updates": [],
            "risk_updates": [],
            "proposal_updates": [],
            "action_updates": [],
        },
    })

    run2_graph = json.dumps({
        "schema_version": "5.7",
        "meeting_id": "nexus-e2e-01",
        "evidence_index": {
            "ev-1": {
                "evidence_id": "ev-1",
                "text": "We are setting up Kafka cluster for the data ingestion layer.",
                "exact_text": "We are setting up Kafka cluster for the data ingestion layer.",
                "locator": {"kind": "timestamp", "value": "00:00:00"},
            },
            "ev-2": {
                "evidence_id": "ev-2",
                "text": "We also need a Postgres DB for operational metadata.",
                "exact_text": "We also need a Postgres DB for operational metadata.",
                "locator": {"kind": "timestamp", "value": "00:01:00"},
            },
        },
        "graph_mutations": {
            "entity_upserts": [
                # Existing entities
                {"id": "system:kafka_cluster", "type": "system", "name": "Kafka Cluster", "mutation": "CREATE", "evidence_ids": ["ev-1"]},
                {"id": "person:dave", "type": "person", "name": "Dave", "mutation": "CREATE", "evidence_ids": ["ev-1"]},
                # Net-new entity
                {"id": "system:postgres_db", "type": "system", "name": "Postgres Database", "mutation": "CREATE", "evidence_ids": ["ev-2"]},
            ],
            "relationship_signals": [
                # Duplicate edge from run 1
                {"signal_id": "sig-1", "from_id": "person:dave", "to_id": "system:kafka_cluster", "relationship": "OWNS", "evidence_ids": ["ev-1"]},
                # Net-new relationship
                {"signal_id": "sig-2", "from_id": "system:kafka_cluster", "to_id": "system:postgres_db", "relationship": "INTEGRATES_WITH", "evidence_ids": ["ev-1", "ev-2"]},
            ],
            "decision_updates": [],
            "risk_updates": [],
            "proposal_updates": [],
            "action_updates": [],
        },
    })

    graph_call_count = [0]

    def mock_synthesize_graph(*args, **kwargs):
        graph_call_count[0] += 1
        return run1_graph if graph_call_count[0] == 1 else run2_graph

    with patch.object(pipeline.l2_graph.provider, "synthesize_graph", side_effect=mock_synthesize_graph):
        # =========================================================================
        # STEP 1: Initial Run
        # =========================================================================
        res1 = pipeline.run(
            input_path=str(transcript_file),
            metadata=metadata.copy(),
            output_dir=str(output_dir),
            max_judge_retries=0,
        )

        assert Path(res1["01_transcript"]).exists()
        assert Path(res1["02_recap"]).exists()
        assert Path(res1["03_graph"]).exists()
        assert Path(res1["final_release_decision"]).exists()

        assert mock_pipeline_suite["l1_calls"] == 1
        assert mock_pipeline_suite["judge_calls"] == 1

        decision_1 = json.loads(Path(res1["final_release_decision"]).read_text(encoding="utf-8"))
        assert decision_1["source_file_hash"] == compute_source_file_hash(transcript_file)
        assert decision_1["status"] == "PASS"

        recap_1 = Path(res1["02_recap"]).read_text(encoding="utf-8")
        assert "Finalize delivery charter" in recap_1
        assert "Implement incremental operational additions" not in recap_1

        graph_1 = json.loads(Path(res1["03_graph"]).read_text(encoding="utf-8"))
        nodes_1 = graph_1["graph_mutations"]["entity_upserts"]
        assert any(n["id"] == "system:kafka_cluster" for n in nodes_1)

        # =========================================================================
        # STEP 2: Identical Re-Run (Feature 1: Zero-Token Caching Bypass)
        # =========================================================================
        res2 = pipeline.run(
            input_path=str(transcript_file),
            metadata=metadata.copy(),
            output_dir=str(output_dir),
            max_judge_retries=0,
        )

        # Absolutely ZERO new calls to L1 or Judge
        assert mock_pipeline_suite["l1_calls"] == 1
        assert mock_pipeline_suite["judge_calls"] == 1

        # Exact same artifacts returned directly from cache
        assert res2["01_transcript"] == res1["01_transcript"]
        assert res2["02_recap"] == res1["02_recap"]
        assert res2["03_graph"] == res1["03_graph"]
        assert res2["final_release_decision"] == res1["final_release_decision"]

        # =========================================================================
        # STEP 3: Material Incremental Re-Run (Feature 2: Incremental Graph & Recap Sync)
        # =========================================================================
        transcript_v2_file = tmp_path / "transcript_v2.txt"
        transcript_v2_file.write_text(
            "Dave: We are setting up Kafka cluster for the data ingestion layer.\n"
            "Elena: Sounds good, let's target Q4.\n"
            "Elena: In addition, we decided to provision Postgres read replicas for reporting.\n"
            "Dave: I will own the Postgres provisioning by October 15th.",
            encoding="utf-8",
        )

        res3 = pipeline.run(
            input_path=str(transcript_v2_file),
            metadata=metadata.copy(),
            output_dir=str(output_dir),
            max_judge_retries=0,
        )

        # Incremental run triggered execution because source_file_hash changed
        assert mock_pipeline_suite["l1_calls"] == 2
        assert mock_pipeline_suite["judge_calls"] == 2

        # Verify Output 02 received both prior and incremental action items
        recap_3 = Path(res3["02_recap"]).read_text(encoding="utf-8")
        assert "Finalize delivery charter" in recap_3
        assert "Implement incremental operational additions" in recap_3

        # Verify Output 03 graph reconciliation:
        # 1. system:kafka_cluster and person:dave already existed in Run 1 -> CONFIRM
        # 2. system:postgres_db is net-new -> CREATE
        # 3. Duplicate edge sig-1 pruned, only net-new edge sig-2 retained!
        graph_3 = json.loads(Path(res3["03_graph"]).read_text(encoding="utf-8"))
        nodes_3 = graph_3["graph_mutations"]["entity_upserts"]

        ent_map = {n["id"]: n for n in nodes_3}
        assert ent_map["system:kafka_cluster"]["mutation"] == "CONFIRM"
        assert ent_map["person:dave"]["mutation"] == "CONFIRM"
        assert ent_map["system:postgres_db"]["mutation"] == "CREATE"

        # Check duplicate relationship pruning
        signals_3 = graph_3["graph_mutations"]["relationship_signals"]
        assert len(signals_3) == 1
        assert signals_3[0]["relationship"] == "INTEGRATES_WITH"

        # Verify reconciliation telemetry is preserved in Output 03
        recon_stats = graph_3.get("incremental_reconciliation")
        assert recon_stats is not None
        assert recon_stats["confirmed_entities"] == 2
        assert recon_stats["new_entities"] == 1
        assert recon_stats["pruned_duplicate_relationships"] == 1
        assert recon_stats["prior_projection_detected"] is True


def test_audio_content_addressed_caching_and_idempotency(tmp_path, mock_pipeline_suite):
    """
    Verify audio caching stops redundant acoustic transcription and execution.
    """
    output_dir = tmp_path / "project_audio" / "09-16-2026"
    audio_file = tmp_path / "standup_meeting.wav"
    audio_file.write_bytes(b"RIFFmockwavdata1234567890abcdef")

    metadata = {
        "meeting_name": "Audio Standup",
        "date": "09-16-2026",
        "time": "09:00 AM",
        "project_name": "project_audio",
        "speaker_map": {"Speaker 1": "Amit Ayer"},
        "roster": [
            {"name": "Amit Ayer", "role": "Lead Enterprise Architect", "org": "PartnerAdvisory"},
        ],
        "attendees": {
            "PartnerAdvisory": ["Amit Ayer (Lead Enterprise Architect)"],
        },
    }

    # Verify helper caching functions directly with precomputed hash and file path
    file_hash = compute_source_file_hash(audio_file)
    cached_path = cache_audio_transcript(file_hash, "Speaker 1: Audio transcript content from standup.")
    assert cached_path is not None
    assert cached_path.is_file()
    assert get_cached_audio_transcript(file_hash) == "Speaker 1: Audio transcript content from standup."

    pipeline = RawTranscriptPipeline(provider_type="offline", model_name="gpt-6-luna")

    # Run 1: with audio file
    res1 = pipeline.run(
        input_path=str(audio_file),
        metadata=metadata.copy(),
        output_dir=str(output_dir),
        max_judge_retries=0,
    )
    assert Path(res1["final_release_decision"]).exists()
    assert mock_pipeline_suite["l1_calls"] == 1

    # Run 2: with identical audio file
    res2 = pipeline.run(
        input_path=str(audio_file),
        metadata=metadata.copy(),
        output_dir=str(output_dir),
        max_judge_retries=0,
    )
    # Proves 0 new L1 or Judge calls
    assert mock_pipeline_suite["l1_calls"] == 1
    assert mock_pipeline_suite["judge_calls"] == 1
    assert res2["final_release_decision"] == res1["final_release_decision"]

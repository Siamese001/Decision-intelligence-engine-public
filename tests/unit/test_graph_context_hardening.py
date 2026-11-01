"""Unit tests for Wave 2: Hardened graph context population & fail-loud behavior."""

from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.transcript_pipeline_core.context_builder import load_prior_meeting_context
from dual_pipeline.transcript_stages_core.forensic_normalizer import ForensicNormalizer


def test_context_builder_sets_degraded_flag_on_retriever_failure(monkeypatch, tmp_path):
    class FailingRetriever:
        def retrieve_meeting_prior_context(self, *args, **kwargs):
            raise RuntimeError("Database disk I/O error or schema corruption")

    monkeypatch.setattr(
        "dual_pipeline.query_core.offline_retriever.OfflineGraphContextRetriever",
        FailingRetriever,
    )

    metadata = {"meeting_name": "Test Meeting"}
    out_dir = str(tmp_path / "meeting_out")
    Path(out_dir).mkdir(parents=True)

    load_prior_meeting_context(out_dir, metadata, transcript_text="Turn 1: Hello")

    assert metadata.get("graph_context_degraded") is True
    assert "Database disk I/O error" in metadata.get("graph_context_degradation_reason", "")


def test_forensic_normalizer_flags_ungrounded_when_entities_empty(monkeypatch):
    class MockProvider:
        def synthesize_forensic(self, prompt, user_content):
            assert "<project_resources>" not in user_content
            return "# Test Meeting -- HITL-Resolved Transcript\n**Participants:**\n- PartnerAdvisory: Amit\nTurn 1: Hi"

    normalizer = ForensicNormalizer()
    u0_ctx = {
        "clean_transcript": "Speaker 1: Hello world",
        "metadata": {
            "meeting_name": "Test Meeting",
            "date": "09/28/2026",
            "source_type": "AUDIO_ONLY_UNDIARIZED",
            "speaker_map": {"Speaker 1": "Amit Ayer"},
            "roster": [{"name": "Amit Ayer", "role": "Architect", "org": "PartnerAdvisory"}],
        },
    }
    plan = MagicMock()
    plan.diarization_status = "UNDIARIZED"

    normalizer.execute(u0_ctx, plan, MockProvider())
    assert u0_ctx["metadata"].get("graph_grounding_status") == "UNGROUNDED"


def test_forensic_normalizer_flags_grounded_when_entities_present(monkeypatch):
    class MockProvider:
        def synthesize_forensic(self, prompt, user_content):
            assert "<project_resources>" in user_content
            assert "Chief Architecture Officer" in user_content
            return "# Test Meeting -- HITL-Resolved Transcript\n**Participants:**\n- PartnerAdvisory: Amit\nTurn 1: Hi"

    normalizer = ForensicNormalizer()
    u0_ctx = {
        "clean_transcript": "Speaker 1: Hello world",
        "metadata": {
            "meeting_name": "Test Meeting",
            "date": "09/28/2026",
            "source_type": "AUDIO_ONLY_UNDIARIZED",
            "speaker_map": {"Speaker 1": "Amit Ayer"},
            "roster": [{"name": "Amit Ayer", "role": "Architect", "org": "PartnerAdvisory"}],
            "known_graph_entities": [
                {
                    "node_id": "person:amit_ayer",
                    "canonical_name": "Amit Ayer",
                    "display_name": "Amit Ayer",
                    "node_type": "person",
                    "organization": "PartnerAdvisory",
                    "role": "Chief Architecture Officer",
                }
            ],
        },
    }
    plan = MagicMock()
    plan.diarization_status = "UNDIARIZED"

    normalizer.execute(u0_ctx, plan, MockProvider())
    assert u0_ctx["metadata"].get("graph_grounding_status") == "GROUNDED"

"""
Tests for Tiered Governed Ingestion Context Envelope (T-GICE)
Validates C0ContextEnvelopeGate, security scanning, hypothesis firewall,
and lexicon mapping propagation.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dual_pipeline.transcript_pipeline import RawTranscriptPipeline
from dual_pipeline.transcript_stages import (
    C0ContextEnvelopeGate,
    C0RosterContextGate,
    HITLRequiredError,
    L1ForensicCognition,
    L2ExecutiveRecapExecutor,
    OfflineTranscriptSynthesizer,
)


class TestC0ContextEnvelopeGate:
    """Test suite for T-GICE C0 gate logic."""

    def test_gate_alias_identity(self):
        """Confirm C0ContextEnvelopeGate is an alias to C0RosterContextGate."""
        assert C0ContextEnvelopeGate is C0RosterContextGate

    def test_tgice_unpacking_roster_and_lexicon(self):
        """Verify ingestion_context unpacks into metadata fields and clears gate."""
        gate = C0ContextEnvelopeGate()
        ingestion_ctx = {
            "date": "09/09/2026",
            "time": "04:45 PM - 05:15 PM ET",
            "meeting_name": "Sherpa_Demo_Test",
            "attendee_roster": [
                {"name": "logan Miller", "org": "Apex Global Enterprises", "role": "CAIDO"},
                {"name": "Amit Ayer", "org": "PartnerAdvisory", "role": "Lead EA"},
            ],
            "domain_lexicon": {
                "adjudication": "A2A",
                "Kevin": "Devin Cloud",
            },
            "collateral_context": "Sprint 49.4 demo slides",
            "operator_hypothesis": "Josh is skeptical of A2A architecture",
        }
        l0_context = {"metadata": {"ingestion_context": ingestion_ctx}}

        result = gate.execute(l0_context)

        assert result["roster_gate_cleared"] is True
        assert result["context_envelope_cleared"] is True
        meta = result["metadata"]
        assert len(meta["roster"]) == 2
        assert meta["lexicon_map"]["adjudication"] == "A2A"
        assert meta["collateral_context"] == "Sprint 49.4 demo slides"
        assert meta["operator_hypothesis"] == "Josh is skeptical of A2A architecture"
        assert meta["date"] == "09/09/2026"

    def test_tgice_missing_roster_raises_hitl(self):
        """Missing roster in both metadata and ingestion_context raises HITLRequiredError."""
        gate = C0ContextEnvelopeGate()
        l0_context = {"metadata": {"ingestion_context": {"meeting_name": "Test"}}}

        with pytest.raises(HITLRequiredError) as exc_info:
            gate.execute(l0_context)

        assert exc_info.value.hitl_type == "roster_gate"
        assert "roster" in exc_info.value.missing_fields

    def test_tgice_security_restricted_key_raises(self):
        """Restricted credentials in ingestion_context trigger fail-closed ValueError."""
        gate = C0ContextEnvelopeGate()
        l0_context = {
            "metadata": {
                "ingestion_context": {
                    "attendee_roster": ["Alice", "Bob"],
                    "collateral_context": "Pasted AWS key AKIA_REDACTED_EXAMPLE for demo",
                }
            }
        }
        with pytest.raises(ValueError) as exc_info:
            gate.execute(l0_context)
        assert "Restricted content pattern detected" in str(exc_info.value)

    def test_tgice_security_confidential_ssn_elevates(self):
        """Confidential markers (SSN) elevate classification to confidential."""
        gate = C0ContextEnvelopeGate()
        l0_context = {
            "metadata": {
                "classification": "internal",
                "ingestion_context": {
                    "attendee_roster": ["Alice", "Bob"],
                    "notes": "Test case with SSN 000-12-3456",
                },
            }
        }
        result = gate.execute(l0_context)
        assert result["metadata"]["classification"] == "confidential"


class TestEpistemicHypothesisFirewall:
    """Test suite ensuring operator hypothesis is quarantined from Stage 01 and delivered to Stage 02."""

    def test_firewall_quarantines_hypothesis_from_stage01(self):
        """Stage 01 prompt user_content must NOT contain operator_hypothesis or executive_tensions."""
        l1 = L1ForensicCognition()
        mock_provider = MagicMock(spec=OfflineTranscriptSynthesizer)
        mock_provider.synthesize_forensic.return_value = "Attributed transcript"

        metadata = {
            "meeting_name": "Test Meeting",
            "roster": ["Alice", "Bob"],
            "domain_lexicon": {"adjudication": "A2A"},
            "operator_hypothesis": "SECRET_OPINION_JOSH_IS_SKEPTICAL",
            "executive_tensions": "SECRET_TENSION_DECOUPLING_URGENT",
        }

        l1._execute_undiarized("raw text", metadata, mock_provider)

        # Check call arguments to synthesize_forensic
        mock_provider.synthesize_forensic.assert_called_once()
        user_content = mock_provider.synthesize_forensic.call_args[0][1]

        # Assert hypothesis and tensions were stripped from Stage 01 user content
        assert "SECRET_OPINION_JOSH_IS_SKEPTICAL" not in user_content
        assert "SECRET_TENSION_DECOUPLING_URGENT" not in user_content
        # Assert lexicon was passed
        assert "<domain_lexicon>" in user_content
        assert "adjudication" in user_content

    def test_stage02_receives_operator_prior_hypothesis(self):
        """Stage 02 recap executor MUST include <operator_prior_hypothesis> when present."""
        mock_provider = MagicMock(spec=OfflineTranscriptSynthesizer)
        mock_provider.synthesize_recap.return_value = "Recap content"
        executor = L2ExecutiveRecapExecutor(mock_provider)

        pa_context = {
            "metadata": {
                "meeting_name": "Test",
                "operator_hypothesis": "Josh demands empirical benchmark before A2A approval",
            },
            "_stage_prompts": {"recap": "Recap system prompt"},
        }

        executor.execute("forensic transcript text", pa_context)

        mock_provider.synthesize_recap.assert_called_once()
        user_content = mock_provider.synthesize_recap.call_args[0][1]

        assert "<operator_prior_hypothesis>" in user_content
        assert "Josh demands empirical benchmark before A2A approval" in user_content


class TestPipelineEndToEndTGICE:
    """Test full pipeline run with ingestion_context parameter."""

    def test_pipeline_run_with_ingestion_context(self, tmp_path):
        """Pipeline accepts ingestion_context and executes offline without errors."""
        pipeline = RawTranscriptPipeline(provider_type="offline")
        input_file = tmp_path / "raw_memo.txt"
        input_file.write_text("Hello everyone. Today we discuss Sherpa and OpenSearch.")

        ingestion_ctx = {
            "meeting_name": "Sherpa_Demo_Test",
            "date": "09/09/2026",
            "time": "04:45 PM - 05:15 PM ET",
            "attendee_roster": [
                {"name": "logan Miller", "org": "Apex Global", "role": "CAIDO"},
                {"name": "Marcus Balk", "org": "Apex Global", "role": "EA"},
            ],
            "domain_lexicon": {"OpenSearch": "OpenSearch"},
            "operator_hypothesis": "Decoupling is priority 1",
        }

        out_dir = str(tmp_path / "output")
        result = pipeline.run(
            input_path=str(input_file),
            metadata={"platform": "Voice Memo"},
            output_dir=out_dir,
            ingestion_context=ingestion_ctx,
        )

        assert "01_transcript" in result
        assert "02_recap" in result
        assert "03_graph" in result
        assert Path(result["01_transcript"]).exists()
        assert Path(result["02_recap"]).exists()
        assert Path(result["03_graph"]).exists()

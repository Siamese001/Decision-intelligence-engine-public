"""
Tests for unshackled frontier reasoning effort, extended prompt limits, and timeouts.
"""

from unittest.mock import MagicMock, patch

from config.credentials import OpenAICredentials
from dual_pipeline.attachment_core.prompt_builder import _format_content_details
from dual_pipeline.attachment_core.types import ExtractedAttachmentContent
from dual_pipeline.prompts.config import CANONICAL_PROMPT_CONFIG


def test_attachment_max_chars_extended_to_60k():
    """Verify that attachment OCR / extracted content is not truncated at 12k chars."""
    large_text = "A" * 30000
    content = ExtractedAttachmentContent(
        attachment_id="att_1",
        filename="diagram.png",
        attachment_type="diagram",
        sha256="dummy_sha",
        extracted_text=large_text,
    )
    details = _format_content_details(content)
    combined = "\n".join(details)
    assert "... [truncated]" not in combined
    assert len(combined) >= 30000

    # Verify truncation only occurs beyond 60k chars
    huge_text = "B" * 65000
    huge_content = ExtractedAttachmentContent(
        attachment_id="att_2",
        filename="specs.pdf",
        attachment_type="document",
        sha256="dummy_sha2",
        extracted_text=huge_text,
    )
    huge_details = _format_content_details(huge_content)
    huge_combined = "\n".join(huge_details)
    assert "... [truncated]" in huge_combined


def test_judge_config_deadline_is_600s():
    """Verify JudgeExecutionConfig deadline_seconds is 600.0s for frontier reasoning."""
    assert CANONICAL_PROMPT_CONFIG.judge.deadline_seconds == 600.0


def test_synthesizer_preserves_frontier_high_reasoning():
    """Verify LLMTranscriptSynthesizer does not force frontier high reasoning to low reasoning effort."""
    from dual_pipeline.transcript_stages_core.synthesizers import LLMTranscriptSynthesizer

    mock_creds = OpenAICredentials(
        api_key="sk-mock",
        model="gpt-6-luna",
        reasoning_effort="high",
        is_mock=False,
        masked_key="sk-mock",
        source="test",
    )
    synth = LLMTranscriptSynthesizer(
        model="gpt-6-luna",
        reasoning_effort="high",
        _client=MagicMock(),
        _creds=mock_creds,
    )
    assert synth.reasoning_effort == "high"


def test_synthesizer_defaults_frontier_to_high_when_unspecified():
    """Verify LLMTranscriptSynthesizer automatically defaults frontier high model to high reasoning effort."""
    mock_creds = OpenAICredentials(
        api_key="sk-mock",
        model="gpt-6-luna",
        reasoning_effort="high",
        is_mock=False,
        masked_key="sk-mock",
        source="test",
    )
    with patch("dual_pipeline.credentials.get_openai_client", return_value=(MagicMock(), mock_creds)) as mock_get_client:
        from dual_pipeline.transcript_stages_core.synthesizers import LLMTranscriptSynthesizer

        synth = LLMTranscriptSynthesizer(model="gpt-6-luna")
        # Ensure get_openai_client was requested with eff_effort="high"
        mock_get_client.assert_called_once_with(
            allow_mock=False,
            model="gpt-6-luna",
            reasoning_effort="high",
        )
        assert synth.reasoning_effort == "high"


def test_raw_transcript_pipeline_preserves_frontier_high_reasoning():
    """Verify RawTranscriptPipeline does not clamp frontier high reasoning effort to medium."""
    from dual_pipeline.transcript_pipeline_core.pipeline import RawTranscriptPipeline

    pipeline = RawTranscriptPipeline(
        provider_type="offline",
        model_name="gpt-6-luna",
        reasoning_effort="high",
    )
    assert pipeline.reasoning_effort == "high"


def test_document_pipeline_preserves_frontier_high_reasoning():
    """Verify DocumentPipeline does not clamp frontier high reasoning effort to medium."""
    from dual_pipeline.document_pipeline_core.pipeline import DocumentPipeline

    doc_pipeline = DocumentPipeline(
        provider_type="offline",
        model_name="gpt-6-luna",
        reasoning_effort="high",
    )
    assert doc_pipeline.reasoning_effort == "high"


def test_document_evaluator_does_not_force_frontier_to_low():
    """Verify _execute_document_judge does not degrade frontier model to low reasoning effort."""
    from dual_pipeline.document_pipeline_core.evaluator import _execute_document_judge

    with patch("dual_pipeline.document_pipeline_core.evaluator.GraphExtractionJudge") as mock_judge_cls:
        mock_instance = MagicMock()
        mock_instance.evaluate.return_value = {"composite_score": 95, "verdict": "PASS"}
        mock_judge_cls.return_value = mock_instance

        _execute_document_judge(
            eff_model="gpt-6-luna",
            eff_effort="high",
            reasoning_effort=None,
            fast_judge=True,
            f3_str="{}",
            source_text="Sample text",
            mock=True,
        )

        mock_judge_cls.assert_called_once_with(
            model="gpt-6-luna",
            reasoning_effort="high",
            mock=True,
        )


def test_credentials_timeout_for_high_reasoning_is_1200s():
    """Verify get_openai_client configures 1200.0s timeout for high reasoning models."""
    with patch("config.credentials.resolve_openai_credentials") as mock_res:
        mock_res.return_value = OpenAICredentials(
            api_key="sk-mock",
            model="gpt-6-luna",
            reasoning_effort="high",
            is_mock=False,
            masked_key="sk-mock",
            source="test",
        )
        with (
            patch("config.transport.build_resilient_http_client") as mock_http,
            patch("openai.OpenAI"),
        ):
            from config.credentials import get_openai_client

            client, creds = get_openai_client(
                allow_mock=False,
                model="gpt-6-luna",
                reasoning_effort="high",
            )
            mock_http.assert_called_once()
            _, kwargs = mock_http.call_args
            assert kwargs.get("timeout") == 1200.0

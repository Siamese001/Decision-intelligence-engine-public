from unittest.mock import patch

from dual_pipeline.transcript_pipeline import RawTranscriptPipeline


@patch("dual_pipeline.transcript_pipeline.RunRecorder")
def test_augmentation_mode_unfreezes_and_injects_prior_context(mock_recorder, tmp_path):
    pipeline = RawTranscriptPipeline(provider_type="offline")
    metadata = {
        "meeting_name": "Augmentation Test Sync",
        "date": "09/01/2026",
        "time": "14:00",
        "classification": "internal",
        "augmentation_mode": True,
    }

    out_dir = tmp_path / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Pre-seed frozen artifacts in the output directory
    dec_file = out_dir / "final_release_decision.json"
    dec_file.write_text('{"status": "PASS"}', encoding="utf-8")

    f1 = out_dir / "01_Augmentation_Test_Sync_09012026_1400_hitl_resolved_transcript.md"
    f1.write_text("Existing 01 Transcript", encoding="utf-8")
    f2 = out_dir / "02_Augmentation_Test_Sync_09012026_1400_executive_recap.md"
    f2.write_text("Existing 02 Recap", encoding="utf-8")
    f3 = out_dir / "03_Augmentation_Test_Sync_09012026_1400_canonical_signal_projection.json"
    f3.write_text('{"schema_version": "5.7"}', encoding="utf-8")

    # Mock input file
    mock_input_file = tmp_path / "mock_transcript.txt"
    mock_input_file.write_text("**Amit Ayer:** New supplemental information.", encoding="utf-8")

    captured_pa_context = {}

    orig_recap_execute = pipeline.l2_recap.execute
    def mock_recap_execute(forensic_transcript, pa_context=None, remediation_directive=None):
        nonlocal captured_pa_context
        captured_pa_context = pa_context
        return orig_recap_execute(forensic_transcript, pa_context=pa_context, remediation_directive=remediation_directive)

    with patch.object(pipeline.l2_recap, "execute", side_effect=mock_recap_execute):
        # Run the pipeline
        pipeline.run(str(mock_input_file), metadata, str(out_dir))

    # Verify that the prior_context was successfully loaded into pa_context
    assert captured_pa_context is not None
    assert "prior_context" in captured_pa_context["metadata"]
    assert "PRIOR OUTPUT 01" in captured_pa_context["metadata"]["prior_context"]
    assert "Existing 02 Recap" in captured_pa_context["metadata"]["prior_context"]
    assert '{"schema_version": "5.7"}' in captured_pa_context["metadata"]["prior_context"]

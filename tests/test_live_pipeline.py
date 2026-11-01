import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from dotenv import load_dotenv

from dual_pipeline.transcript_pipeline import RawTranscriptPipeline

# Load env_di variables
load_dotenv(Path(__file__).resolve().parents[1] / "env_di")

@pytest.mark.skipif(
    os.environ.get("LIVE_EVAL") != "1" or not os.environ.get("OPENAI_API_KEY"),
    reason="Live evaluation requires LIVE_EVAL=1 and OPENAI_API_KEY in environment",
)
@patch("dual_pipeline.transcript_pipeline.RunRecorder")
def test_live_pipeline_e2e(mock_recorder, tmp_path):
    """
    Live E2E test using the LLM provider.
    Requires OPENAI_API_KEY to be set in the .env file.
    """
    metadata = {
        "meeting_name": "Live E2E Test Sync",
        "date": "09/01/2026",
        "time": "10:00",
        "classification": "internal"
    }

    # Create a realistic small mock transcript
    mock_input_file = tmp_path / "mock_transcript.txt"
    transcript_text = """**Speaker A** [00:00:00]: Welcome everyone to the architecture review.
**Speaker B** [00:00:10]: Thanks. I reviewed the document. I propose we use PostgreSQL instead of MySQL for the new microservice.
**Speaker A** [00:00:25]: That sounds reasonable. Let's make that a formal decision. We will use PostgreSQL.
**Speaker B** [00:00:35]: Great. I will update the architecture diagram by next Friday.
**Speaker A** [00:00:45]: Perfect. Are there any blockers?
**Speaker B** [00:00:50]: None on my end.
"""
    mock_input_file.write_text(transcript_text, encoding="utf-8")

    out_dir = tmp_path / "live_artifacts"

    # Run the pipeline using the live LLM provider
    pipeline = RawTranscriptPipeline(provider_type="llm")
    result = pipeline.run(str(mock_input_file), metadata, str(out_dir))

    assert "01_transcript" in result
    assert "02_recap" in result
    assert "03_graph" in result

    # Validate the generated files exist
    assert Path(result["01_transcript"]).exists()
    assert Path(result["02_recap"]).exists()
    assert Path(result["03_graph"]).exists()

    # Basic sanity checks on the LLM output
    graph_content = Path(result["03_graph"]).read_text(encoding="utf-8")
    graph_data = json.loads(graph_content)

    assert graph_data["schema_version"] == "5.7"
    assert len(graph_data["graph_mutations"]["decision_updates"]) > 0 or len(graph_data["graph_mutations"]["proposal_updates"]) > 0
    assert len(graph_data["graph_mutations"]["action_updates"]) > 0

    recap_content = Path(result["02_recap"]).read_text(encoding="utf-8")
    assert "Meeting Objective" in recap_content
    assert "Next Steps & Action Items" in recap_content
